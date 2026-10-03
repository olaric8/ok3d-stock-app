"""
sheets_engine.py
================

Google Sheets data engine for the **OK3D Stock App** (built by LemonLogic).

This module owns every read and write against the spreadsheet. It contains no UI
code and does not import Streamlit, so it can be exercised headlessly by
``verify_engine.py`` or driven from a CLI/cron job.

Architecture
------------

                    StockBackend          <- every business rule lives here
                    (checkout guardrail, reorder status, auto-SKU, ledger writes)
                          ^
              +-----------+-----------+
              |                       |
        SheetsEngine              DemoBackend          <- persistence only
              |                       |
      GoogleSheetsStore          DemoStore

The rules exist exactly once, in :class:`StockBackend`, so the offline demo can
never behave differently from the real spreadsheet path.

SAFETY BOUNDARY
---------------
OK3D's live trading sheet must never be touched by this prototype. The engine
therefore refuses to write unless it can prove it is talking to an isolated
**Shadow Copy**:

1. A spreadsheet id must be supplied explicitly (env var or argument). There is
   deliberately **no default id**, so the app cannot guess its way into
   production.
2. The workbook title must contain ``SHADOW`` (or the acknowledgment override
   must be set deliberately).
3. Both required tabs must exist with the exact agreed headers, or connect()
   raises instead of writing into a mis-shaped sheet.

Sheet schema (strict -- never reorder, never rename)
----------------------------------------------------

``Current Stock``

    A  SKU                 -- auto-handled by this module; staff never type it
    B  Product name        -- the identifier staff actually use
    C  Opening balance
    D  Minimum stock
    E  Total receipts
    F  Total sales/issue
    G  Current stock       -- calculated: opening + receipts - sales
    H  Reorder status      -- ``REORDER`` when current <= minimum, else ``OK``

``Sales Ledger``

    A  Timestamp   B  Transaction ID   C  Product name
    D  Quantity Sold   E  Customer Name   F  Handled By

Design note: ``Current stock`` is written as a literal number rather than a
spreadsheet formula, so the value is readable by anything (gspread, other tabs,
exports) with no recalculation round-trip. It is only ever derived from columns
C-F, so it stays consistent with them.
"""

from __future__ import annotations

import os
import random
import re
import string
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

STOCK_TAB = "Current Stock"
LEDGER_TAB = "Sales Ledger"

STOCK_HEADERS: list[str] = [
    "SKU",
    "Product name",
    "Opening balance",
    "Minimum stock",
    "Total receipts",
    "Total sales/issue",
    "Current stock",
    "Reorder status",
]

LEDGER_HEADERS: list[str] = [
    "Timestamp",
    "Transaction ID",
    "Product name",
    "Quantity Sold",
    "Customer Name",
    "Handled By",
]

#: 1-based column numbers, derived from the header list so they cannot drift.
COL_SKU = STOCK_HEADERS.index("SKU") + 1
COL_PRODUCT = STOCK_HEADERS.index("Product name") + 1
COL_OPENING = STOCK_HEADERS.index("Opening balance") + 1
COL_MINIMUM = STOCK_HEADERS.index("Minimum stock") + 1
COL_RECEIPTS = STOCK_HEADERS.index("Total receipts") + 1
COL_SALES = STOCK_HEADERS.index("Total sales/issue") + 1
COL_CURRENT = STOCK_HEADERS.index("Current stock") + 1
COL_REORDER = STOCK_HEADERS.index("Reorder status") + 1

REORDER_FLAG = "REORDER"
OK_FLAG = "OK"

#: Row 1 is the header row; data starts here.
FIRST_DATA_ROW = 2

#: Prefix for SKUs this module generates (e.g. ``OK3D-0001``).
DEFAULT_SKU_PREFIX = "OK3D-"

CREDENTIALS_FILE = Path(__file__).resolve().parent / "credentials.json"
SPREADSHEET_ID_ENV = "OK3D_SPREADSHEET_ID"
CREDENTIALS_ENV = "OK3D_GOOGLE_CREDENTIALS"
ACK_ENV = "OK3D_ACKNOWLEDGE_SHADOW"
SHADOW_MARKER = "SHADOW"

# NOTE ON THE TARGET WORKBOOK
# ---------------------------
# Deliberately NO default spreadsheet id is compiled in. A silent fallback
# target is how an app ends up writing into the wrong workbook -- and pointing
# at a live trading sheet is the exact outcome this prototype exists to avoid.
# The id is therefore supplied explicitly, from either:
#
#   * the OK3D_SPREADSHEET_ID environment variable (local runs), or
#   * a ``spreadsheet_id`` entry in Streamlit secrets (cloud deploys).
#
# On Streamlit Community Cloud neither needs a code change: secrets are pasted
# into Settings -> Secrets, so the deployment stays configurable without
# committing a target into version control.


