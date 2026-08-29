"""
NARRATIVE LAYER - this is the ONLY file in the whole engine that calls an LLM.
Everything upstream (detect.py, diagnose.py) is pure math/SQL - the LLM never
sees raw data and never decides what's "true." It only receives already-
verified numbers and turns them into a sentence a human can read.

Also handles:
  - Persona access control (reads rules from contract_loader, not hardcoded)
  - Abstention (if detect.py says "insufficient_history", we NEVER call the
    LLM at all - the abstention message is deterministic, not AI-generated)
  - Telemetry (latency, token usage, estimated cost) on every call
"""
import os
import time
import json
import uuid
from datetime import datetime, timezone

from contract_loader import load_kpi_definitions, load_access_rules
from detect import detect_anomalies
from diagnose import diagnose

# Rough Claude pricing for cost estimation (USD per 1M tokens) - update if pricing changes.
# This is intentionally a simple constant, not a live pricing lookup - documented as such.
PRICE_PER_1M_INPUT = 3.00
PRICE_PER_1M_OUTPUT = 15.00

_PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TELEMETRY_LOG_PATH = os.path.join(_PROJECT_ROOT, "telemetry_log.jsonl")
FEEDBACK_LOG_PATH = os.path.join(_PROJECT_ROOT, "feedback_log.jsonl")


def _extract_region_code(default_region_text: str):
    """The Excel's Default Region cell is human-readable text (e.g. 'Northeast (example)'
    or 'All regions (none set as default)'), not a clean code. Pull out just the real
    region name if one is present, otherwise return None (meaning: no region restriction)."""
    if not default_region_text:
        return None
    known_regions = ["Northeast", "Midwest", "South", "West"]
    for r in known_regions:
        if r in default_region_text:
            return r
    return None  # e.g. "All regions..." -> no restriction


def check_access(persona_role: str, kpi_name: str, region: str, personas: list, kpis: list):
    """Deterministic access check - runs BEFORE any LLM call.
    Returns (allowed: bool, reason: str)."""
    persona = next((p for p in personas if p["role"] == persona_role), None)
    kpi = next((k for k in kpis if k["name"] == kpi_name), None)
    if persona is None or kpi is None:
        return False, "Unknown persona or KPI."

    # Column/domain-level check: is this KPI in the persona's allowed access text?
    if persona_role not in kpi["access"]:
        return False, f"'{persona_role}' is not authorized to view '{kpi_name}' (operational detail restricted to Regional Manager per KPI Definitions sheet)."

    # Row-level check: Regional Manager restricted to their own region
    restricted_region = _extract_region_code(persona.get("default_region"))
    if persona_role == "Regional Manager" and restricted_region and region != restricted_region:
        return False, f"Regional Manager is scoped to {restricted_region} only, cannot view {region}."

    return True, "Authorized."


def build_prompt(kpi_name, region, detection_row, diagnosis_df, persona):
    evidence_lines = []
    for _, r in diagnosis_df.head(3).iterrows():
        evidence_lines.append(
            f"- {r['candidate_driver'].replace('_',' ')}: historical correlation with this KPI = {r['historical_correlation']}, "
            f"moved this month at z-score = {r['moved_this_month_zscore']}, evidence score = {r['evidence_score']}"
        )
    evidence_block = "\n".join(evidence_lines)

    system_prompt = f"""You are a business analyst assistant. You will be given ALREADY-VERIFIED
statistical evidence about a KPI movement. Your job is to (a) explain it in plain
language, and (b) propose structured recommended actions. Rules you MUST follow:

1. NEVER claim causation. Only say a factor is a "correlated signal" or "candidate driver,"
   never that it "caused" the change.
2. Cite the actual numbers given to you (z-score, % change, correlation) - do not invent numbers.
3. Match this tone: {persona['tone']}
4. If the evidence is weak or conflicting, say so explicitly and lower your confidence framing -
   do not force a confident-sounding answer.
5. Respond with ONLY valid JSON (no markdown fences, no commentary outside the JSON), in
   exactly this shape:

{{
  "narrative": "<plain-language explanation, under 120 words>",
  "actions": [
    {{
      "driver": "<which candidate driver this action addresses>",
      "controllable_lever": "<the actual business lever that can be pulled>",
      "action": "<the concrete recommended action>",
      "expected_impact": "<plausible, qualitative - do not invent precise numbers>",
      "owner": "<which role/team should own this>",
      "confidence": "<low / medium / high, based on the evidence strength given>",
      "monitoring_plan": "<how to check if the action worked>"
    }}
  ]
}}

Include 2-3 actions, ranked by priority (most important first).
"""

    user_prompt = f"""KPI: {kpi_name}
Region: {region}
Latest value: {detection_row['value']} (baseline average: {detection_row['baseline_mean']})
Change: {detection_row['pct_change']}%, z-score: {detection_row['z_score']}

Top candidate drivers (ranked, already computed - do not recalculate):
{evidence_block}

Persona this is for: {persona['role']}
"""
    return system_prompt, user_prompt


