"""
verify_engine.py
================

Headless verification of the OK3D Stock App data layer. No Streamlit, no
browser, no network, no credentials needed.

    .\\.venv\\Scripts\\python.exe verify_engine.py

Exits 0 when every check passes, 1 otherwise. Run it before you trust the app
against the Shadow Copy sheet: it exercises the exact code path a real checkout
takes, including the guardrails that protect OK3D's data.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

import sheets_engine as se
from demo_backend import DemoBackend, DemoStore

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))
    mark = "  [PASS]" if condition else "  [FAIL]"
    print(f"{mark} {name}" + (f" -- {detail}" if detail and not condition else ""))


# --------------------------------------------------------------------------- #
# 1. Column mapping
# --------------------------------------------------------------------------- #


def test_schema() -> None:
    print("\n=== 1. Column mapping is exactly as agreed ===")
    check("Current Stock headers", se.STOCK_HEADERS == [
        "SKU", "Product name", "Opening balance", "Minimum stock",
        "Total receipts", "Total sales/issue", "Current stock", "Reorder status",
    ], str(se.STOCK_HEADERS))
    check("Sales Ledger headers", se.LEDGER_HEADERS == [
        "Timestamp", "Transaction ID", "Product name",
        "Quantity Sold", "Customer Name", "Handled By",
    ], str(se.LEDGER_HEADERS))

    mapping = {
        "SKU": (se.COL_SKU, 1), "Product name": (se.COL_PRODUCT, 2),
        "Opening balance": (se.COL_OPENING, 3), "Minimum stock": (se.COL_MINIMUM, 4),
        "Total receipts": (se.COL_RECEIPTS, 5), "Total sales/issue": (se.COL_SALES, 6),
        "Current stock": (se.COL_CURRENT, 7), "Reorder status": (se.COL_REORDER, 8),
    }
    for name, (actual, expected) in mapping.items():
        check(f"{name} -> column {expected}", actual == expected, f"got {actual}")


# --------------------------------------------------------------------------- #
# 2. Header validation
# --------------------------------------------------------------------------- #


def test_header_validation() -> None:
    print("\n=== 2. Schema validation rejects a mis-shaped sheet ===")

    try:
        se.verify_headers(se.STOCK_HEADERS, se.STOCK_HEADERS, se.STOCK_TAB)
        check("exact headers accepted", True)
    except se.SchemaMismatchError as exc:
        check("exact headers accepted", False, str(exc))

    for label, headers in {
        "case/whitespace differences tolerated": ["  sku ", "PRODUCT NAME", "Opening  Balance",
                                                  "Minimum stock", "Total receipts",
                                                  "Total sales/issue", "Current stock", "Reorder status"],
    }.items():
        try:
            se.verify_headers(headers, se.STOCK_HEADERS, se.STOCK_TAB)
            check(label, True)
        except se.SchemaMismatchError as exc:
            check(label, False, str(exc))

    # A shifted column must be rejected -- this is the dangerous failure mode.
    shifted = ["Product name", "SKU", "Opening balance", "Minimum stock",
               "Total receipts", "Total sales/issue", "Current stock", "Reorder status"]
    try:
        se.verify_headers(shifted, se.STOCK_HEADERS, se.STOCK_TAB)
        check("swapped SKU/Product name rejected", False, "shifted headers were accepted!")
    except se.SchemaMismatchError:
        check("swapped SKU/Product name rejected", True)

    truncated = se.STOCK_HEADERS[:6]
    try:
        se.verify_headers(truncated, se.STOCK_HEADERS, se.STOCK_TAB)
        check("missing columns rejected", False, "truncated headers were accepted!")
    except se.SchemaMismatchError:
        check("missing columns rejected", True)

    wrong_names = list(se.STOCK_HEADERS)
    wrong_names[6] = "Stock on hand"
    try:
        se.verify_headers(wrong_names, se.STOCK_HEADERS, se.STOCK_TAB)
        check("renamed column rejected", False, "renamed header was accepted!")
    except se.SchemaMismatchError:
        check("renamed column rejected", True)


# --------------------------------------------------------------------------- #
# 3. Calculations
# --------------------------------------------------------------------------- #


def test_calculations() -> None:
    print("\n=== 3. Current stock and reorder status ===")
    check("opening + receipts - sales", se.compute_current(100, 50, 30) == 120,
          str(se.compute_current(100, 50, 30)))
    check("never goes negative", se.compute_current(10, 0, 25) == 0, str(se.compute_current(10, 0, 25)))
    check("text cells tolerated", se.compute_current("1,000", "600", "500") == 1100,
          str(se.compute_current("1,000", "600", "500")))
    check("blank cells treated as zero", se.compute_current("", "", "") == 0)
    check("currency symbols tolerated", se.compute_current("₦1,200", 0, "200") == 1000,
          str(se.compute_current("₦1,200", 0, "200")))
    check("error cells treated as zero", se.compute_current("#N/A", 5, 0) == 5,
          str(se.compute_current("#N/A", 5, 0)))

    check("current == minimum -> REORDER", se.compute_reorder_status(10, 10) == se.REORDER_FLAG)
    check("current <  minimum -> REORDER", se.compute_reorder_status(3, 10) == se.REORDER_FLAG)
    check("current >  minimum -> OK", se.compute_reorder_status(11, 10) == se.OK_FLAG)
    check("zero stock -> REORDER", se.compute_reorder_status(0, 0) == se.REORDER_FLAG)


# --------------------------------------------------------------------------- #
# 4. Auto-SKU
# --------------------------------------------------------------------------- #


def test_sku_generation() -> None:
    print("\n=== 4. Auto-SKU generation ===")
    check("first SKU", se.next_sku([]) == "OK3D-0001", se.next_sku([]))
    check("continues from highest", se.next_sku(["OK3D-0001", "OK3D-0007"]) == "OK3D-0008",
          se.next_sku(["OK3D-0001", "OK3D-0007"]))
    check("ignores legacy hand codes", se.next_sku(["170GRM", "800G", "OK3D-0003"]) == "OK3D-0004",
          se.next_sku(["170GRM", "800G", "OK3D-0003"]))
    check("skips a taken number", se.next_sku(["OK3D-0002", "OK3D-0003"]) == "OK3D-0004",
          se.next_sku(["OK3D-0002", "OK3D-0003"]))
    check("six-digit rollover", se.next_sku(["OK3D-9999"]) == "OK3D-10000",
          se.next_sku(["OK3D-9999"]))
    check("transaction id shape", se.make_transaction_id().startswith("OK3D-"),
          se.make_transaction_id())


# --------------------------------------------------------------------------- #
# 5. Row parsing
# --------------------------------------------------------------------------- #


def test_row_parsing() -> None:
    print("\n=== 5. Row parsing ===")
    row = se.StockRow.from_cells(7, ["OK3D-0003", "VIVA 170GRM", 300, 60, 800, 780, "", ""])
    check("row number remembered", row.row_number == 7, str(row.row_number))
    check("current recomputed on read (ignores stale G)", row.current == 320, str(row.current))
    check("reorder recomputed on read", row.reorder == se.OK_FLAG, row.reorder)
    check("to_cells round-trips", len(row.to_cells()) == len(se.STOCK_HEADERS))
    check("short row padded safely",
          se.StockRow.from_cells(2, ["OK3D-0001", "ONLY NAME"]).product == "ONLY NAME")
    check("empty row has no product",
          not se.StockRow.from_cells(2, []).product)
    check("blank numeric cells -> 0",
          se.StockRow.from_cells(2, ["S", "P", "", "", "", ""]).current == 0)


# --------------------------------------------------------------------------- #
# 6. Checkout guardrails
# --------------------------------------------------------------------------- #


def test_guardrails() -> None:
    print("\n=== 6. Checkout guardrails ===")
    backend = DemoBackend()

    before = backend.get_stock("VIVA 800G")
    assert before is not None
    sheet_before = backend.as_sheet_values(se.STOCK_TAB)
    ledger_before = len(backend.fetch_ledger())

    # over-checkout
    try:
        backend.record_sale("VIVA 800G", before.current + 1, customer="X")
        check("over-checkout rejected", False, "the sale was allowed!")
    except se.StockError as exc:
        check("over-checkout rejected", True)
        check("rejection names the available quantity", str(before.current) in str(exc), str(exc))

    check("rejected sale wrote nothing to Current Stock",
          backend.as_sheet_values(se.STOCK_TAB) == sheet_before)
    check("rejected sale wrote nothing to the ledger",
          len(backend.fetch_ledger()) == ledger_before)

    # unknown product
    try:
        backend.record_sale("NOT A PRODUCT", 1)
        check("unknown product rejected", False, "unknown product was accepted!")
    except se.StockError:
        check("unknown product rejected", True)

    # bad quantities
    for qty in (0, -5):
        try:
            backend.record_sale("VIVA 800G", qty)
            check(f"quantity {qty} rejected", False, "accepted!")
        except se.StockError:
            check(f"quantity {qty} rejected", True)

    # exact-stock sale is allowed and floors at zero
    rim = backend.get_stock("RIM BLOCK 140G")
    assert rim is not None
    check("item starts out of stock", rim.current == 0, str(rim.current))
    backend.record_receipt("RIM BLOCK 140G", 5, handled_by="QA")
    result = backend.record_sale("RIM BLOCK 140G", 5, customer="Exact Buyer", handled_by="QA")
    check("selling exactly the available stock is allowed", result.ok)
    check("stock floors at zero", result.stock_after == 0, str(result.stock_after))
    check("zeroing stock flags REORDER", result.reorder == se.REORDER_FLAG, result.reorder)


# --------------------------------------------------------------------------- #
# 7. Sale mechanics across all three columns
# --------------------------------------------------------------------------- #


def test_sale_mechanics() -> None:
    print("\n=== 7. A sale updates Total sales/issue, Current stock and Reorder status ===")
    backend = DemoBackend()
    row_before = backend.get_stock("VIVA 900ML")
    assert row_before is not None
    sheet_row = row_before.row_number
    raw_before = list(backend.as_sheet_values(se.STOCK_TAB)[sheet_row - 1])

    result = backend.record_sale("VIVA 900ML", 2, customer="Mama Ngozi Stores", handled_by="Chidi")
    raw_after = backend.as_sheet_values(se.STOCK_TAB)[sheet_row - 1]

    check("Total sales/issue increased by the quantity",
          int(raw_after[se.COL_SALES - 1]) == int(raw_before[se.COL_SALES - 1]) + 2,
          f"{raw_before[se.COL_SALES - 1]} -> {raw_after[se.COL_SALES - 1]}")
    check("Current stock decreased by the quantity",
          int(raw_after[se.COL_CURRENT - 1]) == int(raw_before[se.COL_CURRENT - 1]) - 2,
          f"{raw_before[se.COL_CURRENT - 1]} -> {raw_after[se.COL_CURRENT - 1]}")
    check("SKU column untouched by a sale", raw_after[se.COL_SKU - 1] == raw_before[se.COL_SKU - 1])
    check("Opening balance untouched by a sale",
          raw_after[se.COL_OPENING - 1] == raw_before[se.COL_OPENING - 1])
    check("Minimum stock untouched by a sale",
          raw_after[se.COL_MINIMUM - 1] == raw_before[se.COL_MINIMUM - 1])
    check("Total receipts untouched by a sale",
          raw_after[se.COL_RECEIPTS - 1] == raw_before[se.COL_RECEIPTS - 1])
    check("Reorder status recomputed", str(raw_after[se.COL_REORDER - 1]) in {se.OK_FLAG, se.REORDER_FLAG},
          str(raw_after[se.COL_REORDER - 1]))

    ledger = backend.fetch_ledger()
    newest = ledger[0]
    check("ledger row appended", newest["Product name"] == "VIVA 900ML", newest["Product name"])
    check("ledger quantity correct", int(newest["Quantity Sold"]) == 2, newest["Quantity Sold"])
    check("ledger customer recorded", newest["Customer Name"] == "Mama Ngozi Stores")
    check("ledger staff recorded", newest["Handled By"] == "Chidi")
    check("ledger transaction id matches the result", newest["Transaction ID"] == result.transaction_id)
    check("ledger timestamp recorded", bool(str(newest["Timestamp"]).strip()))


# --------------------------------------------------------------------------- #
# 8. Receipts
# --------------------------------------------------------------------------- #


def test_receipts() -> None:
    print("\n=== 8. Stock-in recomputes stock and clears the flag ===")
    backend = DemoBackend()
    before = backend.get_stock("VIVA 1L REFILL")
    assert before is not None
    quantity = 40
    # Derive the expectation from the sheet formula rather than from the floored
    # `current` value: current = max(0, opening + receipts - sales).
    sheet = backend.as_sheet_values(se.STOCK_TAB)
    raw_before = next(r for r in sheet if r[se.COL_PRODUCT - 1] == "VIVA 1L REFILL")
    expected_after = max(
        0,
        int(raw_before[se.COL_OPENING - 1])
        + int(raw_before[se.COL_RECEIPTS - 1])
        + quantity
        - int(raw_before[se.COL_SALES - 1]),
    )
    expected_receipts = int(raw_before[se.COL_RECEIPTS - 1]) + quantity
    check("starts flagged", before.is_low, before.reorder)

    result = backend.record_receipt("VIVA 1L REFILL", quantity, handled_by="QA", note="Supplier 4471")
    check("receipt reported the pre-receipt quantity", result.stock_before == before.current,
          f"reported {result.stock_before}, expected {before.current}")
    check("receipt raised stock by the full quantity", result.stock_after == expected_after,
          f"formula gives {expected_after}, got {result.stock_after}")
    check("receipt increased Total receipts", result.receipts_total == expected_receipts,
          str(result.receipts_total))
    check("line no longer flagged", result.reorder == se.OK_FLAG, result.reorder)

    # Re-read from the store: the write must be durable, not just in the result.
    reread = backend.get_stock("VIVA 1L REFILL")
    assert reread is not None
    check("re-read shows the new stock", reread.current == expected_after, str(reread.current))
    check("re-read shows the new receipts total", reread.receipts == expected_receipts, str(reread.receipts))
    check("receipt left Opening balance alone", reread.opening == before.opening, str(reread.opening))
    check("receipt left Total sales/issue alone", reread.sales == before.sales, str(reread.sales))
    check("receipt does not touch the sales ledger",
          len(backend.fetch_ledger()) == len(DemoBackend().fetch_ledger()))


# --------------------------------------------------------------------------- #
# 9. Store contract parity (demo twin must fit the same seam)
# --------------------------------------------------------------------------- #


def test_store_contract() -> None:
    print("\n=== 9. DemoStore satisfies the same seam as GoogleSheetsStore ===")
    store_methods = ["read_stock_rows", "write_stock_cells", "append_stock_row",
                     "read_ledger_entries", "append_ledger"]
    for name in store_methods:
        check(f"GoogleSheetsStore.{name}", callable(getattr(se.GoogleSheetsStore, name, None)))
        check(f"DemoStore.{name}", callable(getattr(DemoStore, name, None)))

    store = DemoStore()
    check("demo exposes Current Stock grid", store.as_sheet_values(se.STOCK_TAB)[0] == se.STOCK_HEADERS)
    check("demo exposes Sales Ledger grid", store.as_sheet_values(se.LEDGER_TAB)[0] == se.LEDGER_HEADERS)
    check("demo seeds the real product vocabulary",
          any("VIVA" in p for p in [r.product for r in store.read_stock_rows()]))


# --------------------------------------------------------------------------- #
# 10. Connection safety boundary
# --------------------------------------------------------------------------- #


def test_safety_boundary() -> None:
    print("\n=== 10. The engine refuses to run without a valid Shadow Copy target ===")

    # Missing id -> configuration error, and crucially no attempt at credentials.
    engine = se.SheetsEngine(spreadsheet_id="", credentials_path="does-not-exist.json")
    try:
        engine.connect()
        check("missing spreadsheet id rejected", False, "connected anyway!")
    except se.ConfigurationError as exc:
        check("missing spreadsheet id rejected", True)
        check("error names the env var", se.SPREADSHEET_ID_ENV in str(exc), str(exc)[:120])

    # Id present but no credentials file -> clear setup error, not a crash.
    engine = se.SheetsEngine(spreadsheet_id="abc123", credentials_path="does-not-exist.json")
    try:
        engine.connect()
        check("missing credentials.json rejected", False, "connected anyway!")
    except se.ConfigurationError as exc:
        check("missing credentials.json rejected", True)
        check("error tells you where the file goes", "credentials.json" in str(exc))

    check("credentials_summary reports absence", se.credentials_summary("does-not-exist.json")["present"] is False)
    info = se.credentials_summary("does-not-exist.json")
    check("credentials_summary never leaks a key", "private_key" not in info)

    # The shadow marker check itself must be present in the connect() path.
    source = Path(se.__file__).read_text(encoding="utf-8")
    check("connect() enforces the SHADOW marker", "SHADOW_MARKER not in self.spreadsheet_title.upper()" in source)
    check("no default spreadsheet id is hard-coded",
          'SPREADSHEET_ID = "' not in source and "spreadsheet_id: str = ''" not in source)

    # build_engine reads the environment, defaulting to empty (never production).
    check("engine builds without an id", isinstance(se.build_engine(), se.SheetsEngine))

    # An unconfigured engine must refuse, not guess. This is what stops a cloud
    # deploy silently writing into whatever workbook happens to be first.
    #
    # Hermetic: st.secrets is stubbed empty. Without that, a developer machine
    # with a local .streamlit/secrets.toml would resolve an id here and the check
    # would be measuring the machine rather than the resolver.
    import os as _os

    saved_env = _os.environ.pop(se.SPREADSHEET_ID_ENV, None)
    saved_secrets = se._st_secrets
    saved_config = se.spreadsheet_id_from_config_file
    se._st_secrets = lambda: {}
    # The committed [ok3d] config is a third source; clear it too, or this check
    # measures whatever happens to be in config.toml rather than the resolver.
    se.spreadsheet_id_from_config_file = lambda: ""
    try:
        resolved = se.spreadsheet_id_from_env()
        check("no config at all resolves to empty", resolved == "", repr(resolved))

        unconfigured = se.SheetsEngine(
            spreadsheet_id="", credentials_path="does-not-exist.json"
        )
        try:
            unconfigured.connect()
            check("unconfigured engine refuses to connect", False, "it connected!")
        except se.ConfigurationError as exc:
            check("unconfigured engine refuses to connect", True)
            check("error documents the cloud secrets route", "Streamlit secrets" in str(exc),
                  str(exc)[:160])

        # Precedence: config < secrets < environment.
        se.spreadsheet_id_from_config_file = lambda: "from-config"
        check("config.toml is used when nothing else is set",
              se.spreadsheet_id_from_env() == "from-config",
              repr(se.spreadsheet_id_from_env()))

        se._st_secrets = lambda: {"spreadsheet_id": "from-secrets"}
        check("secrets override the committed config",
              se.spreadsheet_id_from_env() == "from-secrets",
              repr(se.spreadsheet_id_from_env()))

        _os.environ[se.SPREADSHEET_ID_ENV] = "from-env"
        check("environment overrides secrets and config",
              se.spreadsheet_id_from_env() == "from-env",
              repr(se.spreadsheet_id_from_env()))
        _os.environ.pop(se.SPREADSHEET_ID_ENV, None)

        # And the real config file must actually carry an id, or the cloud
        # deployment has no fallback at all.
        se.spreadsheet_id_from_config_file = saved_config
        real = se.spreadsheet_id_from_config_file()
        check("the shipped config.toml supplies an id", bool(real), repr(real))
        se.spreadsheet_id_from_config_file = lambda: ""
    finally:
        se._st_secrets = saved_secrets
        se.spreadsheet_id_from_config_file = saved_config
        if saved_env is not None:
            _os.environ[se.SPREADSHEET_ID_ENV] = saved_env


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def main() -> int:
    print("=" * 72)
    print("OK3D Stock App -- data layer verification (no network, no credentials)")
    print("=" * 72)

    test_schema()
    test_header_validation()
    test_calculations()
    test_sku_generation()
    test_row_parsing()
    test_guardrails()
    test_sale_mechanics()
    test_receipts()
    test_store_contract()
    test_safety_boundary()
    test_batch_sales()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("\nFAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("ALL CHECKS PASSED -- the data layer behaves as specified.")
    return 0


# --------------------------------------------------------------------------- #
# BATCH SALES (one customer, several products, one transaction)
# --------------------------------------------------------------------------- #


def test_batch_sales() -> None:
    """A basket is one transaction: shared txn id, one ledger row per line."""
    print("\n=== batch sales ===")

    _batch_backend = DemoBackend()
    _before_ledger = len(_batch_backend.fetch_ledger())
    _a = _batch_backend.get_stock("VIVA 800G")
    _b = _batch_backend.get_stock("ARIEL 900G")
    _a_before, _b_before = _a.current, _b.current

    _batch = _batch_backend.record_sale_batch(
        [("VIVA 800G", 2), ("ARIEL 900G", 3)], customer="Batch Buyer", handled_by="QA"
    )

    check("batch sale: one transaction covers every line",
          _batch.ok and len({line.transaction_id for line in _batch.lines}) == 1,
          _batch.transaction_id)

    _rows = [e for e in _batch_backend.fetch_ledger() if e["Transaction ID"] == _batch.transaction_id]
    check("batch sale: ledger holds one row per product", len(_rows) == 2, f"{len(_rows)} rows")
    check("batch sale: ledger grew by the number of lines",
          len(_batch_backend.fetch_ledger()) == _before_ledger + 2)

    _units = sum(int(e["Quantity Sold"]) for e in _rows)
    check("batch sale: ledger quantities match the basket", _units == 5, f"{_units} units")

    check("batch sale: first product deducted by its quantity",
          _batch_backend.get_stock("VIVA 800G").current == _a_before - 2,
          f"{_a_before} -> {_batch_backend.get_stock('VIVA 800G').current}")
    check("batch sale: second product deducted by its quantity",
          _batch_backend.get_stock("ARIEL 900G").current == _b_before - 3,
          f"{_b_before} -> {_batch_backend.get_stock('ARIEL 900G').current}")

    check("batch sale: customer is on every row",
          all(e["Customer Name"] == "Batch Buyer" for e in _rows))
    check("batch sale: handler is on every row",
          all(e["Handled By"] == "QA" for e in _rows))

    # ---- duplicate lines merge rather than double-charging ------------------- #
    _c = _batch_backend.get_stock("SUNLIGHT 1KG")
    _c_before = _c.current
    _merged = _batch_backend.record_sale_batch([("SUNLIGHT 1KG", 1), ("SUNLIGHT 1KG", 2)])
    check("batch sale: duplicate lines merge into one",
          _merged.product_count == 1 and _merged.total_units == 3,
          f"{_merged.product_count} line(s), {_merged.total_units} units")
    check("batch sale: merged line deducts the combined quantity",
          _batch_backend.get_stock("SUNLIGHT 1KG").current == _c_before - 3,
          f"{_c_before} -> {_batch_backend.get_stock('SUNLIGHT 1KG').current}")

    # ---- guardrails: nothing is written when the basket is invalid ---------- #
    _guard_ledger = len(_batch_backend.fetch_ledger())
    _guard_stock = _batch_backend.get_stock("VIVA 800G").current

    try:
        _batch_backend.record_sale_batch([("VIVA 800G", 1), ("NOT A PRODUCT", 1)])
        check("batch sale: unknown product is refused", False, "no error raised")
    except se.StockError as exc:
        check("batch sale: unknown product is refused", "NOT A PRODUCT" in str(exc), str(exc)[:90])
    check("batch sale: refused basket wrote no ledger rows",
          len(_batch_backend.fetch_ledger()) == _guard_ledger)

    try:
        _batch_backend.record_sale_batch([("VIVA 800G", 1), ("ARIEL 900G", 999_999)])
        check("batch sale: over-checkout is refused", False, "no error raised")
    except se.StockError as exc:
        check("batch sale: over-checkout is refused", "ARIEL 900G" in str(exc), str(exc)[:90])
    check("batch sale: over-checkout left stock untouched",
          _batch_backend.get_stock("VIVA 800G").current == _guard_stock,
          f"{_guard_stock} -> {_batch_backend.get_stock('VIVA 800G').current}")

    try:
        _batch_backend.record_sale_batch([])
        check("batch sale: empty basket is refused", False, "no error raised")
    except se.StockError as exc:
        check("batch sale: empty basket is refused", "at least one product" in str(exc).lower(), str(exc)[:90])

    try:
        _batch_backend.record_sale_batch([("VIVA 800G", 0)])
        check("batch sale: zero quantity is refused", False, "no error raised")
    except se.StockError as exc:
        check("batch sale: zero quantity is refused", "greater than zero" in str(exc).lower(), str(exc)[:90])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
