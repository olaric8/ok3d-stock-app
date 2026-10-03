"""
demo_backend.py
===============

Offline, in-memory stand-in for the Shadow Copy spreadsheet.

Purpose: let anyone open the OK3D Stock App and click through the real
checkout flow -- dropdown, guardrails, ledger writes, reorder flagging -- without
credentials.json and without touching any spreadsheet.

It is **not** a second implementation of the business rules. It subclasses
``sheets_engine.StockBackend`` and only supplies persistence, so every guardrail
is byte-for-byte the same code path the live engine runs.

The seed data uses OK3D's own product vocabulary (sourced from their existing
stock sheets) so the prototype reads as their shop, not as lorem ipsum.

    demo store  =  a dict of rows that mimics the two tabs, in memory
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from sheets_engine import (
    FIRST_DATA_ROW,
    OK_FLAG,
    REORDER_FLAG,
    LEDGER_HEADERS,
    STOCK_HEADERS,
    StockBackend,
    StockRow,
    as_int,
    compute_current,
    compute_reorder_status,
)

# --------------------------------------------------------------------------- #
# Seed data -- OK3D product names, with deliberate REORDER cases
# --------------------------------------------------------------------------- #

# (product, opening, minimum, receipts, sales)
# Four rows land in REORDER status on first load (marked below), so the light-red
# highlighting, the KPI counter and the restock list are all visible immediately.
DEMO_PRODUCTS: list[tuple[str, int, int, int, int]] = [
    ("VIVA 800G", 240, 40, 600, 500),
    ("VIVA 800G GOLD", 90, 25, 120, 110),
    ("VIVA 800G WHITE", 80, 20, 400, 300),
    ("VIVA 170GRM", 300, 60, 800, 780),
    ("VIVA 900ML", 48, 30, 0, 44),               # REORDER - 4 left
    ("VIVA 900ML WHITE", 110, 30, 60, 40),
    ("VIVA 900ML GOLD", 26, 15, 180, 150),
    ("VIVA 1L", 120, 25, 200, 90),
    ("VIVA 1L REFILL", 14, 20, 0, 18),           # REORDER - 0 left, out of stock
    ("SUNLIGHT 1KG", 250, 50, 300, 210),
    ("SUNLIGHT 500G", 180, 40, 200, 120),
    ("ARIEL 900G", 96, 24, 120, 70),
    ("RIM BLOCK 140G", 0, 12, 6, 6),             # REORDER - 0 left, out of stock
    ("RIM BLOCK 200G", 62, 18, 0, 30),
]

# (product, quantity, customer, handled_by, minutes_ago)
DEMO_LEDGER_SEED: list[tuple[str, int, str, str, int]] = [
    ("VIVA 800G", 6, "Mama Ngozi Stores", "Chidi", 320),
    ("SUNLIGHT 1KG", 4, "Blessed Supermarket", "Amaka", 265),
    ("VIVA 170GRM", 12, "Kingsley Wholesale", "Chidi", 190),
    ("RIM BLOCK 140G", 3, "Corner Shop", "Amaka", 120),
    ("ARIEL 900G", 2, "Walk-in", "Chidi", 55),
]


class DemoStore:
    """In-memory twin of :class:`sheets_engine.GoogleSheetsStore`."""

    def __init__(self, seed: Optional[Sequence[tuple[str, int, int, int, int]]] = None) -> None:
        self._rows: list[list[Any]] = []
        self._ledger: list[list[Any]] = []
        self._seed(seed if seed is not None else DEMO_PRODUCTS)
        self.last_read_utc = f"{datetime.now():%Y-%m-%d %H:%M:%S}"

    # -- seed --------------------------------------------------------------- #

    def _seed(self, products: Sequence[tuple[str, int, int, int, int]]) -> None:
        for index, (product, opening, minimum, receipts, sales) in enumerate(products, start=1):
            current = compute_current(opening, receipts, sales)
            self._rows.append(
                [
                    f"OK3D-{index:04d}",
                    product,
                    opening,
                    minimum,
                    receipts,
                    sales,
                    current,
                    compute_reorder_status(current, minimum),
                ]
            )

        # A little history so the ledger tab is not empty on first load.
        base = datetime.now().replace(microsecond=0)
        for offset, (product, qty, customer, staff, minutes) in enumerate(DEMO_LEDGER_SEED, start=1):
            stamp = base - timedelta(minutes=minutes)
            self._ledger.append(
                [
                    f"{stamp:%Y-%m-%d %H:%M:%S}",
                    f"OK3D-{stamp:%Y%m%d}-{stamp:%H%M%S}-D{offset:03d}",
                    product,
                    qty,
                    customer,
                    staff,
                ]
            )

    # -- store protocol ----------------------------------------------------- #

    def read_stock_rows(self) -> list[StockRow]:
        self.last_read_utc = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
        return [
            StockRow.from_cells(FIRST_DATA_ROW + index, cells) for index, cells in enumerate(self._rows)
        ]

    def write_stock_cells(self, triples: Sequence[tuple[int, int, Any]]) -> None:
        for row_number, column, value in triples:
            index = row_number - FIRST_DATA_ROW
            if 0 <= index < len(self._rows):
                self._rows[index][column - 1] = value

    def append_stock_row(self, cells: Sequence[Any]) -> None:
        self._rows.append(list(cells))

    def read_ledger_entries(self) -> list[dict[str, Any]]:
        return [
            dict(zip(LEDGER_HEADERS, list(cells) + [""] * (len(LEDGER_HEADERS) - len(cells))))
            for cells in self._ledger
        ]

    def append_ledger(self, entry: dict[str, Any]) -> None:
        self._ledger.append([entry.get(header, "") for header in LEDGER_HEADERS])

    # -- export / reset ----------------------------------------------------- #

    def as_sheet_values(self, tab: str) -> list[list[Any]]:
        """The whole tab as a list of rows, headers first -- for previews/exports."""
        if tab == "Current Stock":
            return [list(STOCK_HEADERS)] + [list(r) for r in self._rows]
        if tab == "Sales Ledger":
            return [list(LEDGER_HEADERS)] + [list(r) for r in self._ledger]
        raise KeyError(tab)

    def reset(self) -> None:
        self._rows.clear()
        self._ledger.clear()
        self._seed(DEMO_PRODUCTS)


class DemoBackend(StockBackend):
    """The offline backend the app falls back to when the Shadow Copy is unreachable."""

    label = "Demo data (in-memory, nothing is saved)"
    is_demo = True
    spreadsheet_title = "OK3D SHADOW COPY (demo)"

    def __init__(self, seed: Optional[Sequence[tuple[str, int, int, int, int]]] = None) -> None:
        super().__init__()
        self.store = DemoStore(seed=seed)

    @property
    def last_read_utc(self) -> str:
        return self.store.last_read_utc

    def reset(self) -> None:
        """Restore the seeded demo data."""
        self.store.reset()
        self._invalidate()

    def as_sheet_values(self, tab: str) -> list[list[Any]]:
        return self.store.as_sheet_values(tab)

    # -- persistence wiring ------------------------------------------------- #

    def _read_stock_rows(self) -> list[StockRow]:
        return self.store.read_stock_rows()

    def _read_ledger_entries(self) -> list[dict[str, Any]]:
        return self.store.read_ledger_entries()

    def _write_stock_cells(self, updates: Sequence[tuple[int, int, Any]]) -> None:
        self.store.write_stock_cells(updates)

    def _append_stock_row(self, cells: Sequence[Any]) -> None:
        self.store.append_stock_row(cells)

    def _append_ledger(self, entry: dict[str, Any]) -> None:
        self.store.append_ledger(entry)


__all__ = ["DEMO_LEDGER_SEED", "DEMO_PRODUCTS", "DemoBackend", "DemoStore"]
