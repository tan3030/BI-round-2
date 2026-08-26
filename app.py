"""
STREAMLIT UI - the only file meant to be run directly by a human.
Wraps detect -> diagnose -> confidence -> narrate into one clickable app.

Design goal: make the LLM-vs-non-LLM split explicitly.
Every section is tagged so a judge can see exactly which step used AI and
which step was pure math/SQL, per the brief's ask for this.
"""
import sys
import os
import json
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from contract_loader import load_kpi_definitions, load_access_rules
from detect import detect_anomalies
from narrate import generate_narrative, log_feedback, load_feedback_log
from confidence import check_contradictory_signals

st.set_page_config(page_title="KPI Intelligence-to-Action Engine", layout="wide")

NO_LLM_BADGE = "🟩 NO LLM — deterministic math/SQL"
LLM_BADGE = "🟪 LLM — narrative generation only"

st.title("KPI Intelligence-to-Action Engine")
st.caption("BusinessIntelligence.ai — Round 2 Prototype")

# ---------------------------------------------------------------------------
# Sidebar: scenario picker
# ---------------------------------------------------------------------------
st.sidebar.header("Pick a scenario")

kpis = load_kpi_definitions()
personas = load_access_rules()
kpi_names = [k["name"] for k in kpis]
persona_names = [p["role"] for p in personas]
regions = ["Northeast", "Midwest", "South", "West"]

SCENARIO_PRESETS = {
    "① Northeast Revenue Drop (multi-factor)": {"kpi": "Regional Revenue", "region": "Northeast", "persona": "Regional Manager"},
    "② Product X (sparse history → abstain)": {"kpi": "Product X Revenue (New Launch)", "region": "Northeast", "persona": "Finance VP"},
    "③ Finance VP asks for Support Tickets (should be denied)": {"kpi": "Support Ticket Volume", "region": "Northeast", "persona": "Finance VP"},
    "④ Regional Manager asks about another region (should be denied)": {"kpi": "Regional Revenue", "region": "West", "persona": "Regional Manager"},
    "⑤ West contradictory signals (low confidence)": {"kpi": "__contradiction__", "region": "West", "persona": "Finance VP"},
    "Custom": None,
}

choice = st.sidebar.selectbox("Preset scenarios (map to the brief's required demo cases)", list(SCENARIO_PRESETS.keys()))

if choice != "Custom":
    preset = SCENARIO_PRESETS[choice]
    sel_kpi = preset["kpi"]
    sel_region = preset["region"]
    sel_persona = preset["persona"]
    st.sidebar.selectbox("KPI", kpi_names, index=kpi_names.index(sel_kpi) if sel_kpi in kpi_names else 0, disabled=True)
    st.sidebar.selectbox("Region", regions, index=regions.index(sel_region), disabled=True)
    st.sidebar.selectbox("Persona", persona_names, index=persona_names.index(sel_persona), disabled=True)
else:
    sel_kpi = st.sidebar.selectbox("KPI", kpi_names)
    sel_region = st.sidebar.selectbox("Region", regions)
    sel_persona = st.sidebar.selectbox("Persona", persona_names)

run = st.sidebar.button("Run engine", type="primary")

