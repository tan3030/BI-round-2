"""
Generates 3 synthetic data sources with different grains and refresh cadences,
simulating a fragmented enterprise KPI landscape.

Sources:
  1. daily_ops.csv       - DAILY grain   - ops team system   - regions: Northeast, Midwest, South, West
  2. weekly_sales.csv    - WEEKLY grain  - sales/CRM system  - same regions
  3. monthly_finance.csv - MONTHLY grain - finance system    - same regions + a sparse-history new product line

Scenarios deliberately injected (used later for the 4 required demo cases):
  A. Multi-factor movement: Northeast revenue drop in month index 10, driven by a
     price increase + shipping-complaint spike + repeat-order decline landing in the
     same window (visible across all 3 sources -> requires reconciliation).
  B. Sparse-history KPI: "Product X" launched only 3 months ago -> no reliable baseline.
  C. Contradictory evidence: West region shows rising marketing spend (implies push for
     growth) at the same time competitor price index drops sharply (implies share pressure)
     -> signals point in different directions, should trigger lower confidence / abstention.
  D. Role-security case: operational (row-level) detail exists only at daily/weekly grain;
     finance-only aggregates exist at monthly grain -> used later for persona/RBAC demo.
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

rng = np.random.default_rng(42)

REGIONS = ["Northeast", "Midwest", "South", "West"]
TODAY = datetime(2026, 8, 22)

# ---------------------------------------------------------------------------
# 1. DAILY OPS DATA (last 120 days) - support tickets, shipping delays, price
# ---------------------------------------------------------------------------
n_days = 120
dates = [TODAY - timedelta(days=n_days - i) for i in range(n_days)]

rows = []
for region in REGIONS:
    base_tickets = {"Northeast": 8, "Midwest": 6, "South": 7, "West": 9}[region]
    base_ship_days = {"Northeast": 3.2, "Midwest": 2.8, "South": 3.0, "West": 3.5}[region]
    base_price = {"Northeast": 48.0, "Midwest": 45.0, "South": 46.5, "West": 47.0}[region]

    for i, d in enumerate(dates):
        tickets = base_tickets + rng.poisson(1.5)
        ship_days = base_ship_days + rng.normal(0, 0.3)
        price = base_price

        # --- Scenario A: Northeast, injected in last ~30 days (price hike + shipping spike) ---
        if region == "Northeast" and i >= n_days - 30:
            price = base_price * 1.08          # Oct 1st-style price increase
            tickets = base_tickets + rng.poisson(1.5) + 6   # shipping complaint spike
            ship_days = base_ship_days + rng.normal(1.8, 0.4)

        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "region": region,
            "support_tickets": max(0, int(tickets)),
            "avg_shipping_days": round(max(0.5, ship_days), 2),
            "unit_price_usd": round(price, 2),
        })

daily_ops = pd.DataFrame(rows)
daily_ops.to_csv("/home/claude/round2/data/daily_ops.csv", index=False)

# ---------------------------------------------------------------------------
# 2. WEEKLY SALES DATA (last 26 weeks) - repeat order %, new customers, units
# ---------------------------------------------------------------------------
n_weeks = 26
week_starts = [TODAY - timedelta(weeks=n_weeks - i) for i in range(n_weeks)]

rows = []
for region in REGIONS:
    base_repeat_pct = {"Northeast": 34.0, "Midwest": 31.0, "South": 32.5, "West": 29.0}[region]
    base_units = {"Northeast": 2100, "Midwest": 1800, "South": 1950, "West": 2400}[region]
    base_new_cust = {"Northeast": 140, "Midwest": 120, "South": 130, "West": 160}[region]

    for i, wk in enumerate(week_starts):
        repeat_pct = base_repeat_pct + rng.normal(0, 1.2)
        units = base_units + rng.normal(0, 80)
        new_cust = base_new_cust + rng.normal(0, 15)

        # --- Scenario A: Northeast repeat-order decline in the same recent window ---
        if region == "Northeast" and i >= n_weeks - 7:
            repeat_pct = base_repeat_pct - rng.uniform(6, 10)
            units = base_units - rng.uniform(150, 300)

        rows.append({
            "week_start": wk.strftime("%Y-%m-%d"),
            "region": region,
            "units_sold": max(0, round(units)),
            "repeat_order_pct": round(max(0, repeat_pct), 1),
            "new_customers": max(0, round(new_cust)),
        })

weekly_sales = pd.DataFrame(rows)
weekly_sales.to_csv("/home/claude/round2/data/weekly_sales.csv", index=False)

# ---------------------------------------------------------------------------
# 3. MONTHLY FINANCE DATA (last 18 months) - revenue, marketing spend,
#    competitor price index + a sparse-history new product line
# ---------------------------------------------------------------------------
n_months = 18
current_month_start = TODAY.replace(day=1)
month_starts = pd.date_range(end=current_month_start, periods=n_months, freq="MS")
assert len(month_starts) == n_months, f"expected {n_months} months, got {len(month_starts)}"

rows = []
for region in REGIONS:
    base_revenue = {"Northeast": 980_000, "Midwest": 810_000, "South": 860_000, "West": 1_050_000}[region]
    base_marketing = {"Northeast": 60_000, "Midwest": 48_000, "South": 52_000, "West": 70_000}[region]
    base_competitor_idx = 100.0

    for i, m in enumerate(month_starts):
        revenue = base_revenue + rng.normal(0, 15_000)
        marketing = base_marketing + rng.normal(0, 3_000)
        competitor_idx = base_competitor_idx + rng.normal(0, 1.5)

        # --- Scenario A: Northeast revenue drop in the final month (~10%) ---
        if region == "Northeast" and i == n_months - 1:
            revenue = base_revenue * 0.90

        # --- Scenario C: West - contradictory evidence in last 2 months ---
        # marketing spend rising (growth push) while competitor price index falls sharply
        # (competitive pressure) - signals point in opposite directions
        if region == "West" and i >= n_months - 2:
            marketing = base_marketing * 1.25
            competitor_idx = base_competitor_idx - rng.uniform(6, 9)

        rows.append({
            "month": m.strftime("%Y-%m-01"),
            "region": region,
            "product_line": "Core",
            "revenue_usd": round(revenue),
            "marketing_spend_usd": round(marketing),
            "competitor_price_index": round(competitor_idx, 1),
        })

# --- Scenario B: sparse-history KPI - "Product X" launched only 3 months ago, Northeast only ---
launch_months = month_starts[-3:]
base_px_revenue = 40_000
for i, m in enumerate(launch_months):
    rows.append({
        "month": m.strftime("%Y-%m-01"),
        "region": "Northeast",
        "product_line": "Product X (New Launch)",
        "revenue_usd": round(base_px_revenue * (1 + i * 0.3) + rng.normal(0, 2000)),
        "marketing_spend_usd": round(15_000 + rng.normal(0, 1000)),
        "competitor_price_index": None,  # no comparable competitor product yet
    })

monthly_finance = pd.DataFrame(rows)
monthly_finance.to_csv("/home/claude/round2/data/monthly_finance.csv", index=False)

print("Generated:")
print(f"  daily_ops.csv       : {daily_ops.shape[0]} rows, grain=daily,   sources: ops system")
print(f"  weekly_sales.csv    : {weekly_sales.shape[0]} rows, grain=weekly,  sources: CRM/sales system")
print(f"  monthly_finance.csv : {monthly_finance.shape[0]} rows, grain=monthly, sources: finance system")
print("\nScenarios embedded:")
print("  A. Multi-factor movement -> Northeast, most recent period, across all 3 sources")
print("  B. Sparse-history KPI    -> 'Product X (New Launch)' in Northeast, 3 months only")
print("  C. Contradictory signals -> West region, last 2 months (marketing up, competitor idx down)")
print("  D. Role-security surface -> operational detail (daily/weekly) vs finance aggregates (monthly)")
