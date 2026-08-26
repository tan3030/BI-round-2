"""
DETECTION LAYER - fully deterministic, zero LLM involvement.

For each KPI, for each region, for the most recent period:
  1. Build a trailing baseline (mean + standard deviation) from prior periods
  2. Compute a z-score for the latest value against that baseline
  3. Compare against the KPI's threshold (from the contract) - both the
     z-score AND the minimum % change must be exceeded to count as material
  4. If there isn't enough history yet, don't compute a z-score at all -
     return "insufficient_history" instead of a number

Uses DuckDB (real SQL) for the aggregation step, per the brief's ask to show
explicitly where deterministic logic / SQL is doing the work, not the LLM.
"""
import duckdb
import pandas as pd
from contract_loader import load_kpi_definitions, SOURCE_FILE_MAP


def _zscore_sql(df: pd.DataFrame, value_col: str, group_cols: list[str], period_col: str, min_periods: int):
    """
    For each group (e.g. region), compute the z-score of the LAST period's value
    against the mean/std of all PRIOR periods in that group, using SQL window
    functions - not a Python loop.
    """
    con = duckdb.connect()
    con.register("t", df)

    group_by = ", ".join(group_cols)
    partition_by = ", ".join(group_cols)

    query = f"""
    WITH ordered AS (
        SELECT *,
               ROW_NUMBER() OVER (PARTITION BY {partition_by} ORDER BY {period_col}) AS rn,
               COUNT(*) OVER (PARTITION BY {partition_by}) AS n_periods
        FROM t
    ),
    latest AS (
        SELECT * FROM ordered WHERE rn = n_periods   -- most recent period per group
    ),
    baseline AS (
        SELECT {group_by},
               AVG({value_col}) AS baseline_mean,
               STDDEV_SAMP({value_col}) AS baseline_std,
               COUNT(*) AS n_baseline_periods
        FROM ordered
        WHERE rn < n_periods   -- everything EXCEPT the latest period
        GROUP BY {group_by}
    )
    SELECT
        l.{group_by},
        l.{period_col} AS latest_period,
        l.{value_col} AS latest_value,
        b.baseline_mean,
        b.baseline_std,
        b.n_baseline_periods,
        CASE
            WHEN b.n_baseline_periods < {min_periods} THEN NULL
            WHEN b.baseline_std = 0 OR b.baseline_std IS NULL THEN NULL
            ELSE (l.{value_col} - b.baseline_mean) / b.baseline_std
        END AS z_score,
        CASE
            WHEN b.baseline_mean = 0 OR b.baseline_mean IS NULL THEN NULL
            ELSE (l.{value_col} - b.baseline_mean) / ABS(b.baseline_mean) * 100
        END AS pct_change
    FROM latest l
    JOIN baseline b USING ({group_by})
    """
    return con.execute(query).df()


def detect_anomalies():
    kpis = load_kpi_definitions()
    results = []

    for kpi in kpis:
        th = kpi["thresholds"]
        src_path = SOURCE_FILE_MAP[kpi["source_file"]]
        df = pd.read_csv(src_path)

        # Map KPI name -> (value column, period column, extra filter)
        if kpi["name"] == "Regional Revenue":
            sub = df[df["product_line"] == "Core"].copy()
            value_col, period_col = "revenue_usd", "month"
        elif kpi["name"] == "Repeat Order Rate":
            sub = df.copy()
            value_col, period_col = "repeat_order_pct", "week_start"
        elif kpi["name"] == "Support Ticket Volume":
            sub = df.copy()
            value_col, period_col = "support_tickets", "date"
        elif kpi["name"] == "Average Shipping Days":
            sub = df.copy()
            value_col, period_col = "avg_shipping_days", "date"
        elif kpi["name"] == "Product X Revenue (New Launch)":
            sub = df[df["product_line"].str.contains("Product X", na=False)].copy()
            value_col, period_col = "revenue_usd", "month"
        else:
            continue

        min_periods = th.get("min_periods", 6)
        z_out = _zscore_sql(sub, value_col, ["region"], period_col, min_periods)

        for _, row in z_out.iterrows():
            z = row["z_score"]
            pct = row["pct_change"]

            if pd.isna(z):
                status = "insufficient_history"
                material = False
            else:
                z_thresh = th.get("z")
                pct_thresh = th.get("min_pct_change") or 0
                material = (z_thresh is not None) and (abs(z) >= z_thresh) and (abs(pct) >= pct_thresh)
                status = "material_anomaly" if material else "normal"

            results.append({
                "kpi": kpi["name"],
                "region": row["region"],
                "period": str(row["latest_period"]),
                "value": round(row["latest_value"], 2) if pd.notna(row["latest_value"]) else None,
                "baseline_mean": round(row["baseline_mean"], 2) if pd.notna(row["baseline_mean"]) else None,
                "n_baseline_periods": int(row["n_baseline_periods"]),
                "z_score": round(z, 2) if pd.notna(z) else None,
                "pct_change": round(pct, 1) if pd.notna(pct) else None,
                "status": status,
            })

    return pd.DataFrame(results)


if __name__ == "__main__":
    out = detect_anomalies()
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 20)

    print("=== ALL DETECTION RESULTS ===")
    print(out.to_string(index=False))

    print("\n=== MATERIAL ANOMALIES ONLY (these are what the engine should surface) ===")
    print(out[out.status == "material_anomaly"].to_string(index=False))

    print("\n=== INSUFFICIENT HISTORY (engine should abstain on these) ===")
    print(out[out.status == "insufficient_history"].to_string(index=False))