def _st_secrets() -> dict[str, Any]:
    """
    Streamlit Cloud secrets, or an empty dict when unavailable.

    Returning {} for every failure mode (not on Streamlit, no secrets.toml,
    malformed file) means every caller keeps working locally with env vars and
    files -- the cloud path is purely additive.
    """
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if secrets is None:
            return {}
        return {key: secrets[key] for key in secrets}
    except Exception:  # noqa: BLE001
        return {}


def _materialise_secret_credentials() -> Path | None:
    """
    Write service-account JSON held in st.secrets to a private temp file.

    google-auth's ``from_service_account_file`` needs a real path, so cloud
    deployments stash the key in secrets and this turns it into one. The file is
    created with owner-only permissions and removed when the process exits.
    """
    secrets = _st_secrets()
    if not secrets:
        return None

    payload = secrets.get("gcp_service_account") or secrets.get("credentials")

    # Preferred, wrapper-proof route: the whole service-account JSON base64
    # encoded. Base64 is a single unbroken line, so a rich-text paste path
    # cannot reflow it -- which is what breaks the PEM key in the TOML table
    # form. Both routes end up as the same dict.
    if payload is None:
        encoded = secrets.get("gcp_service_account_b64")
        if isinstance(encoded, str) and encoded.strip():
            import base64
            import json

            cleaned = "".join(encoded.split())  # tolerate stray whitespace
            for decoder in (base64.b64decode, base64.urlsafe_b64decode):
                try:
                    payload = json.loads(decoder(cleaned).decode("utf-8"))
                    if isinstance(payload, dict):
                        break
                    payload = None
                except Exception:  # noqa: BLE001
                    payload = None
            if payload is None:
                return None

    if payload is None:
        return None
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except Exception:  # noqa: BLE001
            return None
    if not isinstance(payload, dict):
        return None

    import atexit
    import json
    import os
    import tempfile

    handle, name = tempfile.mkstemp(prefix="ok3d-credentials-", suffix=".json")
    target = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        os.chmod(target, 0o600)
    except Exception:  # noqa: BLE001
        return None
    atexit.register(lambda: target.unlink(missing_ok=True))
    return target


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class EngineError(RuntimeError):
    """Base class for every engine failure, so the UI can catch one type."""


class ConfigurationError(EngineError):
    """Missing spreadsheet id, missing credentials file, unreadable key."""


class SafetyBoundaryError(EngineError):
    """The target workbook did not prove itself to be the Shadow Copy."""


class SchemaMismatchError(EngineError):
    """A tab exists but its headers are not the agreed schema."""


class StockError(EngineError):
    """A business-rule failure (insufficient stock, unknown product, ...)."""


# --------------------------------------------------------------------------- #
# Value helpers
# --------------------------------------------------------------------------- #


