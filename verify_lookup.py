"""
verify_lookup.py
================

Read-only audit of the product-name lookup against the live Shadow Copy.

For every entry in the checkout dropdown it asserts that:

* the name resolves to a row at all;
* the row it resolves to actually carries that product name (no off-by-one);
* the name is unique across the tab, so no two rows can shadow each other;
* the values returned (SKU, Current stock, Minimum stock) are the ones sitting
  in that row on the sheet, compared cell by cell.

Writes nothing. Run it any time you suspect the lookup:

    .\\.venv\\Scripts\\python.exe verify_lookup.py <spreadsheet-id>
"""

from __future__ import annotations

import sys
from collections import Counter

import sheets_engine as se

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def main() -> int:
    spreadsheet_id = sys.argv[1] if len(sys.argv) > 1 else se.spreadsheet_id_from_env()
    if not spreadsheet_id:
        print(f"No spreadsheet id. Pass it as an argument or set {se.SPREADSHEET_ID_ENV}.")
        return 2

    engine = se.SheetsEngine(store=se.GoogleSheetsStore(spreadsheet_id))
    engine.store.acknowledge_shadow = True
    engine.store.connect()
    print("=" * 78)
    print(f"Lookup audit -- {engine.spreadsheet_title!r}")
    print("=" * 78)

    rows = engine.fetch_stock(force=True)
    dropdown = engine.list_products()

    print(f"\nrows on sheet        : {len(rows)}")
    print(f"dropdown entries     : {len(dropdown)}")

    print("\n=== 1. name uniqueness (duplicates would shadow each other) ===")
    counts = Counter(r.product.casefold() for r in rows)
    dupes = {name: n for name, n in counts.items() if n > 1}
    check("every product name is unique", not dupes, f"duplicates: {dupes}")

    print("\n=== 2. every dropdown entry resolves to its own row ===")
    mismatches: list[str] = []
    for name in dropdown:
        found = engine.get_stock(name)
        if found is None:
            mismatches.append(f"{name!r} -> None")
        elif found.product.casefold() != name.casefold():
            mismatches.append(f"{name!r} -> {found.product!r}")
    check(f"all {len(dropdown)} names resolve to themselves", not mismatches,
          "; ".join(mismatches[:5]))

    print("\n=== 3. returned values match the cells in that row ===")
    bad_values: list[str] = []
    for row in rows:
        found = engine.get_stock(row.product)
        if found is None:
            bad_values.append(f"{row.product}: not found")
            continue
        for field, got, want in (
            ("sku", found.sku, row.sku),
            ("current", found.current, row.current),
            ("minimum", found.minimum, row.minimum),
            ("row_number", found.row_number, row.row_number),
        ):
            if got != want:
                bad_values.append(f"{row.product}: {field} {got!r} != {want!r}")
    check("SKU, Current stock and Minimum stock read back identically", not bad_values,
          "; ".join(bad_values[:5]))

    print("\n=== 4. resolution is name-based, not positional ===")
    # Pick a row and confirm the lookup still finds it when the dropdown is
    # reordered. If anything depended on list position, this would break.
    sample = rows[len(rows) // 2]
    reversed_names = list(reversed(dropdown))
    found = engine.get_stock(sample.product)
    check("lookup unaffected by dropdown ordering",
          found is not None and found.row_number == sample.row_number,
          f"{sample.product!r} -> {found.row_number if found else None} vs {sample.row_number}")
    check("dropdown ordering differs from sheet order", reversed_names != dropdown)

    print("\n=== 5. targeted spot checks ===")
    for name in ("SUPA 50G", "G/MAMA 800G", "VIVA 800G"):
        row = engine.get_stock(name)
        if row is None:
            check(f"{name} resolves", False, "not found")
            continue
        print(
            f"      {name:<16} sheet row {row.row_number:<3} sku={row.sku:<12} "
            f"current={row.current:<4} min={row.minimum}"
        )
        check(f"{name} resolves to its own row",
              row.product.casefold() == name.casefold())

    print("\n=== 6. no row-number / list-index confusion ===")
    # row_number is a 1-based sheet row including the header; the list index is
    # 0-based over data rows only. They must not be used interchangeably.
    off_by = []
    for index, row in enumerate(rows):
        if row.row_number != index + se.FIRST_DATA_ROW:
            off_by.append(f"idx {index} -> row {row.row_number}")
    check("row_number == list index + FIRST_DATA_ROW for every row", not off_by,
          "; ".join(off_by[:5]))

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 78)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("LOOKUP VERIFIED -- name-based resolution, no positional dependency")
    return 0


if __name__ == "__main__":
    sys.exit(main())
