# KPI Intelligence-to-Action Engine
**Team Mars - Accenture Innovation Challenge 2026, Round 2 Prototype**
Problem Statement: BusinessIntelligence.ai

🔗 **Live demo:** [[Streamlit app link](https://uzgbzzs3m8hbqbh9nrcdmn.streamlit.app/)]
🎥 **Demo video:** [add your video link here]


## What this is: 
A working prototype of an AI layer that sits on top of fragmented business
dashboards, detects when a KPI has moved in a way that actually matters,
explains *why* in plain language, and knows when not to answer.

Built solo, every design decision below was made to satisfy the Round 2 brief's
explicit requirement: **the LLM is never the source of quantitative truth** —
detection, diagnosis, and access control are all deterministic math/SQL, and
the LLM is used for exactly one job: turning verified evidence into a sentence
a human can read.


## Architecture:
┌─────────────────────┐
│  Semantic_Contract  │   Human-editable Excel file: KPI definitions,
│      .xlsx          │   calculations, thresholds, lineage, access rules
└──────────┬──────────┘
           │  (read at runtime by every module below — nothing hardcoded)
           ▼
┌────────────────────────────────────────────────────────────────┐
│  3 DATA SOURCES - different grains/refresh cadences (fragmented│
│  systems)                                                      │
│    • daily_ops.csv       (daily,   ops system)                 │
│    • weekly_sales.csv    (weekly)                              │
│    • monthly_finance.csv (monthly, finance system)             │
└──────────┬─────────────────────────────────────────────────────┘
           ▼
   ┌───────────────┐     🟩 NO LLM
   │  detect.py    │     Z-score anomaly detection via DuckDB SQL.
   └───────┬───────┘     Flags material moves; abstains if history < threshold.
           ▼
   ┌───────────────┐     🟩 NO LLM
   │  diagnose.py  │     Ranks candidate drivers by historical correlation +
   └───────┬───────┘     concurrent movement. Reports correlation not causation.
           ▼
   ┌───────────────┐     🟩 NO LLM
   │ confidence.py │     Detects contradictory signals (e.g. marketing up +
   └───────┬───────┘     competitor pricing down) → flags for human review.
           ▼
   ┌───────────────┐     🟩 NO LLM
   │  narrate.py   │     Access control check (persona + region) : runs
   │  (access +    │     BEFORE any LLM call. Denies/abstains deterministically.
   │  abstention)  │
   └───────┬───────┘
           ▼
   ┌───────────────┐     🟪 LLM (Claude, via Anthropic API)
   │  narrate.py   │     ONLY step that touches an LLM. Receives pre-verified
   │  (narrative)  │     numbers, forbidden from claiming causation, must cite
   └───────┬───────┘     evidence, tone adapted per persona. Logs latency/
           │             tokens/cost on every call.
           ▼
   ┌───────────────┐
   │   app.py      │     Streamlit UI. Every pipeline step tagged 🟩/🟪 on
   │  (Streamlit)  │     screen. 5 preset scenarios map directly to the
   └───────────────┘     brief's required demo cases.
   

## The 8 minimum-expectation items, and their location:

## Requirement | Where it's demonstrated
1. 3-5 connected KPIs across 2-3 sources, different grains | `Semantic_Contract.xlsx` → KPI Definitions (5 KPIs, 3 sources: daily/weekly/monthly)
2. Lightweight KPI/semantic contract | `Semantic_Contract.xlsx` - definitions, calculations, thresholds, lineage, access, in one governed file
3. ≥2 personas, different narratives/actions | Regional Manager vs Finance VP - different visibility and different tone (`narrate.py`)
4. One multifactor KPI movement | Scenario ① - Northeast revenue drop, driven by 3 correlated signals across all 3 sources
5. One low-confidence/contradictory scenario | Scenario ⑤ - West region, marketing spend up vs competitor pricing down (`confidence.py`)
6. One sparse-history/new KPI scenario | Scenario ② - Product X, only 3 months of data, engine abstains rather than guesses
7. One role-based security scenario | Scenarios ③ and ④ - column-level (ops data hidden from Finance VP) and row-level (region-locked Regional Manager) denial
8. Evidence: source freshness, method, contribution, confidence, lineage | Every scenario in the UI shows z-scores, correlation, evidence scores, and cites its source file/grain

Plus, beyond the 8 minimum items:

Extra capabilities:
1. Structured recommended actions, in the exact shape the brief specifies: **driver → controllable lever → action → expected impact → owner → confidence → monitoring plan** | `narrate.py` forces this JSON shape from the LLM; rendered as a table in the UI
2. Feedback loop (**objective #7** — a required capability, not just an "explore area") | 👍/👎 buttons under every narrative, logged to `feedback_log.jsonl` with the run ID, KPI, region, and persona attached
3. Explicit LLM-vs-non-LLM tagging on every UI step | 🟩/🟪 badges throughout `app.py`
4. Runtime telemetry that doubles as an audit trail | Every LLM call logs latency, token usage, estimated cost, and a `run_id` to `telemetry_log.jsonl` - the same record that traces "what the AI said" also traces "who asked, when, and what it cost," which is what an audit trail needs to be useful for compliance review


## Why these specific design choices:

- **Excel, not a database for the semantic contract** - chosen deliberately so the governance rules stay human-editable and           auditable without needing to touch code. Real KPI-governance tools (data catalogs) work the same way.
- **DuckDB for detection** - lets the deterministic layer use real SQL, not just pandas loops, matching the brief's ask to            show explicit use of SQL/deterministic logic distinct from the LLM layer.
- **Correlation, never causation, in diagnosis output** - the engine ranks candidate drivers by how closely they move with            the anomaly, but is explicitly prevented (in both code and LLM prompt instructions) from claiming one caused the             other.
- **Abstention is deterministic, not AI-generated** - when a KPI lacks enough history (e.g. Product X), the "insufficient             data" message is a fixed rule in `detect.py`/`narrate.py`. The LLM is never even called for this case, removing any          chance of it confidently guessing.

## Cost & scalability strategy:

- **Model choice:** Google Gemini, not a larger model - the narrative step is a bounded, well-specified formatting task             (structured JSON from pre-verified numbers), not open-ended reasoning, so a mid-sized model is sufficient and                meaningfully cheaper/faster at scale.
- **LLM calls are minimized by design**: access denials and abstentions never reach the LLM at all (see architecture diagram        above) in a real deployment, a large fraction of queries would be filtered out before incurring any LLM cost.
- **Caching (not yet implemented, but architected for):** identical KPI/region/persona queries within the same reporting            period would return a cached narrative rather than re-calling the LLM - the `run_id` + telemetry log already provide         the structure needed to add this.
- **Every LLM call is metered** (see Telemetry above), so cost per insight is visible and auditable from day one, not               something bolted on later.

## Known limitations:

- With only ~16–17 months of synthetic history, correlation estimates in `diagnose.py` can be noisier than they'd be with a            longer real-world dataset, a production version would report confidence intervals around the correlation itself,             not just around the anomaly detection.
- Data is synthetic (generated in `src/generate_data.py`), as the brief notes that teams are not expected to have access to            real proprietary data.
- Persona set is currently 2 roles; the architecture (contract-driven, no hardcoded roles) supports adding more without code           changes.
- The detector currently flags statistical anomalies but doesn't separate seasonal patterns from genuine shifts, so a                  natural next step would be seasonal decomposition before z-scoring.
- The feedback loop currently logs structured 👍/👎 feedback but doesn't yet auto-adjust thresholds, suggested next step              would use accumulated feedback to recalibrate the `materiality_threshold` values in the semantic contract over               time.
- Delivery channels are currently limited to the Streamlit UI. The persona-aware design would extend naturally to routing:             e.g. Regional Manager insights to Slack (daily, operational), Finance VP insights to a weekly email digest                   (strategic, less frequent), an additional delivery adapter per persona needed.


## Project structure:

├── app.py                    # Streamlit UI — run this
├── requirements.txt
├── Semantic_Contract.xlsx    # Single source of truth for KPI rules & access
├── data/
│   ├── daily_ops.csv
│   ├── weekly_sales.csv
│   └── monthly_finance.csv
├── src/
│   ├── contract_loader.py    # Reads the Excel contract
│   ├── detect.py              # Z-score anomaly detection (SQL)
│   ├── diagnose.py            # Driver ranking (correlation)
│   ├── confidence.py          # Contradictory-signal detection
│   ├── narrate.py             # Access control, abstention, structured LLM narrative, feedback logging
│   └── generate_data.py       # Synthetic data generator (documents all injected scenarios)
├── telemetry_log.jsonl        # Generated at runtime - latency/tokens/cost per LLM call (audit trail)
└── feedback_log.jsonl         # Generated at runtime - 👍/👎 feedback per run (feedback loop)

## Dependencies:
See `requirements.txt`: streamlit, pandas, numpy, duckdb, Gemini, openpyxl.

## Author:
Built solo by Tanushka Paul (Team Mars) for the Accenture Innovation Challenge 2026.
