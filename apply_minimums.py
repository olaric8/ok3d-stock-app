"""Write the agreed minimum stock levels into the Shadow Copy.

    fast movers (>= 0.5/day) : 5
    slow movers              : 2
    no recorded sales        : 2   (slow-mover floor)

Values come from ``minimum-stock-final.csv``, produced by ``propose_minimums.py``
from ``demand-clean.csv``. Only column D (Minimum stock) is touched -- SKU,
product name and every balance column are left exactly as they are.

Safety
------
* reports the full diff before writing
* writes in batches, then RE-READS the sheet and confirms every value landed
* prints any line whose value did not change as expected

Usage:
    .\\.venv\\Scripts\\python.exe apply_minimums.py            # preview
    .\\.venv\\Scripts\\python.exe apply_minimums.py --apply    # write
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

import sheets_engine as se

SPREADSHEET_ID = "1BIizam6JvfXW1YX6sxJS5pB4aKBj77Vdn09uvKmjMcg"
PLAN_FILE = pathlib.Path("minimum-stock-final.csv")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write")
    args = parser.parse_args(argv)

    if not PLAN_FILE.exists():
        print(f"missing {PLAN_FILE} -- run propose_minimums.py first")
        return 2

    with PLAN_FILE.open(encoding="utf-8") as fh:
        wanted = {
            row["product"]: int(row["suggested_minimum"])
            for row in csv.DictReader(fh)
        }

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

    updates: list[tuple[int, int, object]] = []
    changes: list[str] = []
    missing: list[str] = []

    for row in rows:
        target = wanted.get(row.product)
        if target is None:
            missing.append(row.product)
            continue
        if row.minimum != target:
            updates.append((row.row_number, se.COL_MINIMUM, target))
            changes.append(
                f"  row {row.row_number:>3}  {row.product:<26} "
                f"{row.minimum:>3} -> {target}"
            )

    print()
    print(f"lines in the sheet : {len(rows)}")
    print(f"lines in the plan  : {len(wanted)}")
    print(f"lines to change    : {len(changes)}")
    if missing:
        print(f"in the sheet but not in the plan ({len(missing)}): {missing}")

    if changes:
        print()
        print("CHANGES")
        print("\n".join(changes))

    print()
    print("1 -> 2 :", sum(1 for r in rows if wanted.get(r.product) == 2), "lines")
    print("10 -> 5:", sum(1 for r in rows if wanted.get(r.product) == 5), "lines")

    if not args.apply:
        print()
        print("DRY RUN -- nothing written. Re-run with --apply.")
        return 0

    if not updates:
        print()
        print("Nothing to change -- the sheet already matches the plan.")
        return 0

    print()
    print("APPLYING")
    written = 0
    for start in range(0, len(updates), se_batch()):
        chunk = updates[start : start + se_batch()]
        engine.store.write_stock_cells(chunk)
        written += len(chunk)
        print(f"  written {written}/{len(updates)}")
    engine._invalidate()

    # ---- verify by re-reading ---------------------------------------------- #
    before_names = {r.product for r in rows}
    after_rows = {r.product: r for r in engine.fetch_stock(force=True)}
    mismatches = [
        f"  {name}: wanted {value}, sheet has {after_rows[name].minimum}"
        for name, value in wanted.items()
        if name in after_rows and after_rows[name].minimum != value
    ]
    # Only flag products that WERE in the sheet and have now gone. The plan
    # covers production's 72 lines; the sheet has 64, so the 8 production-only
    # products are legitimately absent and must not be reported as losses.
    absent = [name for name in wanted if name in before_names and name not in after_rows]
    after = engine.summary()

    print()
    print(
        "AFTER: products={p} units={u} reorder={l} out={o}".format(
            p=after["products"], u=after["units"],
            l=after["low"], o=after["out_of_stock"],
        )
    )
    print(f"  minimums written : {written}")
    print(f"  reorder flag     : {before['low']} -> {after['low']}")

    if after["units"] != before["units"]:
        print(f"  UNITS CHANGED: {before['units']} -> {after['units']}  (should be equal)")
        return 1
    if mismatches:
        print("VERIFICATION FAILED:")
        print("\n".join(mismatches))
        return 1
    if absent:
        print(f"VERIFICATION FAILED - products vanished: {absent}")
        return 1

    print("  units unchanged, every minimum confirmed by a fresh read.")
    return 0


def se_batch() -> int:
    """Reuse the engine's own batching constant when available."""
    return getattr(se, "BATCH_CHUNK", 40)


if __name__ == "__main__":
    sys.exit(main())