api_key_present = bool(os.environ.get("ANTHROPIC_API_KEY"))
if not api_key_present:
    st.sidebar.warning("No ANTHROPIC_API_KEY set — narrative step will run in DRY RUN mode (shows the prompt, doesn't call the LLM).")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if run:
    st.divider()
    st.subheader(f"Query: {sel_persona} — {sel_kpi} — {sel_region}")

    # ---- Special path: contradiction scenario ----
    if sel_kpi == "__contradiction__":
        st.markdown(f"**Step: Signal Consistency Check**  {NO_LLM_BADGE}")
        result = check_contradictory_signals(sel_region)
        if result["status"] == "low_confidence_conflict":
            st.error("⚠️ Conflicting signals detected — engine is NOT forcing a single confident answer.")
            st.write(result["message"])
            c1, c2 = st.columns(2)
            c1.metric("Marketing Spend z-score", result["marketing_spend_zscore"])
            c2.metric("Competitor Price Index z-score", result["competitor_price_index_zscore"])
            st.info("Requires human clarification before acting.")
        else:
            st.success(f"No contradiction flagged: {result.get('message', result['status'])}")
        st.stop()

    # ---- Standard path: access -> detect -> diagnose -> narrate ----
    with st.expander(f"Step 1: Access Control Check   {NO_LLM_BADGE}", expanded=True):
        st.write(f"Checking whether **{sel_persona}** is allowed to view **{sel_kpi}** in **{sel_region}**, "
                 f"per the rules in `Semantic_Contract.xlsx`.")

    result = generate_narrative(sel_kpi, sel_region, sel_persona, dry_run=not api_key_present)

    if result["status"] == "access_denied":
        st.error(f"🔒 ACCESS DENIED: {result['message']}")
        st.stop()

    if result["status"] == "abstained":
        st.warning(f"🚫 ENGINE ABSTAINED: {result['message']}")
        st.caption("No LLM call was made — abstention is a deterministic rule, not an AI judgment call.")
        st.stop()

    if result["status"] == "normal":
        st.info(result["message"])
        st.stop()

    if result["status"] == "no_data":
        st.error(result["message"])
        st.stop()

    # material_anomaly path
    with st.expander(f"Step 2 & 3: Detection + Diagnosis   {NO_LLM_BADGE}", expanded=True):
        c1, c2 = st.columns(2)
        c1.metric("z-score", result["confidence_inputs"]["z_score"])
        c2.metric("% change", f"{result['confidence_inputs']['pct_change']}%")
        st.write("Ranked candidate drivers (correlation + concurrent movement — not causation):")
        st.dataframe(pd.DataFrame(result["evidence"]), use_container_width=True, hide_index=True)

    with st.expander(f"Step 4: Narrative Generation   {LLM_BADGE}", expanded=True):
        st.write(result["narrative"])
        with st.popover("Show exact prompt sent to the LLM"):
            st.code(result["prompt_used"]["system"], language="text")
            st.code(result["prompt_used"]["user"], language="text")

    if result.get("actions"):
        with st.expander("Recommended Actions (structured)", expanded=True):
            st.caption("Format: driver → controllable lever → action → expected impact → owner → confidence → monitoring plan")
            actions_df = pd.DataFrame(result["actions"])
            actions_df.columns = [c.replace("_", " ").title() for c in actions_df.columns]
            st.dataframe(actions_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Telemetry (this run)")
    t = result["telemetry"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latency (ms)", t["latency_ms"])
    c2.metric("Input tokens", t["input_tokens"])
    c3.metric("Output tokens", t["output_tokens"])
    c4.metric("Est. cost (USD)", f"${t['estimated_cost_usd']}")

    # -------------------------------------------------------------------
    # Feedback loop (objective #7: mechanism to learn from user feedback)
    # -------------------------------------------------------------------
    st.divider()
    st.subheader("Was this explanation useful?")
    run_id = result["run_id"]
    fb_key = f"feedback_given_{run_id}"

    if st.session_state.get(fb_key):
        st.success("Thanks — your feedback was logged.")
    else:
        fc1, fc2, fc3 = st.columns([1, 1, 4])
        if fc1.button("👍 Useful", key=f"up_{run_id}"):
            log_feedback(run_id, sel_kpi, sel_region, sel_persona, "up")
            st.session_state[fb_key] = True
            st.rerun()
        if fc2.button("👎 Not useful", key=f"down_{run_id}"):
            log_feedback(run_id, sel_kpi, sel_region, sel_persona, "down")
            st.session_state[fb_key] = True
            st.rerun()

    with st.expander("View feedback log (recent)"):
        fb_log = load_feedback_log()
        if fb_log:
            st.dataframe(pd.DataFrame(fb_log), use_container_width=True, hide_index=True)
        else:
            st.caption("No feedback logged yet.")

else:
    st.info("← Pick a scenario in the sidebar and click **Run engine**.")
    st.markdown("""
    ### What this prototype demonstrates
    | Required capability | Where to see it |
    |---|---|
    | Detect + prioritize material KPI movements | Scenario ① |
    | Reconcile data across sources with different grains | Scenario ① (daily+weekly+monthly combined) |
    | Rank explanatory drivers | Scenario ① evidence table |
    | Persona-specific narratives | Switch persona on any scenario |
    | Abstain when evidence is insufficient | Scenario ② |
    | Role-based access control | Scenarios ③ and ④ |
    | Low-confidence / contradictory evidence handling | Scenario ⑤ |
    | Explicit LLM vs non-LLM breakdown | Every step is tagged above |
    | Runtime telemetry (latency, tokens, cost) | Bottom of any successful run |
    | Structured actions (driver → lever → action → impact → owner → confidence → monitoring) | "Recommended Actions" table on any material anomaly |
    | Feedback loop mechanism | 👍/👎 buttons + persisted feedback log, bottom of any successful run |
    """)

st.divider()
with st.expander("View the semantic contract this engine is reading from"):
    contract_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Semantic_Contract.xlsx")
    df = pd.read_excel(contract_path, sheet_name="KPI Definitions")
    st.dataframe(df, use_container_width=True, hide_index=True)
