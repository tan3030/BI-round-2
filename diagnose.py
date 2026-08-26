"""
DIAGNOSIS LAYER - still fully deterministic, zero LLM involvement.

Given a flagged anomaly (e.g. Northeast Regional Revenue, latest month),
rank candidate "drivers" (other KPIs) by TWO combined signals:

  1. Historical correlation - across all available months, does this
     candidate's monthly pattern generally move together with revenue?
     (Pearson correlation coefficient, -1 to +1)

  2. Concurrent movement - did this candidate ALSO move unusually in the
     SAME month as the anomaly? (its own z-score that month)

A candidate that both (a) historically tracks revenue AND (b) also moved
unusually this month is stronger circumstantial evidence than a candidate
that only satisfies one of the two.

IMPORTANT: this ranks CORRELATION, not causation. The output is deliberately
worded as "correlated signal", never "caused by" - see quiz answer from
earlier. The LLM layer (Day 3) is not allowed to upgrade this wording either.
"""
import duckdb
import pandas as pd
import numpy as np
from contract_loader import SOURCE_FILE_MAP


def _monthly_aggregate_candidates(region: str):
    """Roll daily + weekly candidate signals up to monthly grain so they can be
    compared against monthly revenue on equal footing."""
    daily = pd.read_csv(SOURCE_FILE_MAP["daily_ops.csv"])
    weekly = pd.read_csv(SOURCE_FILE_MAP["weekly_sales.csv"])

    daily = daily[daily.region == region].copy()
    daily["month"] = pd.to_datetime(daily["date"]).dt.to_period("M").dt.to_timestamp()
    daily_m = daily.groupby("month").agg(
        avg_support_tickets=("support_tickets", "mean"),
        avg_shipping_days=("avg_shipping_days", "mean"),
    ).reset_index()

    weekly = weekly[weekly.region == region].copy()
    weekly["month"] = pd.to_datetime(weekly["week_start"]).dt.to_period("M").dt.to_timestamp()
    weekly_m = weekly.groupby("month").agg(
        avg_repeat_order_pct=("repeat_order_pct", "mean"),
        total_units_sold=("units_sold", "sum"),
    ).reset_index()

    return daily_m.merge(weekly_m, on="month", how="outer").sort_values("month")


def diagnose(region: str, kpi_source_file: str, kpi_value_col: str, kpi_filter=None):
    """Returns a ranked DataFrame of candidate drivers for the latest anomaly
    in the target KPI, for the given region."""
    target_df = pd.read_csv(SOURCE_FILE_MAP[kpi_source_file])
    target_df = target_df[target_df.region == region]
    if kpi_filter is not None:
        target_df = kpi_filter(target_df)
    target_df["month"] = pd.to_datetime(target_df["month"]).dt.to_period("M").dt.to_timestamp()
    target_df = target_df.groupby("month")[kpi_value_col].sum().reset_index()
    target_df = target_df.rename(columns={kpi_value_col: "target_value"})

    candidates = _monthly_aggregate_candidates(region)
    merged = target_df.merge(candidates, on="month", how="left").sort_values("month")

    candidate_cols = ["avg_support_tickets", "avg_shipping_days", "avg_repeat_order_pct", "total_units_sold"]
    rows = []
    latest = merged.iloc[-1]
    history = merged.iloc[:-1]

    for col in candidate_cols:
        # 1. historical correlation (needs at least 3 overlapping points)
        valid = history.dropna(subset=[col, "target_value"])
        if len(valid) >= 3:
            corr = np.corrcoef(valid[col], valid["target_value"])[0, 1]
        else:
            corr = np.nan

        # 2. concurrent movement: candidate's own z-score in the latest month
        base_mean = history[col].mean()
        base_std = history[col].std()
        if base_std and not np.isnan(base_std) and base_std > 0:
            z = (latest[col] - base_mean) / base_std
        else:
            z = np.nan

        # combined evidence score: magnitude of correlation x magnitude of concurrent move
        # (nan-safe: treat missing pieces as 0 contribution, but flag them as such)
        corr_component = abs(corr) if not np.isnan(corr) else 0
        z_component = min(abs(z), 5) / 5 if not np.isnan(z) else 0  # cap/normalize to 0-1
        evidence_score = round(corr_component * 0.5 + z_component * 0.5, 3)

        rows.append({
            "candidate_driver": col,
            "historical_correlation": round(corr, 2) if not np.isnan(corr) else None,
            "moved_this_month_zscore": round(z, 2) if not np.isnan(z) else None,
            "latest_value": round(latest[col], 2) if pd.notna(latest[col]) else None,
            "evidence_score": evidence_score,
        })

    ranked = pd.DataFrame(rows).sort_values("evidence_score", ascending=False).reset_index(drop=True)
    ranked.insert(0, "rank", ranked.index + 1)
    return ranked


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)

    print("=== DIAGNOSIS: Northeast Regional Revenue drop (latest month) ===\n")
    result = diagnose(
        region="Northeast",
        kpi_source_file="monthly_finance.csv",
        kpi_value_col="revenue_usd",
        kpi_filter=lambda df: df[df.product_line == "Core"],
    )
    print(result.to_string(index=False))

    print("\nPlain-English ranking:")
    for _, r in result.iterrows():
        label = r["candidate_driver"].replace("_", " ").replace("avg ", "").title()
        print(f"  #{r['rank']}: {label}  "
              f"(historical correlation: {r['historical_correlation']}, "
              f"moved this month: z={r['moved_this_month_zscore']})")