def _parse_llm_json(raw_text: str):
    """Parses the LLM's JSON response. Falls back gracefully if the model
    didn't follow the format exactly - never crashes the app over formatting."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        parsed = json.loads(cleaned)
        return parsed.get("narrative", raw_text), parsed.get("actions", [])
    except (json.JSONDecodeError, AttributeError):
        return raw_text, []


def _mock_structured_response(kpi_name, region, diag_df):
    top = diag_df.iloc[0]
    top2 = diag_df.iloc[1] if len(diag_df) > 1 else top
    narrative = (
        f"[DRY RUN - no Gemini_API_KEY set] {kpi_name} in {region} moved outside its normal range. "
        f"The top correlated signal is '{top['candidate_driver'].replace('_',' ')}' "
        f"(correlation={top['historical_correlation']}, this-month z={top['moved_this_month_zscore']}). "
        f"Set Gemini_API_KEY to get a real, live-generated explanation here instead of this placeholder."
    )
    actions = [
        {
            "driver": top["candidate_driver"].replace("_", " "),
            "controllable_lever": "Operational response time / service levels",
            "action": f"Investigate root cause of {top['candidate_driver'].replace('_',' ')} in {region} this cycle.",
            "expected_impact": "Qualitative - reduce recurrence risk next period",
            "owner": "Regional Ops Lead",
            "confidence": "medium (dry-run placeholder)",
            "monitoring_plan": f"Re-check {top['candidate_driver'].replace('_',' ')} z-score next reporting period",
        },
        {
            "driver": top2["candidate_driver"].replace("_", " "),
            "controllable_lever": "Customer communication / retention outreach",
            "action": f"Review {top2['candidate_driver'].replace('_',' ')} trend with account team.",
            "expected_impact": "Qualitative - stabilize secondary signal",
            "owner": "Regional Manager",
            "confidence": "low (dry-run placeholder)",
            "monitoring_plan": "Track weekly until back within normal range",
        },
    ]
    return narrative, actions


def log_feedback(run_id: str, kpi: str, region: str, persona: str, rating: str, comment: str = ""):
    """Appends a feedback record. This IS the feedback-loop mechanism required
    by the brief (objective #7) - deliberately simple: it doesn't retrain
    anything automatically (out of scope for a prototype), but it captures
    structured, attributable feedback that a real system would use to
    recalibrate thresholds or flag narratives for review."""
    entry = {
        "run_id": run_id, "kpi": kpi, "region": region, "persona": persona,
        "rating": rating, "comment": comment,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(FEEDBACK_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def load_feedback_log(limit: int = 20):
    if not os.path.exists(FEEDBACK_LOG_PATH):
        return []
    with open(FEEDBACK_LOG_PATH) as f:
        lines = f.readlines()
    return [json.loads(l) for l in lines[-limit:]]


def _log_telemetry(entry: dict):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(TELEMETRY_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def generate_narrative(kpi_name: str, region: str, persona_role: str, dry_run: bool = False):
    kpis = load_kpi_definitions()
    personas = load_access_rules()

    # 1. ACCESS CHECK - deterministic, before anything else
    allowed, reason = check_access(persona_role, kpi_name, region, personas, kpis)
    if not allowed:
        result = {"status": "access_denied", "message": reason}
        _log_telemetry({"kpi": kpi_name, "region": region, "persona": persona_role,
                         "step": "access_check", "result": "denied", "llm_call": False})
        return result

    # 2. DETECTION - has this KPI even moved unusually?
    det = detect_anomalies()
    row = det[(det.kpi == kpi_name) & (det.region == region)]
    if row.empty:
        return {"status": "no_data", "message": "No detection result found for this KPI/region."}
    row = row.iloc[0]

    # 3. ABSTENTION - deterministic, NO LLM CALL if insufficient history
    if row["status"] == "insufficient_history":
        msg = (f"{kpi_name} in {region} only has {row['n_baseline_periods']} periods of history "
               f"(needs more before a reliable baseline can be established). "
               f"The engine is abstaining from judging this as unusual or not, rather than guessing.")
        _log_telemetry({"kpi": kpi_name, "region": region, "persona": persona_role,
                         "step": "abstention", "result": "insufficient_history", "llm_call": False})
        return {"status": "abstained", "message": msg}

    if row["status"] == "normal":
        return {"status": "normal", "message": f"{kpi_name} in {region} is within normal range (z={row['z_score']}). No explanation needed."}

    # 4. DIAGNOSIS - rank candidate drivers
    kpi_def = next(k for k in kpis if k["name"] == kpi_name)
    source_map = {"Regional Revenue": ("monthly_finance.csv", "revenue_usd", lambda df: df[df.product_line == "Core"])}
    src_file, val_col, filt = source_map.get(kpi_name, (kpi_def["source_file"], None, None))
    diag = diagnose(region=region, kpi_source_file=src_file, kpi_value_col=val_col, kpi_filter=filt)

    persona = next(p for p in personas if p["role"] == persona_role)
    system_prompt, user_prompt = build_prompt(kpi_name, region, row, diag, persona)

    # 5. LLM CALL - the only step that uses an LLM
    t0 = time.time()
    if dry_run or not os.environ.get("GEMINI_API_KEY"):
        # Dry-run mode: lets us test/demo the full pipeline - including the structured actions table - without a live API key.
        narrative_text, actions = _mock_structured_response(kpi_name, region, diag)
        usage = {"input_tokens": len(system_prompt.split()) + len(user_prompt.split()), "output_tokens": 0}
    else:
      from google import genai
      client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
      resp = client.models.generate_content(model="gemini-3.7-flash",contents=user_prompt,config={
        "system_instruction": system_prompt,
        "max_output_tokens": 500,
    },
)

    raw_text = resp.text
    narrative_text, actions = _parse_llm_json(raw_text)
    usage = {
    "input_tokens": resp.usage_metadata.prompt_token_count,
    "output_tokens": resp.usage_metadata.candidates_token_count,
}
    est_cost = round(
        usage["input_tokens"] / 1_000_000 * PRICE_PER_1M_INPUT +
        usage["output_tokens"] / 1_000_000 * PRICE_PER_1M_OUTPUT, 6
    )

    run_id = str(uuid.uuid4())[:8]
    telemetry = {
        "run_id": run_id, "kpi": kpi_name, "region": region, "persona": persona_role,
        "step": "llm_narrative", "llm_call": True,
        "latency_ms": latency_ms, "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"], "estimated_cost_usd": est_cost,
    }
    _log_telemetry(telemetry)

    return {
        "status": "material_anomaly",
        "run_id": run_id,
        "narrative": narrative_text,
        "actions": actions,
        "evidence": diag.to_dict("records"),
        "confidence_inputs": {"z_score": row["z_score"], "pct_change": row["pct_change"]},
        "telemetry": telemetry,
        "prompt_used": {"system": system_prompt, "user": user_prompt},  # kept for transparency/demo
    }


if __name__ == "__main__":
    print("=== TEST 1: Regional Manager asking about their own region's revenue (should work) ===")
    r1 = generate_narrative("Regional Revenue", "Northeast", "Regional Manager", dry_run=True)
    print(json.dumps({k: v for k, v in r1.items() if k != "prompt_used"}, indent=2, default=str))

    print("\n=== TEST 2: Finance VP asking about Support Ticket Volume (should be DENIED - column security) ===")
    r2 = generate_narrative("Support Ticket Volume", "Northeast", "Finance VP", dry_run=True)
    print(json.dumps(r2, indent=2, default=str))

    print("\n=== TEST 3: Anyone asking about Product X (should ABSTAIN - insufficient history) ===")
    r3 = generate_narrative("Product X Revenue (New Launch)", "Northeast", "Finance VP", dry_run=True)
    print(json.dumps(r3, indent=2, default=str))

    print("\n=== TEST 4: Regional Manager asking about a DIFFERENT region (should be DENIED - row security) ===")
    r4 = generate_narrative("Regional Revenue", "West", "Regional Manager", dry_run=True)
    print(json.dumps(r4, indent=2, default=str))
