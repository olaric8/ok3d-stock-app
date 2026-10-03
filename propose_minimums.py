"""Minimum-stock model with the owner's final thresholds.

THRESHOLDS
----------
    fast movers (>= 0.5/day) : 5
    slow movers              : 2

Both are flat, owner-set values. An earlier version scaled the fast-mover
minimum to a week of demand, which gave SUPA 50G a minimum of 120 against 69
units in stock -- permanently flagged, which the owner judged unhelpful. Flat
thresholds keep the flag meaningful: a line reads REORDER when it is genuinely
down to its last few units.

Demand comes from ``demand-clean.csv`` (see ``analyse_demand.py``), which
neutralises the operator's reversal pairs -- a receipt and an equal sale posted
minutes apart to undo a booking mistake. Two exist in the log (1,044 x MAGIK 80G
and 3 x G/MAMA 45G); both net to zero.
"""

from __future__ import annotations

import csv
import pathlib

SOURCE = pathlib.Path("demand-clean.csv")
FAST_MINIMUM = 5
SLOW_MINIMUM = 2


def main() -> int:
    if not SOURCE.exists():
        print(f"missing {SOURCE} -- run analyse_demand.py first")
        return 2

    with SOURCE.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    plan: list[dict] = []
    for row in rows:
        mover = row["mover"]
        stock = int(row["current_stock"])
        suggested = FAST_MINIMUM if mover == "fast" else SLOW_MINIMUM
        plan.append(
            {
                "product": row["product"],
                "mover": mover,
                "units_sold": int(row["units_sold"]),
                "daily_demand": float(row["daily_demand"]),
                "weekly_demand": float(row["weekly_demand"]),
                "current_stock": stock,
                "observed_minimum": 10,
                "suggested_minimum": suggested,
                "status_before": "REORDER" if stock <= 10 else "OK",
                "status_after": "REORDER" if stock <= suggested else "OK",
            }
        )

    plan.sort(key=lambda r: (-r["units_sold"], r["product"]))

    out = pathlib.Path("minimum-stock-final.csv")
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(plan[0].keys()))
        writer.writeheader()
        writer.writerows(plan)

    fast = [r for r in plan if r["mover"] == "fast"]
    slow = [r for r in plan if r["mover"] == "slow"]
    dormant = [r for r in plan if r["mover"] == "none"]

    print("=" * 88)
    print(f"FAST MOVERS - minimum {FAST_MINIMUM}")
    print("=" * 88)
    print(f"  {'product':<22}{'sold':>6}{'/week':>7}{'stock':>7}"
          f"{'min now':>9}{'min new':>9}   reads")
    print("  " + "-" * 80)
    for r in fast:
        flag = "   <- change" if r["status_before"] != r["status_after"] else ""
        print(
            f"  {r['product'][:21]:<22}{r['units_sold']:>6}{r['weekly_demand']:>7.1f}"
            f"{r['current_stock']:>7}{r['observed_minimum']:>9}{r['suggested_minimum']:>9}"
            f"   {r['status_after']}{flag}"
        )

    flagged_fast = [r for r in fast if r["status_after"] == "REORDER"]
    print()
    print(f"  fast movers flagged: {len(flagged_fast)} of {len(fast)}")
    for r in flagged_fast:
        print(
            f"    {r['product']:<22}{r['current_stock']:>4} left, "
            f"selling {r['weekly_demand']:.0f}/week"
        )

    print()
    print("=" * 88)
    print(f"SLOW MOVERS - minimum {SLOW_MINIMUM}")
    print("=" * 88)
    changed = [r for r in slow if r["status_before"] != r["status_after"]]
    still = [r for r in slow if r["status_after"] == "REORDER"]
    print(f"  {len(slow)} slow movers; {len(changed)} stop being flagged, "
          f"{len(still)} still flagged")
    for r in still:
        print(f"    {r['product']:<22}{r['current_stock']:>4} left")

    before = sum(1 for r in plan if r["status_before"] == "REORDER")
    after = sum(1 for r in plan if r["status_after"] == "REORDER")

    print()
    print("=" * 88)
    print("EFFECT")
    print("=" * 88)
    print(f"  lines flagged REORDER : {before} -> {after}")
    print(
        "  fast movers           : "
        f"{sum(1 for r in fast if r['status_before'] == 'REORDER')} -> {len(flagged_fast)}"
    )
    print(
        "  slow movers           : "
        f"{sum(1 for r in slow if r['status_before'] == 'REORDER')} -> {len(still)}"
    )
    print(
        f"  dormant (no sales)    : {len(dormant)}, all holding <= {SLOW_MINIMUM} "
        "so still flagged -- the reorder-list filter is what hides these"
    )
    print()
    print(f"  written to: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
