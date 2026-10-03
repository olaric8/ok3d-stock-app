"""
verify_import.py
================

Exercises the ``--apply`` write path of ``import_from_recent.py`` against an
in-memory fake worksheet.

The real sheet is never touched. This exists because ``--apply`` is the one code
path that mutates OK3D's data, and its behaviour (auto-SKU numbering, duplicate
skipping, chunked appends, test-row removal) should be proven before it runs for
real.

    .\\.venv\\Scripts\\python.exe verify_import.py
"""

from __future__ import annotations

import sys
import traceback

import sheets_engine as se
import import_from_recent as imp

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not condition else ""))


class FakeWorksheet:
    """Mimics the gspread worksheet surface the importer uses."""

    def __init__(self, rows):
        self.title = "Current Stock"
        self._rows = [list(r) for r in rows]
        self.appended_batches: list[int] = []

    def row_values(self, n):
        return list(self._rows[n - 1]) if 0 < n <= len(self._rows) else []

    def get_all_values(self):
        return [list(r) for r in self._rows]

    def append_row(self, values, value_input_option=None):
        self._rows.append(list(values))

    def append_rows(self, rows, value_input_option=None):
        rows = [list(r) for r in rows]
        self._rows.extend(rows)
        self.appended_batches.append(len(rows))

    def delete_rows(self, n):
        del self._rows[n - 1]

    def batch_update(self, data, value_input_option=None):
        pass

    def update(self, *a, **k):
        pass


def build_store(fake_ws, fake_ledger, title="OK3D_Shadow_Database"):
    store = se.GoogleSheetsStore("fake-id")
    store._stock_ws = fake_ws
    store._ledger_ws = fake_ledger
    store.spreadsheet_title = title
    store.acknowledge_shadow = True
    return store


def main() -> int:
    print("=" * 74)
    print("verify_import.py -- apply-path check against a fake worksheet")
    print("=" * 74)

    # Existing sheet: header + the two paste-test rows the user already had.
    header = list(se.STOCK_HEADERS)
    existing = [
        header,
        ["VIVA 800G", "VIVA 800G", 10, 5, 0, 1, 9, "OK"],
        ["VIVA 800G GOLD", "VIVA 800G GOLD", 15, 5, 0, 0, 15, "OK"],
    ]
    ws = FakeWorksheet(existing)
    ledger = FakeWorksheet([list(se.LEDGER_HEADERS)])

    # Reuse the importer's real parser on the real source file.
    grid = imp.read_sheet(imp.DEFAULT_SOURCE)
    products, _ = imp.parse_products(grid, set())

    print("\n=== 1. parse ===")
    check("source parses to a full catalogue", len(products) >= 55, f"{len(products)} products")
    check("section headings excluded",
          not any(p["product"] in {"FABRIC CARE", "ORACARE BIG", "JELLY"} for p in products))
    check("no-movement products kept (the earlier bug)",
          any(p["product"] == "RAFA 900G" for p in products))
    check("every product has a name", all(p["product"].strip() for p in products))

    print("\n=== 2. apply ===")
    # The fake sheet already holds VIVA 800G and VIVA 800G GOLD, so those two are
    # expected to be skipped as duplicates rather than appended.
    already_present = {"viva 800g", "viva 800g gold"}
    expected_new = [p for p in products if p["product"].casefold() not in already_present]
    store = build_store(ws, ledger)
    written = imp.apply_import(
        products, minimum=10, opening=0, replace_test_rows=False,
        spreadsheet_id="fake-id", store=store, skip_connect=True,
    )

    rows_after = ws.get_all_values()
    data_rows = [r for r in rows_after[1:] if r[1].strip()]
    check("rows were appended", written > 0, f"written={written}")
    check("two pre-existing products skipped as duplicates", written == len(expected_new),
          f"written={written}, expected={len(expected_new)}")
    check("row count = existing + newly added", len(data_rows) == 2 + len(expected_new),
          f"{len(data_rows)} vs {2 + len(expected_new)}")
    check("appends were chunked, not one-per-call", len(ws.appended_batches) > 1 and max(ws.appended_batches) <= imp.APPEND_CHUNK,
          f"batches={ws.appended_batches}")

    print("\n=== 3. auto-SKU numbering ===")
    skus = [r[0] for r in rows_after[1:] if r[1].strip()]
    new_skus = skus[2:]
    check("new SKUs are sequential from OK3D-0001",
          new_skus[:3] == ["OK3D-0001", "OK3D-0002", "OK3D-0003"], str(new_skus[:3]))
    check("no duplicate SKUs", len(set(new_skus)) == len(new_skus))
    check("last SKU matches the number of new rows",
          new_skus[-1] == f"OK3D-{len(expected_new):04d}",
          f"{new_skus[-1]} vs OK3D-{len(expected_new):04d}")
    check("existing hand-written SKUs untouched", skus[0] == "VIVA 800G" and skus[1] == "VIVA 800G GOLD")

    print("\n=== 4. column content for imported rows ===")
    sample = next(r for r in rows_after[1:] if r[1] == "RAFA 900G")
    check("opening balance written", int(sample[2]) == 0, str(sample[2]))
    check("minimum stock written", int(sample[3]) == 10, str(sample[3]))
    check("receipts start at 0", int(sample[4]) == 0, str(sample[4]))
    check("sales start at 0", int(sample[5]) == 0, str(sample[5]))
    check("current stock computed (0 + 0 - 0)", int(sample[6]) == 0, str(sample[6]))
    check("reorder flagged at 0 <= 10", sample[7] == se.REORDER_FLAG, sample[7])

    print("\n=== 5. duplicate skipping on a second run ===")
    store2 = build_store(ws, ledger)
    written2 = imp.apply_import(
        products, minimum=10, opening=0, replace_test_rows=False,
        spreadsheet_id="fake-id", store=store2, skip_connect=True,
    )
    check("second run writes nothing", written2 == 0, f"written={written2}")
    check("sheet row count unchanged",
          len([r for r in ws.get_all_values()[1:] if r[1].strip()]) == 2 + len(expected_new))

    print("\n=== 6. --replace-test-rows ===")
    ws3 = FakeWorksheet([list(r) for r in existing])
    store3 = build_store(ws3, FakeWorksheet([list(se.LEDGER_HEADERS)]))
    imp.apply_import(
        products, minimum=10, opening=0, replace_test_rows=True,
        spreadsheet_id="fake-id", store=store3, skip_connect=True,
    )
    rows3 = [r for r in ws3.get_all_values()[1:] if r[1].strip()]
    names3 = [r[1] for r in rows3]
    skus3 = [r[0] for r in rows3]
    check("no hand-pasted SKU rows remain", not any(s == "VIVA 800G" for s in skus3))
    check("all rows now use auto-SKUs", all(s.startswith("OK3D-") for s in skus3),
          str([s for s in skus3 if not s.startswith("OK3D-")][:3]))
    check("the whole catalogue is present", len(rows3) == len(products), f"{len(rows3)} vs {len(products)}")
    check("VIVA 800G re-imported cleanly from the source",
          any(n == "VIVA 800G" and s.startswith("OK3D-") for n, s in zip(names3, skus3)))

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 74)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("IMPORT WRITE PATH VERIFIED")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
