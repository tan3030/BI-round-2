"""
CONFIDENCE LAYER - handles the case where evidence points in OPPOSITE
directions at the same time, which is different from "not enough data"
(that's abstention, handled in narrate.py) or "nothing moved" (normal).

This is still fully deterministic - no LLM. The question being asked isn't
"why did KPI X move" (that's detect+diagnose), it's "are the signals around
this region/decision internally consistent, or contradicting each other?"

West region scenario: marketing spend is rising (usually read as "investing
in growth") while the competitor price index is falling (usually read as
"we're facing pricing pressure / losing relative position") - in the SAME
window. A naive system might pick whichever story sounds better. This one
is required to flag the conflict instead.
"""
import pandas as pd
import numpy as np
from contract_loader import SOURCE_FILE_MAP


# Each entry: (column, "what a RISE in this usually implies")
SIGNAL_INTERPRETATION = {
    "marketing_spend_usd": ("rising", "increased investment in growth"),
    "competitor_price_index": ("falling", "competitive pricing pressure"),
}


def _zscore_latest(series: pd.Series, min_periods=3):
    if len(series) < min_periods + 1:
        return None
    history = series.iloc[:-1]
    latest = series.iloc[-1]
    std = history.std()
    if std == 0 or pd.isna(std):
        return None
    return (latest - history.mean()) / std


def check_contradictory_signals(region: str, z_threshold: float = 1.0):
    """Looks at marketing spend vs competitor price index for a region.
    If both moved meaningfully but in a way that tells conflicting stories,
    returns a low-confidence flag instead of picking a side."""
    df = pd.read_csv(SOURCE_FILE_MAP["monthly_finance.csv"])
    df = df[(df.region == region) & (df.product_line == "Core")].sort_values("month")

    mkt_z = _zscore_latest(df["marketing_spend_usd"])
    comp_z = df["competitor_price_index"].dropna()
    comp_z = _zscore_latest(comp_z) if len(comp_z) > 3 else None

    if mkt_z is None or comp_z is None:
        return {"status": "insufficient_data", "message": "Not enough history to assess signal consistency."}

    mkt_moved = abs(mkt_z) >= z_threshold
    comp_moved = abs(comp_z) >= z_threshold

    if not (mkt_moved and comp_moved):
        return {"status": "no_conflict", "message": "Signals are within normal range; no contradiction to flag."}

    # Marketing UP (growth push) while competitor index DOWN (pricing pressure) = conflicting narratives
    marketing_reads_as_growth = mkt_z > 0
    competitor_reads_as_pressure = comp_z < 0

    if marketing_reads_as_growth and competitor_reads_as_pressure:
        return {
            "status": "low_confidence_conflict",
            "region": region,
            "marketing_spend_zscore": round(mkt_z, 2),
            "competitor_price_index_zscore": round(comp_z, 2),
            "message": (
                f"Signals for {region} conflict and should not be collapsed into one confident story. "
                f"Marketing spend rose unusually (z={round(mkt_z,2)}), which normally reads as a deliberate "
                f"growth push. In the same window, the competitor price index fell unusually (z={round(comp_z,2)}), "
                f"which normally reads as competitive pricing pressure - a defensive signal, not a growth one. "
                f"These point in different directions. Rather than pick one narrative, the engine is surfacing "
                f"both and recommending a human review before acting - e.g. confirm whether the marketing spend "
                f"increase was planned (proactive) or reactive (a response to already-seen share loss)."
            ),
            "requires_human_clarification": True,
        }

    return {"status": "moved_but_consistent", "marketing_spend_zscore": round(mkt_z, 2),
            "competitor_price_index_zscore": round(comp_z, 2)}


if __name__ == "__main__":
    import json
    print("=== West region (should flag contradictory signals) ===")
    print(json.dumps(check_contradictory_signals("West"), indent=2))

    print("\n=== Northeast region (should NOT flag - no marketing/competitor anomaly there) ===")
    print(json.dumps(check_contradictory_signals("Northeast"), indent=2))
