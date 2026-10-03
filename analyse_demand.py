"""Neutralise reversal pairs, then recompute demand.

WHY THIS CHANGED
----------------
The 1,044 receipt/sale pair is not a phantom or a typo. The business records
stock through an automated WhatsApp/Telegram bot: a movement cannot be edited,
so a mistake is corrected by posting the opposite movement. That is sound
accounting -- the ledger stays append-only and the audit trail is intact -- but
it means a naive sum counts the correction as demand.

Rule: a receipt and a sale of equal quantity, minutes apart, cancel. Both sides
are dropped and the net effect on stock is zero, which is what the operator
intended.
"""

from __future__ import annotations

import csv
import math
import pathlib
from collections import defaultdict
from datetime import datetime, timedelta

import openpyxl

SOURCE = pathlib.Path.home() / "OneDrive" / "Desktop" / "OK3D Stores – Stock Movement (Responses).xlsx"
REVERSAL_WINDOW = timedelta(minutes=15)


def parse_stamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return datetime.strptime(value.strip(), fmt)
            except ValueError:
                continue
    return None


def main() -> int:
    wb = openpyxl.load_workbook(SOURCE, read_only=True, data_only=True)

    summary = [list(r) for r in wb["Stock Summary"].iter_rows(values_only=True)]
    head = [str(h or "").strip() for h in summary[0]]
    c_sku, c_name, c_cur = head.index("SKU"), head.index("Product name"), head.index("Current stock")

    products: list[str] = []
    sku_to_name: dict[str, str] = {}
    current: dict[str, float] = {}
    for row in summary[1:]:
        if not row[c_name]:
            continue
        name = str(row[c_name]).strip()
        products.append(name)
        sku_to_name[str(row[c_sku]).strip().upper()] = name
        try:
            current[name] = float(row[c_cur] or 0)
        except (TypeError, ValueError):
            current[name] = 0.0

    rows = [list(r) for r in wb["Form Responses 1"].iter_rows(values_only=True)]

    # ---- collect every movement ------------------------------------------- #
    movements: list[dict] = []
    for i, row in enumerate(rows[1:], start=2):
        if not row or not any(row):
            continue
        raw_type = str(row[2] or "")
        code = str(row[3] or "").strip().upper()
        try:
            qty = float(row[4] or 0)
        except (TypeError, ValueError):
            continue
        is_sale = "sale" in raw_type.casefold() or "issue" in raw_type.casefold()
        is_receipt = "receipt" in raw_type.casefold()
        if not (is_sale or is_receipt):
            continue
        movements.append(
            {
                "row": i,
                "stamp": parse_stamp(row[0]),
                "kind": "sale" if is_sale else "receipt",
                "product": sku_to_name.get(code, code),
                "qty": qty,
                "ref": str(row[5] or ""),
                "reversed": False,
            }
        )

    # ---- find reversal pairs: equal quantity, opposite kind, close in time -- #
    reversals: list[str] = []
    for a in movements:
        if a["reversed"] or a["kind"] != "receipt":
            continue
        for b in movements:
            if b["reversed"] or b["kind"] != "sale" or b["product"] != a["product"]:
                continue
            if abs(b["qty"] - a["qty"]) > 0.001:
                continue
            if not (a["stamp"] and b["stamp"]) or abs(b["stamp"] - a["stamp"]) > REVERSAL_WINDOW:
                continue
            a["reversed"] = b["reversed"] = True
            gap = abs((b["stamp"] - a["stamp"]).total_seconds())
            reversals.append(
                f"row {a['row']} receipt + row {b['row']} sale of "
                f"{a['qty']:.0f} x {a['product']} ({gap:.0f}s apart) - nets to zero"
            )
            break

    print("=" * 78)
    print(f"REVERSAL PAIRS NEUTRALISED ({len(reversals)})")
    print("=" * 78)
    print("\n".join("  " + r for r in reversals) if reversals else "  none")

    sales: dict[str, list[float]] = defaultdict(list)
    receipts: dict[str, float] = defaultdict(float)
    stamps: list[datetime] = []
    for m in movements:
        if m["reversed"]:
            continue
        if m["stamp"]:
            stamps.append(m["stamp"])
        if m["kind"] == "sale":
            sales[m["product"]].append(m["qty"])
        else:
            receipts[m["product"]] += m["qty"]

    window = (max(stamps) - min(stamps)).days + 1 if stamps else 1

    plan = []
    for name in products:
        quantities = sales.get(name, [])
        units = sum(quantities)
        events = len(quantities)
        daily = units / window
        plan.append(
            {
                "product": name,
                "units_sold": int(units),
                "sale_events": events,
                "daily_demand": round(daily, 3),
                "weekly_demand": round(daily * 7, 1),
                "current_stock": int(current.get(name, 0)),
                "total_received": int(receipts.get(name, 0)),
                "mover": "fast" if daily >= 0.5 else ("slow" if units > 0 else "none"),
                "observed_minimum": 5,
            }
        )

    plan.sort(key=lambda r: (-r["units_sold"], r["product"]))
    out = pathlib.Path("demand-clean.csv")
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(plan[0].keys()))
        writer.writeheader()
        writer.writerows(plan)

    total = sum(r["units_sold"] for r in plan)
    fast = [r for r in plan if r["mover"] == "fast"]
    slow = [r for r in plan if r["mover"] == "slow"]
    none_ = [r for r in plan if r["mover"] == "none"]

    print()
    print(f"window {window} days · units sold {total} · products selling "
          f"{len(fast) + len(slow)}")
    print()
    print("MOVEMENT CLASSES")
    print(f"  fast movers (>= 0.5/day, i.e. 3+ a week) : {len(fast)}")
    print(f"  slow movers (< 0.5/day)                  : {len(slow)}")
    print(f"  no sales in the window                   : {len(none_)}")
    print()
    print("FAST MOVERS")
    print(f"  {'product':<24}{'sold':>6}{'ev':>4}{'/day':>7}{'/week':>7}{'stock':>7}")
    print("  " + "-" * 55)
    for r in fast:
        print(
            f"  {r['product'][:23]:<24}{r['units_sold']:>6}{r['sale_events']:>4}"
            f"{r['daily_demand']:>7.2f}{r['weekly_demand']:>7.1f}{r['current_stock']:>7}"
        )
    print()
    print("SLOW MOVERS")
    print(f"  {'product':<24}{'sold':>6}{'ev':>4}{'/day':>7}{'/week':>7}{'stock':>7}")
    print("  " + "-" * 55)
    for r in slow:
        print(
            f"  {r['product'][:23]:<24}{r['units_sold']:>6}{r['sale_events']:>4}"
            f"{r['daily_demand']:>7.2f}{r['weekly_demand']:>7.1f}{r['current_stock']:>7}"
        )
    print()
    print(f"  written to: {out.resolve()}")
    wb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
