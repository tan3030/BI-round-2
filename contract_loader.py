"""
Reads the semantic contract straight from the human-edited Excel file.
This is the ONLY place KPI names, definitions, sources, grains, owners, and
access rules come from - nothing is hardcoded elsewhere in the engine.

The one exception (explained to the user up front): the numeric detection
thresholds. The Excel column "When Do We Flag It As Unusual?" is a sentence
for humans, e.g. "Statistically odd AND at least a 5% swing from normal."
Code can't execute a sentence, so the matching numbers live in
THRESHOLDS below - kept deliberately tiny, and written to mirror the Excel
text exactly. If the Excel thresholds change, update this dict to match.
"""
import pandas as pd
import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = _THIS_DIR  # src/ -> project root

CONTRACT_PATH = os.path.join(_PROJECT_ROOT, "Semantic_Contract.xlsx")

# Mirrors the "When Do We Flag It As Unusual?" column, in machine-readable form.
# key = KPI Name (must match the Excel exactly)
THRESHOLDS = {
    "Regional Revenue":               {"z": 2.0, "min_pct_change": 5.0,  "min_periods": 6},
    "Repeat Order Rate":              {"z": 1.8, "min_pct_change": 8.0,  "min_periods": 6},
    "Support Ticket Volume":          {"z": 2.0, "min_pct_change": 20.0, "min_periods": 14},
    "Average Shipping Days":          {"z": 1.8, "min_pct_change": 10.0, "min_periods": 14},
    "Product X Revenue (New Launch)": {"z": None, "min_pct_change": None, "min_periods": 6},  # None z = "insufficient history" KPI
}

SOURCE_FILE_MAP = {
    "monthly_finance.csv": os.path.join(_PROJECT_ROOT, "data", "monthly_finance.csv"),
    "weekly_sales.csv":    os.path.join(_PROJECT_ROOT, "data", "weekly_sales.csv"),
    "daily_ops.csv":       os.path.join(_PROJECT_ROOT, "data", "daily_ops.csv"),
}


def load_kpi_definitions():
    """Returns a list of dicts, one per KPI, read straight from the Excel."""
    df = pd.read_excel(CONTRACT_PATH, sheet_name="KPI Definitions")
    kpis = []
    for _, row in df.iterrows():
        name = row["KPI Name"]
        kpis.append({
            "name": name,
            "meaning": row["What It Means"],
            "calculation": row["How It's Calculated"],
            "source_file": row["Comes From (File)"],
            "refresh": row["How Often It Updates"],
            "unit": row["Unit"],
            "flag_rule_text": row["When Do We Flag It As Unusual?"],
            "owner": row["Who's Responsible (Team)"],
            "access": row["Who's Allowed to See It"],
            "thresholds": THRESHOLDS.get(name, {}),
        })
    return kpis


def load_access_rules():
    """Returns a list of dicts, one per persona, read straight from the Excel."""
    df = pd.read_excel(CONTRACT_PATH, sheet_name="Access")
    personas = []
    for _, row in df.iterrows():
        personas.append({
            "role": row["Role"],
            "can_see": row["What They Can See"],
            "tone": row["How Explanations Should Sound"],
            "default_region": row["Default Region"],
        })
    return personas


def load_source_notes():
    df = pd.read_excel(CONTRACT_PATH, sheet_name="Data Source Notes")
    return df.to_dict("records")


if __name__ == "__main__":
    kpis = load_kpi_definitions()
    print(f"Loaded {len(kpis)} KPIs from Excel contract:")
    for k in kpis:
        print(f"  - {k['name']}  (source={k['source_file']}, access={k['access']})")

    personas = load_access_rules()
    print(f"\nLoaded {len(personas)} personas:")
    for p in personas:
        print(f"  - {p['role']}: {p['can_see']}")