def to_number(value: Any, default: float = 0.0) -> float:
    """
    Coerce a sheet cell into a number.

    Real sheets contain ``""``, ``"1,200"``, ``"₦1,200"``, ``"1 200"`` and the
    occasional ``#N/A``. Anything unparseable falls back to ``default`` rather
    than raising in the middle of a transaction.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.startswith("#"):
        return default
    text = re.sub(r"[^\d.\-]", "", text.replace(",", ""))
    if text in {"", "-", ".", "-."}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def as_int(value: Any, default: int = 0) -> int:
    """Coerce a cell into an int, rounding any decimal part."""
    return int(round(to_number(value, float(default))))


def as_quantity(value: Any, default: int = 0) -> int:
    """
    Like :func:`as_int` but never negative.

    A negative quantity would silently reverse a sale, so it is clamped instead.
    """
    return max(0, as_int(value, default))


def normalise_header(value: Any) -> str:
    """Fold a header cell for tolerant-but-exact comparison."""
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def verify_headers(found: Sequence[Any], expected: Sequence[str], tab: str) -> None:
    """
    Raise :class:`SchemaMismatchError` unless ``found`` matches ``expected``.

    Comparison tolerates case and extra whitespace, but column *order* and
    *count* must match: a shifted column would silently write sales into the
    wrong field, which is exactly the failure this prototype cannot afford.
    """
    got = [normalise_header(c) for c in list(found)[: len(expected)]]
    want = [normalise_header(c) for c in expected]
    if got == want:
        return

    problems = [
        f"    column {string.ascii_uppercase[i - 1]} (expected {want[i - 1]!r}): found {got[i - 1]!r}"
        for i in range(1, min(len(got), len(want)) + 1)
        if got[i - 1] != want[i - 1]
    ]
    if len(got) < len(want):
        problems.append(f"    only {len(got)} column(s) present; {len(want)} required")
    if len(got) > len(want):
        problems.append(f"    {len(got) - len(want)} unexpected trailing column(s)")

    raise SchemaMismatchError(
        f"Tab '{tab}' does not match the agreed schema.\n"
        + "\n".join(problems)
        + f"\n  Expected: {list(expected)}"
        + f"\n  Found:    {list(found)[: len(expected)]}"
    )


def make_transaction_id(now: Optional[datetime] = None, length: int = 4) -> str:
    """Unique, human-readable transaction id: ``OK3D-20260929-104912-A3F2``."""
    now = now or datetime.now()
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=length))
    return f"OK3D-{now:%Y%m%d-%H%M%S}-{suffix}"


def next_sku(existing: Iterable[str], prefix: str = DEFAULT_SKU_PREFIX) -> str:
    """
    Next free auto-SKU, continuing the highest number already in use.

    ``existing`` may contain hand-written codes (OK3D's legacy sheets used values
    such as ``170GRM``); those are ignored for numbering and never overwritten.
    """
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$", re.IGNORECASE)
    highest = 0
    taken: set[str] = set()
    for raw in existing:
        code = str(raw or "").strip()
        if not code:
            continue
        taken.add(code.upper())
        match = pattern.match(code)
        if match:
            highest = max(highest, int(match.group(1)))
    candidate = highest + 1
    while f"{prefix}{candidate:04d}".upper() in taken:
        candidate += 1
    return f"{prefix}{candidate:04d}"


def compute_current(opening: Any, receipts: Any, sales: Any) -> int:
    """``Current stock`` = opening + receipts - sales, floored at zero."""
    return max(0, as_int(opening) + as_int(receipts) - as_int(sales))


def compute_reorder_status(current: Any, minimum: Any) -> str:
    """``REORDER`` once current stock has reached or fallen below the minimum."""
    return REORDER_FLAG if as_int(current) <= as_int(minimum) else OK_FLAG


# --------------------------------------------------------------------------- #
# Row + result models
# --------------------------------------------------------------------------- #


@dataclass
class StockRow:
    """One product line, remembering the sheet row it came from."""

    row_number: int
    sku: str = ""
    product: str = ""
    opening: int = 0
    minimum: int = 0
    receipts: int = 0
    sales: int = 0
    current: int = 0
    reorder: str = OK_FLAG

    @classmethod
    def from_cells(cls, row_number: int, cells: Sequence[Any]) -> "StockRow":
        padded = list(cells) + [""] * (len(STOCK_HEADERS) - len(cells))
        row = cls(
            row_number=row_number,
            sku=str(padded[COL_SKU - 1] or "").strip(),
            product=str(padded[COL_PRODUCT - 1] or "").strip(),
            opening=as_int(padded[COL_OPENING - 1]),
            minimum=as_int(padded[COL_MINIMUM - 1]),
            receipts=as_int(padded[COL_RECEIPTS - 1]),
            sales=as_int(padded[COL_SALES - 1]),
        )
        return row.with_derived_values()

    def with_derived_values(self) -> "StockRow":
        """Recompute ``current`` and ``reorder`` from the base columns."""
        self.current = compute_current(self.opening, self.receipts, self.sales)
        self.reorder = compute_reorder_status(self.current, self.minimum)
        return self

    @property
    def is_low(self) -> bool:
        return self.reorder == REORDER_FLAG

    @property
    def shortfall(self) -> int:
        """Units needed to get back above the minimum."""
        return max(0, self.minimum - self.current + 1) if self.is_low else 0

    def to_cells(self) -> list[Any]:
        """Full row in schema order, ready to write."""
        return [
            self.sku,
            self.product,
            self.opening,
            self.minimum,
            self.receipts,
            self.sales,
            self.current,
            self.reorder,
        ]


@dataclass
class TransactionResult:
    """Outcome of a committed sale or receipt."""

    ok: bool
    transaction_id: str
    product: str
    quantity: int
    stock_before: int = 0
    stock_after: int = 0
    sales_total: int = 0
    receipts_total: int = 0
    customer: str = ""
    handled_by: str = ""
    reorder: str = OK_FLAG
    message: str = ""


# --------------------------------------------------------------------------- #
# Backend: shared business rules
# --------------------------------------------------------------------------- #


class StockBackend:
    """
    The contract the UI codes against, plus every business rule.

    Subclasses supply persistence only. That is why the checkout guardrail, the
    reorder calculation and the ledger format behave identically whether the app
    is on the Shadow Copy spreadsheet (:class:`SheetsEngine`) or the in-memory
    demo (``demo_backend.DemoBackend``).
    """

    label: str = "backend"
    is_demo: bool = False
    spreadsheet_title: str = ""

    def __init__(self, sku_prefix: str = DEFAULT_SKU_PREFIX) -> None:
        self.sku_prefix = sku_prefix
        self._cache: dict[str, Any] = {}

    # -- persistence hooks (implemented by subclasses) ---------------------- #

    def _read_stock_rows(self) -> list[StockRow]:
        raise NotImplementedError

    def _read_ledger_entries(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _write_stock_cells(self, updates: Sequence[tuple[int, int, Any]]) -> None:
        """Persist ``(row_number, column_number, value)`` triples in one batch."""
        raise NotImplementedError

    def _append_stock_row(self, cells: Sequence[Any]) -> None:
        raise NotImplementedError

    def _append_ledger(self, entry: dict[str, Any]) -> None:
        raise NotImplementedError

    def _invalidate(self) -> None:
        self._cache.clear()

    # -- current-stock write policy ----------------------------------------- #

    def _should_write_current_stock(self) -> bool:
        """
        Whether this backend writes column G itself.

        The sandbox has no spreadsheet and therefore always writes it. The live
        engine consults the sheet (see GoogleSheetsStore.should_write_current_stock):
        a formula in G must be left alone so Sheets keeps calculating it, while a
        literal value must be written because nothing else updates it.
        """
        return True

    def _stock_cell_plan(self, row: StockRow, receipt: bool = False) -> list[tuple[int, int, Any]]:
        """
        The exact ``(sheet_row, column, value)`` cells a stock movement writes.

        Always: ``Total sales/issue`` (F) or ``Total receipts`` (E), and
        ``Reorder status`` (H). ``Current stock`` (G) is included only when this
        backend owns that column -- never when the sheet carries a formula there.
        """
        movement_column = COL_RECEIPTS if receipt else COL_SALES
        movement_value = row.receipts if receipt else row.sales
        plan: list[tuple[int, int, Any]] = [
            (row.row_number, movement_column, movement_value),
            (row.row_number, COL_REORDER, row.reorder),
        ]
        if self._should_write_current_stock():
            plan.append((row.row_number, COL_CURRENT, row.current))
        return plan

    # -- reads -------------------------------------------------------------- #

    def fetch_stock(self, force: bool = False) -> list[StockRow]:
        """Every product row; blank product names are skipped."""
        if not force and "stock" in self._cache:
            return [replace(row) for row in self._cache["stock"]]
        rows = [row for row in self._read_stock_rows() if row.product]
        self._cache["stock"] = rows
        return [replace(row) for row in rows]

    def list_products(self) -> list[str]:
        """Product names for the checkout dropdown, alphabetical."""
        return sorted({row.product for row in self.fetch_stock()}, key=str.casefold)

    def get_stock(self, product: str) -> Optional[StockRow]:
        """Case-insensitive product lookup."""
        wanted = (product or "").strip().casefold()
        for row in self.fetch_stock():
            if row.product.casefold() == wanted:
                return row
        return None

    def fetch_ledger(self, limit: int = 200) -> list[dict[str, Any]]:
        """Most recent ledger rows, newest first."""
        entries = self._read_ledger_entries()
        entries.reverse()
        return entries[:limit]

    def low_stock(self) -> list[StockRow]:
        """Flagged products, most urgent (largest shortfall) first."""
        rows = [row for row in self.fetch_stock() if row.is_low]
        return sorted(rows, key=lambda r: (-r.shortfall, r.product.casefold()))

    def summary(self) -> dict[str, Any]:
        rows = self.fetch_stock()
        return {
            "products": len(rows),
            "units": sum(r.current for r in rows),
            "low": sum(1 for r in rows if r.is_low),
            "healthy": sum(1 for r in rows if not r.is_low),
            "out_of_stock": sum(1 for r in rows if r.current <= 0),
        }

    # -- writes ------------------------------------------------------------- #

    def record_sale(
        self, product: str, quantity: int, customer: str = "", handled_by: str = ""
    ) -> TransactionResult:
        """
        Commit a checkout.

        Guardrail: the requested quantity is checked against ``Current stock``
        *before* anything is written. An over-checkout raises :class:`StockError`
        and leaves the sheet completely untouched.
        """
        quantity = as_quantity(quantity)
        if quantity <= 0:
            raise StockError("Quantity must be a whole number greater than zero.")

        row = self.get_stock(product)
        if row is None:
            raise StockError(
                f"'{product}' is not in the Current Stock tab. Add the product first, "
                "then record the sale."
            )
        if quantity > row.current:
            raise StockError(
                f"Only {row.current} unit(s) of {row.product} are in stock; "
                f"{quantity} requested. Nothing was written."
            )

        txn_id = make_transaction_id()
        stock_before = row.current

        # 1. Write the audit record first, so a crash mid-way leaves evidence
        #    rather than a silent stock movement.
        self._append_ledger(
            {
                "Timestamp": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
                "Transaction ID": txn_id,
                "Product name": row.product,
                "Quantity Sold": quantity,
                "Customer Name": customer.strip(),
                "Handled By": handled_by.strip(),
            }
        )

        # 2. Apply the deduction in one batched update (F, G and H together).
        row.sales += quantity
        row.with_derived_values()
        try:
            self._write_stock_cells(self._stock_cell_plan(row))
        except Exception as exc:  # noqa: BLE001
            raise EngineError(
                f"Transaction {txn_id} was written to the Sales Ledger, but the stock "
                f"update failed: {type(exc).__name__}: {exc}\n"
                f"  Reconcile row {row.row_number} ('{row.product}') manually: "
                f"Total sales/issue should increase by {quantity}."
            ) from exc
        finally:
            self._invalidate()

        return TransactionResult(
            ok=True,
            transaction_id=txn_id,
            product=row.product,
            quantity=quantity,
            stock_before=stock_before,
            stock_after=row.current,
            sales_total=row.sales,
            customer=customer.strip(),
            handled_by=handled_by.strip(),
            reorder=row.reorder,
            message=f"Sold {quantity} x {row.product}",
        )

    def record_receipt(
        self, product: str, quantity: int, handled_by: str = "", note: str = ""
    ) -> TransactionResult:
        """Book stock in: increments ``Total receipts`` and recomputes stock."""
        quantity = as_quantity(quantity)
        if quantity <= 0:
            raise StockError("Receipt quantity must be a whole number greater than zero.")

        row = self.get_stock(product)
        if row is None:
            raise StockError(f"'{product}' is not in the Current Stock tab.")

        stock_before = row.current
        row.receipts += quantity
        row.with_derived_values()
        try:
            self._write_stock_cells(self._stock_cell_plan(row, receipt=True))
        finally:
            self._invalidate()

        return TransactionResult(
            ok=True,
            transaction_id=make_transaction_id(),
            product=row.product,
            quantity=quantity,
            stock_before=stock_before,
            stock_after=row.current,
            receipts_total=row.receipts,
            handled_by=handled_by.strip(),
            reorder=row.reorder,
            message=f"Received {quantity} x {row.product}" + (f" ({note})" if note else ""),
        )

    def add_product(
        self,
        product: str,
        opening: int = 0,
        minimum: int = 0,
        receipts: int = 0,
        sales: int = 0,
    ) -> StockRow:
        """
        Append a new product line, assigning the next auto-SKU.

        SKUs are generated here and never requested from staff -- that is the
        point of the no-SKU checkout workspace.
        """
        name = (product or "").strip()
        if not name:
            raise StockError("Product name is required.")
        if self.get_stock(name) is not None:
            raise StockError(f"'{name}' already exists in the Current Stock tab.")

        rows = self.fetch_stock(force=True)
        sku = next_sku([r.sku for r in rows], self.sku_prefix)
        new_row = StockRow(
            row_number=FIRST_DATA_ROW + len(rows),
            sku=sku,
            product=name,
            opening=as_quantity(opening),
            minimum=as_quantity(minimum),
            receipts=as_quantity(receipts),
            sales=as_quantity(sales),
        ).with_derived_values()

        self._append_stock_row(new_row.to_cells())
        self._invalidate()
        return new_row

    def ensure_skus(self) -> int:
        """
        Backfill blank SKUs without touching hand-written codes.

        Useful once after pointing at a copy of a legacy sheet. Returns the number
        of rows updated.
        """
        rows = self.fetch_stock(force=True)
        existing = [r.sku for r in rows if r.sku]
        updates: list[tuple[int, int, Any]] = []
        for row in rows:
            if row.sku:
                continue
            sku = next_sku(existing, self.sku_prefix)
            existing.append(sku)
            updates.append((row.row_number, COL_SKU, sku))
        if updates:
            self._write_stock_cells(updates)
            self._invalidate()
        return len(updates)


# --------------------------------------------------------------------------- #
# Google Sheets persistence
# --------------------------------------------------------------------------- #


class GoogleSheetsStore:
    """
    Persistence against the two worksheet tabs.

    Owns the gspread handles and converts between A1 cells and domain models.
    All safety and schema validation happens in :meth:`connect`.
    """

    STOCK_WIDTH = len(STOCK_HEADERS)
    LEDGER_WIDTH = len(LEDGER_HEADERS)

    def __init__(
        self,
        spreadsheet_id: str,
        credentials_path: Path | str = CREDENTIALS_FILE,
        acknowledge_shadow: Optional[bool] = None,
    ) -> None:
        self.spreadsheet_id = (spreadsheet_id or "").strip()
        self.credentials_path = Path(credentials_path)
        self.acknowledge_shadow = (
            os.environ.get(ACK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
            if acknowledge_shadow is None
            else acknowledge_shadow
        )
        self._book = None
        self._stock_ws = None
        self._ledger_ws = None
        self.spreadsheet_title = ""
        self.last_read_utc = ""
        # None = decide on first use by inspecting column G (see below).
        self._write_current_stock: bool | None = None

    # -- connection --------------------------------------------------------- #

    def connect(self) -> "GoogleSheetsStore":
        """
        Open the workbook and validate everything before any write can happen.

        Raises the appropriate :class:`EngineError` subclass on any problem; it
        never proceeds against a partially validated target.
        """
        if not self.spreadsheet_id:
            raise ConfigurationError(
                "No spreadsheet id configured, so there is no workbook to open.\n"
                f"  Locally : set the {SPREADSHEET_ID_ENV} environment variable.\n"
                "  Cloud   : add a line to Streamlit secrets --\n"
                f'              spreadsheet_id = "<the Shadow Copy id>"\n'
                "  Use the OK3D *Shadow Copy* workbook's id, never the live trading sheet."
            )
        if not self.credentials_path.exists():
            raise ConfigurationError(
                f"Service-account key not found at {self.credentials_path}. Download the "
                "key for the Google service account, save it as 'credentials.json' in the "
                "project root, and share the Shadow Copy workbook with its client_email "
                "as an Editor."
            )

        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ConfigurationError(
                "gspread / google-auth are not installed. Run: "
                ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            ) from exc

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
        try:
            creds = Credentials.from_service_account_file(str(self.credentials_path), scopes=scopes)
        except Exception as exc:  # noqa: BLE001
            raise ConfigurationError(
                f"Could not read the service-account key at {self.credentials_path}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        try:
            client = gspread.authorize(creds)
            self._book = client.open_by_key(self.spreadsheet_id)
        except Exception as exc:  # noqa: BLE001
            raise ConfigurationError(
                "Could not open the spreadsheet. Check that the id is correct and that the "
                "workbook is shared with the service account as an Editor.\n"
                f"  {type(exc).__name__}: {exc}"
            ) from exc

        self.spreadsheet_title = getattr(self._book, "title", "") or ""

        # ---- safety boundary ---------------------------------------------- #
        if not self.acknowledge_shadow and SHADOW_MARKER not in self.spreadsheet_title.upper():
            raise SafetyBoundaryError(
                f"Refusing to use '{self.spreadsheet_title or self.spreadsheet_id}': the "
                f"workbook title does not contain '{SHADOW_MARKER}', so it does not look "
                "like the isolated Shadow Copy.\n"
                "  Rename the shadow workbook so its title contains 'SHADOW' (recommended), "
                f"or set {ACK_ENV}=1 to override deliberately."
            )

        # ---- schema validation -------------------------------------------- #
        try:
            self._stock_ws = self._book.worksheet(STOCK_TAB)
        except Exception as exc:  # noqa: BLE001
            raise SchemaMismatchError(
                f"Tab '{STOCK_TAB}' was not found in '{self.spreadsheet_title}'. "
                f"Tabs present: {[ws.title for ws in self._book.worksheets()]}"
            ) from exc

        try:
            self._ledger_ws = self._book.worksheet(LEDGER_TAB)
        except Exception as exc:  # noqa: BLE001
            raise SchemaMismatchError(
                f"Tab '{LEDGER_TAB}' was not found in '{self.spreadsheet_title}'. "
                f"Tabs present: {[ws.title for ws in self._book.worksheets()]}"
            ) from exc

        verify_headers(self._padded_header(self._stock_ws), STOCK_HEADERS, STOCK_TAB)
        verify_headers(self._padded_header(self._ledger_ws), LEDGER_HEADERS, LEDGER_TAB)
        self.last_read_utc = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        return self

    def _padded_header(self, worksheet: Any) -> list[Any]:
        values = worksheet.row_values(1)
        return list(values) + [""] * (max(self.STOCK_WIDTH, self.LEDGER_WIDTH) - len(values))

    def ensure_tabs(self) -> list[str]:
        """
        Create the two tabs with the agreed headers if they are missing.

        For preparing a brand-new, empty Shadow Copy workbook. It never modifies
        the headers of a tab that already exists.
        """
        if self._book is None:
            raise ConfigurationError("Not connected.")
        created: list[str] = []
        for title, headers in ((STOCK_TAB, STOCK_HEADERS), (LEDGER_TAB, LEDGER_HEADERS)):
            try:
                self._book.worksheet(title)
            except Exception:  # noqa: BLE001 - gspread raises WorksheetNotFound
                ws = self._book.add_worksheet(title=title, rows=1000, cols=max(len(headers), 8))
                ws.update([headers], "A1")
                created.append(title)
        if created:
            self.connect()
        return created

    # -- persistence -------------------------------------------------------- #

    @staticmethod
    def _cell_updates(triples: Sequence[tuple[int, int, Any]]) -> list[dict[str, Any]]:
        return [
            {"range": f"{string.ascii_uppercase[column - 1]}{row}", "values": [[value]]}
            for row, column, value in triples
        ]

    # -- formula policy ----------------------------------------------------- #
    #
    # ``Current stock`` (column G) is normally a literal number that this engine
    # owns and rewrites on every sale. But the sheet may instead carry a formula
    # there (=C2+E2-F2). Writing a literal over a formula would silently destroy
    # it, so the policy is decided once per session by inspecting the live cells:
    #
    #   * column holds formulas -> do NOT write G; let Sheets calculate it
    #   * column holds literals -> write G, because nothing else keeps it current
    #
    # Overridable in both directions for operators who know their sheet:
    #   store.current_stock_written = True / False
    def current_stock_is_formula(self, sample_rows: int = 5) -> bool:
        """True when column G holds a formula in the sampled data rows."""
        try:
            last_row = min(max(self.STOCK_WIDTH, sample_rows + 1), 50)
            cells = self._stock_ws.get(
                f"G1:G{last_row}", value_render_option="FORMULA"
            )
        except Exception:  # noqa: BLE001 - never block a sale on a detection failure
            return False
        for row in cells[1:]:  # skip the header
            value = row[0] if row else ""
            if isinstance(value, str) and value.startswith("="):
                return True
        return False

    def should_write_current_stock(self, sample_rows: int = 5) -> bool:
        """Whether this engine should write column G itself."""
        if self._write_current_stock is None:
            self._write_current_stock = not self.current_stock_is_formula(sample_rows)
        return self._write_current_stock

    def read_stock_rows(self) -> list[StockRow]:
        raw = self._stock_ws.get_all_values()
        self.last_read_utc = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        return [
            StockRow.from_cells(offset, cells)
            for offset, cells in enumerate(raw[1:], start=FIRST_DATA_ROW)
        ]

    def write_stock_cells(self, triples: Sequence[tuple[int, int, Any]]) -> None:
        if not triples:
            return
        self._stock_ws.batch_update(self._cell_updates(triples), value_input_option="USER_ENTERED")

    def append_stock_row(self, cells: Sequence[Any]) -> None:
        self._stock_ws.append_row(list(cells), value_input_option="USER_ENTERED")

    def append_stock_rows(self, rows: Sequence[Sequence[Any]]) -> None:
        """Append several product rows in ONE API call (quota-friendly bulk import)."""
        if not rows:
            return
        self._stock_ws.append_rows(
            [list(row) for row in rows], value_input_option="USER_ENTERED"
        )

    def delete_stock_row(self, row_number: int) -> None:
        """
        Delete one sheet row by number.

        1-based and includes the header, matching StockRow.row_number. Callers
        deleting several rows must go bottom-up so the remaining numbers stay
        valid.
        """
        self._stock_ws.delete_rows(row_number)

    def read_ledger_entries(self) -> list[dict[str, Any]]:
        raw = self._ledger_ws.get_all_values()
        entries: list[dict[str, Any]] = []
        for cells in raw[1:]:
            padded = list(cells) + [""] * (self.LEDGER_WIDTH - len(cells))
            if not any(str(c).strip() for c in padded):
                continue
            entries.append(dict(zip(LEDGER_HEADERS, padded[: self.LEDGER_WIDTH])))
        return entries

    def append_ledger(self, entry: dict[str, Any]) -> None:
        self._ledger_ws.append_row(
            [entry.get(header, "") for header in LEDGER_HEADERS],
            value_input_option="USER_ENTERED",
        )


class SheetsEngine(StockBackend):
    """Read/write engine bound to the Shadow Copy spreadsheet."""

    label = "Google Sheets (Shadow Copy)"
    is_demo = False

    def __init__(
        self,
        spreadsheet_id: str = "",
        credentials_path: Path | str = CREDENTIALS_FILE,
        sku_prefix: str = DEFAULT_SKU_PREFIX,
        acknowledge_shadow: Optional[bool] = None,
        store: Optional[GoogleSheetsStore] = None,
    ) -> None:
        super().__init__(sku_prefix=sku_prefix)
        self.store = store or GoogleSheetsStore(
            spreadsheet_id=spreadsheet_id,
            credentials_path=credentials_path,
            acknowledge_shadow=acknowledge_shadow,
        )

    def connect(self) -> "SheetsEngine":
        """Open and validate the workbook. Safe to call repeatedly."""
        self.store.connect()
        self.spreadsheet_title = self.store.spreadsheet_title
        self._invalidate()
        return self

    def ensure_tabs(self) -> list[str]:
        return self.store.ensure_tabs()

    @property
    def spreadsheet_id(self) -> str:
        return self.store.spreadsheet_id

    @property
    def last_read_utc(self) -> str:
        return self.store.last_read_utc

    # -- persistence wiring ------------------------------------------------- #

    def _read_stock_rows(self) -> list[StockRow]:
        return self.store.read_stock_rows()

    def _read_ledger_entries(self) -> list[dict[str, Any]]:
        return self.store.read_ledger_entries()

    def _should_write_current_stock(self) -> bool:
        return self.store.should_write_current_stock()

    def _write_stock_cells(self, updates: Sequence[tuple[int, int, Any]]) -> None:
        self.store.write_stock_cells(updates)

    def _append_stock_row(self, cells: Sequence[Any]) -> None:
        self.store.append_stock_row(cells)

    def _append_ledger(self, entry: dict[str, Any]) -> None:
        self.store.append_ledger(entry)


# --------------------------------------------------------------------------- #
# Factory + diagnostics
# --------------------------------------------------------------------------- #


def spreadsheet_id_from_env() -> str:
    """
    Resolve the target spreadsheet id.

    Precedence: environment variable, then Streamlit secrets. Returns "" when
    neither is set -- there is no compiled-in fallback, so the engine can never
    quietly target a workbook nobody chose.
    """
    from_env = os.environ.get(SPREADSHEET_ID_ENV, "").strip()
    if from_env:
        return from_env
    from_secrets = str(_st_secrets().get("spreadsheet_id", "") or "").strip()
    if from_secrets:
        return from_secrets
    return ""  # no target configured -- connect() will refuse loudly


def credentials_path_from_env() -> Path:
    """
    Resolve the service-account key path.

    Precedence: environment override, then credentials.json beside this file,
    then JSON stashed in Streamlit secrets (materialised to a temp file).
    """
    override = os.environ.get(CREDENTIALS_ENV, "").strip()
    if override:
        return Path(override)
    if CREDENTIALS_FILE.exists():
        return CREDENTIALS_FILE
    from_secrets = _materialise_secret_credentials()
    if from_secrets is not None:
        return from_secrets
    return CREDENTIALS_FILE


def build_engine(
    spreadsheet_id: str = "",
    credentials_path: Optional[Path | str] = None,
    sku_prefix: str = DEFAULT_SKU_PREFIX,
) -> SheetsEngine:
    """Construct (but do not connect) a :class:`SheetsEngine`."""
    return SheetsEngine(
        spreadsheet_id=spreadsheet_id or spreadsheet_id_from_env(),
        credentials_path=credentials_path or credentials_path_from_env(),
        sku_prefix=sku_prefix,
    )


def credentials_summary(path: Optional[Path | str] = None) -> dict[str, Any]:
    """
    Non-secret preview of the service-account file for the setup screen.

    Never returns the private key.
    """
    target = Path(path) if path else credentials_path_from_env()
    if not target.exists():
        return {"present": False, "path": str(target)}
    try:
        import json

        data = json.loads(target.read_text(encoding="utf-8"))
        return {
            "present": True,
            "path": str(target),
            "type": data.get("type", "unknown"),
            "project_id": data.get("project_id", "unknown"),
            "client_email": data.get("client_email", "unknown"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"present": True, "path": str(target), "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "ACK_ENV",
    "COL_CURRENT",
    "COL_MINIMUM",
    "COL_OPENING",
    "COL_PRODUCT",
    "COL_RECEIPTS",
    "COL_REORDER",
    "COL_SALES",
    "COL_SKU",
    "CREDENTIALS_FILE",
    "ConfigurationError",
    "DEFAULT_SKU_PREFIX",
    "EngineError",
    "FIRST_DATA_ROW",
    "GoogleSheetsStore",
    "LEDGER_HEADERS",
    "LEDGER_TAB",
    "OK_FLAG",
    "REORDER_FLAG",
    "SPREADSHEET_ID_ENV",
    "STOCK_HEADERS",
    "STOCK_TAB",
    "SafetyBoundaryError",
    "SchemaMismatchError",
    "SheetsEngine",
    "StockBackend",
    "StockError",
    "StockRow",
    "TransactionResult",
    "as_int",
    "as_quantity",
    "build_engine",
    "compute_current",
    "compute_reorder_status",
    "credentials_path_from_env",
    "credentials_summary",
    "make_transaction_id",
    "next_sku",
    "spreadsheet_id_from_env",
    "to_number",
    "verify_headers",
]
