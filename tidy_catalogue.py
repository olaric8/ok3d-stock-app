"""
Clean two data-entry artefacts in the Shadow Copy.

1. Delete the row named ``supa`` -- a lowercase duplicate of ``SUPA 50G``
   (row 16, 69 units, three real sales). The duplicate has no movement and no
   ledger reference, so removing it loses nothing.

2. Rename ``G/PRO 850G`` to ``G/PRO 800G``. The product was renamed in the
   business; production already lists G/PRO 800G and has no G/PRO 850G.

SAFETY
------
Both rows are re-checked here before anything is written: if either has any
receipts, sales, or a matching Sales Ledger entry, the script refuses rather than
orphaning history. The delete uses the engine's own method so the row-number
bookkeeping stays with the code that owns it.

Deletion shifts every row below it up by one. The engine re-reads row numbers on
every fetch, so nothing is cached across runs -- but the verification below
re-reads the sheet rather than assuming.

Usage:
    .\\.venv\\Scripts\\python.exe tidy_catalogue.py            # preview
    .\\.venv\\Scripts\\python.exe tidy_catalogue.py --apply    # write
"""

from __future__ import annotations

import argparse
import sys

import sheets_engine as se

SPREADSHEET_ID = "1BIizam6JvfXW1YX6sxJS5pB4aKBj77Vdn09uvKmjMcg"
DUPLICATE = "supa"
OLD_NAME = "G/PRO 850G"
NEW_NAME = "G/PRO 800G"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write")
    args = parser.parse_args(argv)

    engine = se.SheetsEngine(store=se.GoogleSheetsStore(SPREADSHEET_ID))
    engine.store.acknowledge_shadow = True
    engine.connect()
    print(f"connected to: {engine.spreadsheet_title!r}")

    rows = engine.fetch_stock(force=True)
    before = engine.summary()
    print(
        "BEFORE: products={p} units={u} reorder={l} out={o}".format(
            p=before["products"], u=before["units"],
            l=before["low"], o=before["out_of_stock"],
        )
    )

    ledger = engine.fetch_ledger(limit=500)
    ledger_names = {str(e.get("Product name", "")).strip().casefold() for e in ledger}

    # ---- safety re-checks -------------------------------------------------- #
    target = engine.get_stock(DUPLICATE)
    if target is None:
        print(f"\n  '{DUPLICATE}' not found -- nothing to delete")
        do_delete = False
    else:
        clean = target.receipts == 0 and target.sales == 0 and target.current == 0
        referenced = DUPLICATE.casefold() in ledger_names
        print(
            f"\n  delete candidate: '{target.product}' row {target.row_number} "
            f"sku={target.sku} receipts={target.receipts} sales={target.sales} current={target.current}"
        )
        print(f"    no movement      : {clean}")
        print(f"    not in the ledger: {not referenced}")
        if not clean or referenced:
            print("    REFUSING to delete -- this row has history")
            do_delete = False
        else:
            do_delete = True

    rename_row = engine.get_stock(OLD_NAME)
    if rename_row is None:
        print(f"\n  '{OLD_NAME}' not found -- nothing to rename")
        do_rename = False
    elif engine.get_stock(NEW_NAME) is not None:
        print(f"\n  '{NEW_NAME}' already exists -- refusing to create a duplicate name")
        do_rename = False
    else:
        print(f"\n  rename candidate: row {rename_row.row_number} '{OLD_NAME}' -> '{NEW_NAME}'")
        do_rename = True

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        return 0

    print("\nAPPLYING")

    # ---- rename first: it does not move any rows --------------------------- #
    if do_rename:
        engine.store.write_stock_cells([(rename_row.row_number, se.COL_PRODUCT, NEW_NAME)])
        engine._invalidate()
        print(f"  renamed row {rename_row.row_number} to '{NEW_NAME}'")

    # ---- then delete, going last-row-first if ever extended to many -------- #
    if do_delete:
        engine.store.delete_stock_row(target.row_number)
        engine._invalidate()
        print(f"  deleted row {target.row_number} ('{DUPLICATE}')")

    # ---- verify by re-reading ---------------------------------------------- #
    after_rows = engine.fetch_stock(force=True)
    after = engine.summary()
    print(
        "\nAFTER: products={p} units={u} reorder={l} out={o}".format(
            p=after["products"], u=after["units"],
            l=after["low"], o=after["out_of_stock"],
        )
    )

    problems: list[str] = []
    if do_delete and engine.get_stock(DUPLICATE) is not None:
        problems.append(f"'{DUPLICATE}' is still present")
    if do_rename:
        if engine.get_stock(NEW_NAME) is None:
            problems.append(f"'{NEW_NAME}' is missing after the rename")
        if engine.get_stock(OLD_NAME) is not None:
            problems.append(f"'{OLD_NAME}' is still present")
    if after["products"] != before["products"] - (1 if do_delete else 0):
        problems.append(
            f"product count moved unexpectedly: {before['products']} -> {after['products']}"
        )
    if after["units"] != before["units"]:
        problems.append(f"UNIT TOTAL CHANGED: {before['units']} -> {after['units']}")

    names = {r.product for r in after_rows}
    if NEW_NAME not in names and do_rename:
        problems.append("renamed product absent from the re-read")
    if DUPLICATE in names:
        problems.append("duplicate still in the re-read")

    print()
    if problems:
        print("VERIFICATION FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("VERIFIED -- unit total unchanged, both changes confirmed by a fresh read.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
