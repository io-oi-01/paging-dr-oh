#!/usr/bin/env python3
"""
generate.py  —  Paging Dr. Oh  —  Daily Content Generator
==========================================================
Runs every day at 6 AM UTC via GitHub Actions.
Calls the Anthropic Claude API (with web search) to produce fresh
medical-education content and writes index.html for Netlify.

Required environment variable:
    ANTHROPIC_API_KEY

Required files (in the same directory):
    template.html           CSS reference (styling is extracted at runtime)
    landmark_studies.json   curated list of 200+ classic trials
    history.json            deduplication tracker
    whats_new_current.json  rolling 30-day items
    archive.json            items older than 30 days
    manual_additions.json   manually added What's New items (consumed each run)
"""

# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — IMPORTS & CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

import os, sys, json, random, re, traceback
from datetime import datetime, timedelta, timezone

try:
    from anthropic import Anthropic
except ImportError:
    print("ERROR: 'anthropic' package not installed.  Run:  pip install anthropic")
    sys.exit(1)

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    print("ERROR: Set the ANTHROPIC_API_KEY environment variable.")
    sys.exit(1)

CLIENT  = Anthropic(api_key=API_KEY)
MODEL   = "claude-sonnet-4-20250514"
TODAY   = datetime.now(timezone.utc)
TODAY_STR = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"   # "March 5, 2026"
TODAY_ISO = TODAY.strftime("%Y-%m-%d")
THIRTY_DAYS_AGO = (TODAY - timedelta(days=30)).strftime("%Y-%m-%d")

# File paths (relative to repo root)
TEMPLATE_PATH  = "template.html"
OUTPUT_PATH    = "index.html"
HISTORY_PATH   = "history.json"
LANDMARK_PATH  = "landmark_studies.json"
MANUAL_PATH    = "manual_additions.json"
WN_PATH        = "whats_new_current.json"
ARCHIVE_PATH   = "archive.json"


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — DISEASE LIST  (weighted by hospitalist frequency)
# ═══════════════════════════════════════════════════════════════════════
# Diseases listed 3× are very common, 2× are common, 1× are less common.

DISEASES = [
    # ── Very common  (×3) ────────────────────────────────────────────
    "Community-Acquired Pneumonia",
    "Community-Acquired Pneumonia",
    "Community-Acquired Pneumonia",
    "Acute Decompensated Heart Failure",
    "Acute Decompensated Heart Failure",
    "Acute Decompensated Heart Failure",
    "COPD Exacerbation",
    "COPD Exacerbation",
    "COPD Exacerbation",
    "Acute Kidney Injury",
    "Acute Kidney Injury",
    "Acute Kidney Injury",
    "Sepsis and Septic Shock",
    "Sepsis and Septic Shock",
    "Sepsis and Septic Shock",
    "Cellulitis and Skin Soft Tissue Infections",
    "Cellulitis and Skin Soft Tissue Infections",
    "Cellulitis and Skin Soft Tissue Infections",
    "Urinary Tract Infection and Pyelonephritis",
    "Urinary Tract Infection and Pyelonephritis",
    "Urinary Tract Infection and Pyelonephritis",
    "Venous Thromboembolism",
    "Venous Thromboembolism",
    "Venous Thromboembolism",
    "Acute Coronary Syndromes",
    "Acute Coronary Syndromes",
    "Acute Coronary Syndromes",
    "Atrial Fibrillation",
    "Atrial Fibrillation",
    "Atrial Fibrillation",
    "Diabetic Ketoacidosis",
    "Diabetic Ketoacidosis",
    "Diabetic Ketoacidosis",
    "Upper GI Bleeding",
    "Upper GI Bleeding",
    "Upper GI Bleeding",
    "Acute Pancreatitis",
    "Acute Pancreatitis",
    "Acute Pancreatitis",
    "Hyponatremia",
    "Hyponatremia",
    "Hyponatremia",
    "Hyperkalemia",
    "Hyperkalemia",
    "Hyperkalemia",
    # ── Common  (×2) ─────────────────────────────────────────────────
    "Pneumothorax", "Pneumothorax",
    "Cirrhosis and Hepatic Encephalopathy", "Cirrhosis and Hepatic Encephalopathy",
    "Alcohol Withdrawal Syndrome", "Alcohol Withdrawal Syndrome",
    "Acute Ischemic Stroke", "Acute Ischemic Stroke",
    "Asthma Exacerbation", "Asthma Exacerbation",
    "Clostridioides difficile Infection", "Clostridioides difficile Infection",
    "Bacterial Meningitis", "Bacterial Meningitis",
    "Osteomyelitis", "Osteomyelitis",
    "Infective Endocarditis", "Infective Endocarditis",
    "Acute Respiratory Failure", "Acute Respiratory Failure",
    "Delirium", "Delirium",
    "Iron Deficiency Anemia", "Iron Deficiency Anemia",
    "Hypercalcemia", "Hypercalcemia",
    "Acute Gout", "Acute Gout",
    "Syncope", "Syncope",
    "ARDS", "ARDS",
    "Lower GI Bleeding", "Lower GI Bleeding",
    "Hypertensive Emergency", "Hypertensive Emergency",
    "Acute Cholecystitis", "Acute Cholecystitis",
    "Small Bowel Obstruction", "Small Bowel Obstruction",
    "Hospital-Acquired Pneumonia", "Hospital-Acquired Pneumonia",
    "Hypoglycemia", "Hypoglycemia",
    # ── Less common  (×1) ────────────────────────────────────────────
    "Pericarditis and Pericardial Effusion",
    "Aortic Dissection",
    "Malignant Spinal Cord Compression",
    "Tumor Lysis Syndrome",
    "Adrenal Crisis",
    "Thyroid Storm",
    "Myxedema Coma",
    "Thrombotic Thrombocytopenic Purpura",
    "Hemolytic Uremic Syndrome",
    "Sarcoidosis",
    "Systemic Lupus Erythematosus Flare",
    "Antiphospholipid Syndrome",
    "Hepatorenal Syndrome",
    "Spontaneous Bacterial Peritonitis",
    "Acute Interstitial Nephritis",
    "Rhabdomyolysis",
    "Myasthenia Gravis Crisis",
    "Guillain-Barre Syndrome",
    "Status Epilepticus",
    "Neutropenic Fever",
    "Disseminated Intravascular Coagulation",
    "Heparin-Induced Thrombocytopenia",
    "Hyperosmolar Hyperglycemic State",
    "Acute Liver Failure",
    "Pulmonary Hypertension",
    "Takotsubo Cardiomyopathy",
    "Cardiac Tamponade",
    "Hemophagocytic Lymphohistiocytosis",
]


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3 — CLINICAL CALCULATOR DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════
# Each calculator: tuple-list of (checkbox_id, label_html, points)
# Ranges: (min_score, max_score, css_class, interpretation_text)

CALCULATORS = {
    "curb65": {
        "title": "CURB-65 Severity Score",
        "criteria": [
            ("curb-c",  "<strong>C</strong>onfusion &mdash; new-onset disorientation to person, place, or time", 1),
            ("curb-u",  "<strong>U</strong>rea (BUN) &gt; 19 mg/dL (7 mmol/L)", 1),
            ("curb-r",  "<strong>R</strong>espiratory Rate &ge; 30 breaths/min", 1),
            ("curb-b",  "<strong>B</strong>lood Pressure: SBP &lt; 90 or DBP &le; 60 mmHg", 1),
            ("curb-65", "Age &ge; <strong>65</strong> years", 1),
        ],
        "ranges": [
            (0, 0, "risk-low",      "Low risk (0.6% 30-day mortality). Consider outpatient treatment."),
            (1, 1, "risk-low",      "Low risk (2.7% mortality). Consider outpatient with close follow-up."),
            (2, 2, "risk-moderate", "Moderate risk (6.8% mortality). Consider short inpatient stay or closely supervised outpatient."),
            (3, 3, "risk-high",     "Severe (14.0% mortality). Hospitalize. Consider ICU admission."),
            (4, 4, "risk-high",     "Severe (27.8% mortality). Hospitalize with ICU admission recommended."),
            (5, 5, "risk-high",     "Highest risk (57.6% mortality). Urgent ICU admission."),
        ],
    },
    "qsofa": {
        "title": "qSOFA (Quick Sepsis-Related Organ Failure Assessment)",
        "criteria": [
            ("qsofa-ms",  "Altered mental status (GCS &lt; 15)", 1),
            ("qsofa-rr",  "Respiratory Rate &ge; 22 breaths/min", 1),
            ("qsofa-sbp", "Systolic Blood Pressure &le; 100 mmHg", 1),
        ],
        "ranges": [
            (0, 1, "risk-low",  "Low risk. qSOFA negative. Continue standard monitoring."),
            (2, 3, "risk-high", "qSOFA positive (&ge;2). Assess for organ dysfunction. Initiate sepsis workup and consider higher level of care."),
        ],
    },
    "wells_pe": {
        "title": "Wells Score for Pulmonary Embolism",
        "criteria": [
            ("wells-pe-1", "Clinical signs/symptoms of DVT (leg swelling, pain with palpation)", 3),
            ("wells-pe-2", "PE is #1 diagnosis or equally likely", 3),
            ("wells-pe-3", "Heart rate &gt; 100 bpm", 1.5),
            ("wells-pe-4", "Immobilization (&ge;3 days) or surgery in previous 4 weeks", 1.5),
            ("wells-pe-5", "Previous DVT/PE", 1.5),
            ("wells-pe-6", "Hemoptysis", 1),
            ("wells-pe-7", "Malignancy (treatment within 6 months or palliative)", 1),
        ],
        "ranges": [
            (0,   1.5, "risk-low",      "Low probability (1.3% PE incidence). Consider D-dimer; if negative, PE effectively excluded."),
            (2,   6,   "risk-moderate", "Moderate probability (16.2%). Obtain D-dimer or CT pulmonary angiography."),
            (6.5, 15,  "risk-high",     "High probability (37.5%). CT pulmonary angiography recommended; do not rely on D-dimer alone."),
        ],
    },
    "wells_dvt": {
        "title": "Wells Score for DVT",
        "criteria": [
            ("wells-dvt-1", "Active cancer (treatment ongoing, within 6 months, or palliative)", 1),
            ("wells-dvt-2", "Paralysis, paresis, or recent plaster immobilization of the lower extremities", 1),
            ("wells-dvt-3", "Recently bedridden &gt;3 days or major surgery within 12 weeks", 1),
            ("wells-dvt-4", "Localized tenderness along the distribution of the deep venous system", 1),
            ("wells-dvt-5", "Entire leg swollen", 1),
            ("wells-dvt-6", "Calf swelling &ge;3 cm compared to asymptomatic leg", 1),
            ("wells-dvt-7", "Pitting edema confined to the symptomatic leg", 1),
            ("wells-dvt-8", "Collateral superficial veins (non-varicose)", 1),
            ("wells-dvt-9", "Previously documented DVT", 1),
            ("wells-dvt-10", "Alternative diagnosis at least as likely as DVT", -2),
        ],
        "ranges": [
            (-2, 0, "risk-low",      "Low probability (5%). D-dimer recommended; if negative, DVT excluded."),
            (1,  2, "risk-moderate", "Moderate probability (17%). D-dimer or ultrasound recommended."),
            (3, 10, "risk-high",     "High probability (53%). Compression ultrasound recommended."),
        ],
    },
    "cha2ds2vasc": {
        "title": "CHA\u2082DS\u2082-VASc Score",
        "criteria": [
            ("cha-c",  "<strong>C</strong>ongestive Heart Failure (or LVEF &le;40%)", 1),
            ("cha-h",  "<strong>H</strong>ypertension", 1),
            ("cha-a2", "<strong>A</strong>ge &ge;75 years", 2),
            ("cha-d",  "<strong>D</strong>iabetes mellitus", 1),
            ("cha-s2", "<strong>S</strong>troke / TIA / thromboembolism history", 2),
            ("cha-v",  "<strong>V</strong>ascular disease (prior MI, PAD, aortic plaque)", 1),
            ("cha-a",  "<strong>A</strong>ge 65&ndash;74 years", 1),
            ("cha-sc", "<strong>Sc</strong> &mdash; Sex category: female", 1),
        ],
        "ranges": [
            (0, 0, "risk-low",      "0 points: Low risk. Anticoagulation generally not recommended."),
            (1, 1, "risk-moderate", "1 point: Low-moderate risk. Consider anticoagulation (especially if not female-sex point alone)."),
            (2, 9, "risk-high",     "Score &ge;2: Anticoagulation recommended (DOAC preferred over warfarin per guidelines)."),
        ],
    },
    "hasbled": {
        "title": "HAS-BLED Bleeding Risk Score",
        "criteria": [
            ("has-h", "<strong>H</strong>ypertension (uncontrolled, SBP &gt;160)", 1),
            ("has-a", "<strong>A</strong>bnormal renal or liver function (1 point each)", 1),
            ("has-s", "<strong>S</strong>troke history", 1),
            ("has-b", "<strong>B</strong>leeding history or predisposition", 1),
            ("has-l", "<strong>L</strong>abile INR (if on warfarin; TTR &lt;60%)", 1),
            ("has-e", "<strong>E</strong>lderly (age &gt;65)", 1),
            ("has-d", "<strong>D</strong>rugs (antiplatelets, NSAIDs) or alcohol (1 point each)", 1),
        ],
        "ranges": [
            (0, 2, "risk-low",      "Low bleeding risk. Proceed with anticoagulation if indicated."),
            (3, 7, "risk-high",     "High bleeding risk (&ge;3). Does NOT contraindicate anticoagulation but warrants closer monitoring and modifiable risk factor correction."),
        ],
    },
    "bisap": {
        "title": "BISAP Score for Pancreatitis Severity",
        "criteria": [
            ("bisap-b", "<strong>B</strong>UN &gt; 25 mg/dL", 1),
            ("bisap-i", "<strong>I</strong>mpaired mental status (disorientation, lethargy, somnolence, coma)", 1),
            ("bisap-s", "<strong>S</strong>IRS (Systemic Inflammatory Response Syndrome) &mdash; &ge;2 of: temp &gt;38 or &lt;36, HR &gt;90, RR &gt;20 or PaCO2 &lt;32, WBC &gt;12k or &lt;4k", 1),
            ("bisap-a", "<strong>A</strong>ge &gt; 60 years", 1),
            ("bisap-p", "<strong>P</strong>leural effusion on imaging", 1),
        ],
        "ranges": [
            (0, 0, "risk-low",      "Score 0: Mortality &lt;1%. Low risk."),
            (1, 1, "risk-low",      "Score 1: Mortality &lt;1%. Low risk."),
            (2, 2, "risk-moderate", "Score 2: Mortality ~2%. Moderate risk. Close monitoring recommended."),
            (3, 3, "risk-high",     "Score 3: Mortality ~5&ndash;8%. High risk. Consider ICU-level care."),
            (4, 5, "risk-high",     "Score 4&ndash;5: Mortality ~20&ndash;25%. Very high risk. ICU admission recommended."),
        ],
    },
}

# Which calculators to show for each disease
DISEASE_CALCULATORS = {
    "Community-Acquired Pneumonia":       ["curb65", "qsofa"],
    "Hospital-Acquired Pneumonia":        ["qsofa"],
    "Sepsis and Septic Shock":            ["qsofa"],
    "ARDS":                               ["qsofa"],
    "Acute Respiratory Failure":          ["qsofa"],
    "Bacterial Meningitis":               ["qsofa"],
    "Venous Thromboembolism":             ["wells_pe", "wells_dvt"],
    "Atrial Fibrillation":               ["cha2ds2vasc", "hasbled"],
    "Acute Pancreatitis":                ["bisap"],
    "Acute Decompensated Heart Failure":  ["qsofa"],
    "Neutropenic Fever":                  ["qsofa"],
    "Infective Endocarditis":            ["qsofa"],
}


# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def load_json(path, default=None):
    """Load a JSON file.  Returns *default* if the file is missing or empty."""
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    """Write data to a JSON file with readable formatting."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_css_from_template():
    """Extract the CSS block from template.html so styling stays in sync."""
    try:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        m = re.search(r"<style>(.*?)</style>", html, re.DOTALL)
        return m.group(1).strip() if m else ""
    except FileNotFoundError:
        print(f"WARNING: {TEMPLATE_PATH} not found. Using empty CSS.")
        return ""


def call_claude(prompt, use_search=False, max_tokens=16000):
    """Call the Anthropic API.  Optionally enables the web-search tool."""
    kwargs = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if use_search:
        kwargs["tools"] = [
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}
        ]
    response = CLIENT.messages.create(**kwargs)

    # Collect all text blocks from the response
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


def parse_json_response(text):
    """Extract the first JSON object or array from an API response string."""
    # Strip markdown code fences if present
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    # Find outermost { } or [ ]
    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            if text[i] == start_char:
                depth += 1
            elif text[i] == end_char:
                depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("No JSON found in API response")


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — CONTENT GENERATION  (API prompts & calls)
# ═══════════════════════════════════════════════════════════════════════

def generate_disease_content(disease_name):
    """Call Claude to generate the Disease of the Day content."""
    prompt = f"""You are a medical education content creator for "Paging Dr. Oh," a daily
clinical reference for internal medicine physicians and hospitalists.

Today's disease is: **{disease_name}**
Today's date is: {TODAY_STR}

Generate comprehensive, evidence-based content.  This will be read by busy
physicians at the bedside, so FORMAT EVERYTHING AS BULLET POINTS for rapid
scanning.

Rules:
- Every bullet should be an HTML <li> element.
- Use <strong> for key terms.  Use HTML entities for symbols (&ge; &le; &rarr; &mdash; &lt; &gt;).
- Include diagnostic test statistics INLINE as prose (Sn, Sp, PPV, NPV, +LR, -LR) wherever data exists.
- Add superscript reference numbers like <sup>1</sup> throughout the text.
- Treatment must be stratified by clinical setting (outpatient / inpatient non-ICU / ICU when applicable).
- Include 8-12 numbered references from peer-reviewed sources.
- Suggest 1-3 relevant clinical calculator IDs from this list ONLY:
  {json.dumps(list(CALCULATORS.keys()))}
  Pick only calculators that are clinically relevant to {disease_name}. If none fit, use an empty list.
- Suggest 2-4 society guidelines with DOI or URL links.

Return ONLY valid JSON (no markdown fences, no extra text) with this structure:

{{
  "disease_name": "{disease_name}",
  "clinical_manifestations": {{
    "symptoms": ["<li>bullet html here<sup>1</sup></li>"],
    "exam_findings": ["<li>bullet html here</li>"]
  }},
  "differential_diagnosis": {{
    "common": ["<li>...</li>"],
    "less_common": ["<li>...</li>"],
    "mimics": ["<li>...</li>"]
  }},
  "diagnostic_tests": [
    {{
      "label": "Test Category Name",
      "items": ["<li>Test name: Sn X%, Sp Y%, +LR Z, -LR W<sup>ref</sup></li>"]
    }}
  ],
  "treatment": [
    {{
      "label": "Setting or Context<sup>ref</sup>",
      "items": ["<li>Drug dose route frequency</li>"]
    }}
  ],
  "expected_course": ["<li>...</li>"],
  "follow_up": ["<li>...</li>"],
  "low_resource": [
    {{
      "label": "Category",
      "items": ["<li>...</li>"]
    }}
  ],
  "calculators": ["curb65", "qsofa"],
  "guidelines": [
    {{
      "name": "Guideline Title",
      "organization": "Society Name",
      "year": "2024",
      "url": "https://doi.org/..."
    }}
  ],
  "references": [
    "Author AB, Author CD. Title. Journal. Year;Vol(Issue):Pages."
  ]
}}"""
    try:
        raw = call_claude(prompt, use_search=True, max_tokens=16000)
        return parse_json_response(raw)
    except Exception as e:
        print(f"ERROR generating disease content: {e}")
        traceback.print_exc()
        # Return minimal fallback
        return {
            "disease_name": disease_name,
            "clinical_manifestations": {"symptoms": [f"<li>Content generation failed for {disease_name}. Please try again tomorrow.</li>"], "exam_findings": []},
            "differential_diagnosis": {"common": [], "less_common": [], "mimics": []},
            "diagnostic_tests": [],
            "treatment": [],
            "expected_course": [],
            "follow_up": [],
            "low_resource": [],
            "calculators": [],
            "guidelines": [],
            "references": [],
        }


def generate_whats_new(history):
    """Call Claude (with web search) to find notable recent medical updates."""
    existing_ids = history.get("whats_new_ids", [])
    prompt = f"""You are a medical news curator for "Paging Dr. Oh," a daily reference for
hospitalists and internal medicine physicians.

Today is {TODAY_STR}. Search the web for notable medical updates from the past
30 days (since {THIRTY_DAYS_AGO}). Find items in these categories:

1. NOTABLE STUDIES — Important RCTs, meta-analyses, or major observational studies
   published in top medical journals (NEJM, JAMA, Lancet, BMJ, Annals of Internal Medicine,
   CHEST, Critical Care Medicine, Circulation, etc.)

2. GUIDELINES — New or updated clinical practice guidelines from major societies
   (ATS, IDSA, AHA, ACC, AASLD, AGA, ACR, KDIGO, etc.)

3. FDA ACTIONS — New drug approvals, expanded indications, safety communications,
   black box warning updates, or drug withdrawals.

Find 3-5 total items that are MOST relevant to a hospitalist physician.

Skip any items with these IDs (already covered): {json.dumps(existing_ids[-50:])}

Return ONLY valid JSON array (no markdown fences):
[
  {{
    "id": "unique-slug-2026",
    "type": "RCT" or "Meta-Analysis" or "Guideline" or "FDA Action" or "Observational",
    "specialty": "e.g. Critical Care",
    "source": "Journal or Organization Name",
    "date": "Month Day, Year",
    "title": "Full title of the study/guideline/action",
    "study_design": "Brief description of design, N, primary outcome (if study)",
    "key_findings": "Key results with statistics where available",
    "bottom_line": "One-sentence clinical takeaway for busy physicians",
    "confidence": "high" or "moderate" or "preliminary",
    "source_url": "https://..."
  }}
]

If you cannot find recent items, return an empty array: []"""
    try:
        raw = call_claude(prompt, use_search=True, max_tokens=8000)
        items = parse_json_response(raw)
        if not isinstance(items, list):
            return []
        # Add the date_iso for archive management
        for item in items:
            item["date_iso"] = TODAY_ISO
        return items
    except Exception as e:
        print(f"WARNING: What's New generation failed: {e}")
        return []


def generate_landmark_content(study_info):
    """Call Claude to generate a deep-dive analysis of a landmark trial."""
    prompt = f"""You are a medical education content creator for "Paging Dr. Oh."

Generate a detailed, educational deep-dive analysis of this landmark clinical trial:

Trial: {study_info.get('name', 'Unknown')}
Year: {study_info.get('year', 'Unknown')}
Journal: {study_info.get('journal', 'Unknown')}
Brief: {study_info.get('one_liner', '')}

Write for an internal medicine physician audience. Be thorough and include specific
statistics (absolute risk reduction, relative risk, NNT, confidence intervals, p-values).

Return ONLY valid JSON:
{{
  "title": "{study_info.get('name', 'Unknown')}",
  "meta": "{study_info.get('journal', '')} &bull; {study_info.get('year', '')} &bull; Authors/Group",
  "study_design": "Detailed description of the study design, setting, and methodology...",
  "population": "Who was enrolled, key inclusion/exclusion criteria, sample size...",
  "primary_endpoint": "What was the primary outcome measure...",
  "key_findings": "Detailed results with statistics (ARR, RRR, NNT, CI, p-values)...",
  "what_changed": "How this trial changed clinical practice...",
  "critics_said": "Major criticisms and limitations...",
  "where_it_stands_now": "Current relevance and how subsequent evidence has refined the findings..."
}}"""
    try:
        raw = call_claude(prompt, use_search=False, max_tokens=8000)
        return parse_json_response(raw)
    except Exception as e:
        print(f"WARNING: Landmark study generation failed: {e}")
        return {
            "title": study_info.get("name", "Unknown Trial"),
            "meta": f"{study_info.get('journal', '')} &bull; {study_info.get('year', '')}",
            "study_design": "Content generation failed. Please try again tomorrow.",
            "population": "", "primary_endpoint": "", "key_findings": "",
            "what_changed": "", "critics_said": "", "where_it_stands_now": "",
        }


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6 — HTML BUILDING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def build_accordion(section_id, title, inner_html, open_by_default=False):
    """Build a single accordion section."""
    cls = "accordion open" if open_by_default else "accordion"
    return f"""
      <div class="{cls}">
        <div class="accordion-header" onclick="toggleAccordion(this)">
          <h3>{title}</h3>
          <span class="accordion-toggle">+</span>
        </div>
        <div class="accordion-body">
          <div class="accordion-content">
            {inner_html}
          </div>
        </div>
      </div>"""


def build_labeled_list(sections):
    """Build HTML from a list of {label, items} dicts."""
    html = ""
    for sec in sections:
        html += f'<p><strong>{sec["label"]}</strong></p>\n<ul>\n'
        for item in sec.get("items", []):
            # Ensure items are wrapped in <li> if not already
            item = item.strip()
            if not item.startswith("<li"):
                item = f"<li>{item}</li>"
            html += f"  {item}\n"
        html += "</ul>\n"
    return html


def build_simple_list(items):
    """Build a <ul> from a list of HTML strings."""
    html = "<ul>\n"
    for item in items:
        item = item.strip()
        if not item.startswith("<li"):
            item = f"<li>{item}</li>"
        html += f"  {item}\n"
    html += "</ul>\n"
    return html


def build_disease_tab(content):
    """Build the Disease of the Day tab HTML."""
    d = content
    disease_name = d.get("disease_name", "Unknown")

    # -- Header card --
    html = f"""
      <div class="disease-header card">
        <h2>{disease_name}</h2>
        <p class="verified-date">Last verified: {TODAY_STR}</p>
      </div>"""

    # -- Clinical Manifestations (open by default) --
    cm = d.get("clinical_manifestations", {})
    cm_html = '<p><strong>Symptoms</strong></p>\n'
    cm_html += build_simple_list(cm.get("symptoms", []))
    if cm.get("exam_findings"):
        cm_html += '<p><strong>Exam Findings</strong></p>\n'
        cm_html += build_simple_list(cm["exam_findings"])
    html += build_accordion("cm", "Clinical Manifestations", cm_html, open_by_default=True)

    # -- Differential Diagnosis --
    dd = d.get("differential_diagnosis", {})
    dd_html = ""
    if dd.get("common"):
        dd_html += '<p><strong>Common</strong></p>\n' + build_simple_list(dd["common"])
    if dd.get("less_common"):
        dd_html += '<p><strong>Less Common</strong></p>\n' + build_simple_list(dd["less_common"])
    if dd.get("immunocompromised_or_less_common"):
        dd_html += '<p><strong>Immunocompromised / Less Common</strong></p>\n' + build_simple_list(dd["immunocompromised_or_less_common"])
    if dd.get("mimics"):
        dd_html += '<p><strong>Mimics</strong></p>\n' + build_simple_list(dd["mimics"])
    html += build_accordion("dd", "Differential Diagnosis", dd_html)

    # -- Diagnostic Tests --
    dt = d.get("diagnostic_tests", [])
    if isinstance(dt, list) and dt:
        html += build_accordion("dt", "Diagnostic Tests", build_labeled_list(dt))

    # -- Treatment --
    tx = d.get("treatment", [])
    if isinstance(tx, list) and tx:
        html += build_accordion("tx", "Treatment", build_labeled_list(tx))

    # -- Expected Course --
    ec = d.get("expected_course", [])
    if ec:
        html += build_accordion("ec", "Expected Course", build_simple_list(ec))

    # -- Follow-up --
    fu = d.get("follow_up", [])
    if fu:
        html += build_accordion("fu", "Follow-up", build_simple_list(fu))

    # -- Low-Resource Considerations --
    lr = d.get("low_resource", [])
    if isinstance(lr, list) and lr:
        html += build_accordion("lr", "Low-Resource Considerations", build_labeled_list(lr))

    # -- Clinical Calculators --
    calc_ids = d.get("calculators", [])
    # Also check the pre-defined mapping
    if not calc_ids:
        calc_ids = DISEASE_CALCULATORS.get(disease_name, [])
    if calc_ids:
        html += '\n      <h3 class="section-title">Relevant Clinical Scores</h3>\n'
        for cid in calc_ids:
            if cid in CALCULATORS:
                html += build_calculator_card(cid)

    # -- Guidelines Box --
    guidelines = d.get("guidelines", [])
    if guidelines:
        html += '\n      <div class="guidelines-box">\n'
        html += '        <h4>Society Guidelines Quick Reference</h4>\n'
        for g in guidelines:
            url = g.get("url") or g.get("doi_url") or "#"
            html += f"""        <div class="guideline-item">
          <div class="guideline-info">
            <div class="guideline-name">{g.get("name", "")}</div>
            <div class="guideline-meta">{g.get("organization", "")} &bull; {g.get("year", "")}</div>
          </div>
          <a href="{url}" class="guideline-link" target="_blank" rel="noopener">View Guideline &#8594;</a>
        </div>\n"""
        html += "      </div>\n"

    # -- References --
    refs = d.get("references", [])
    if refs:
        html += '\n      <div class="references">\n        <h4>References</h4>\n        <ol>\n'
        for i, ref in enumerate(refs, 1):
            html += f'          <li id="ref-{i}">{ref}</li>\n'
        html += "        </ol>\n      </div>\n"

    # -- Randomize Button --
    html += """
      <div class="randomize-wrap">
        <button class="btn btn-primary" id="randomize-btn">Randomize Disease</button>
      </div>"""

    return html


def build_calculator_card(calc_id):
    """Build HTML for a clinical calculator card."""
    calc = CALCULATORS[calc_id]
    func_name = f"update_{calc_id}"
    html = f'      <div class="calculator-card" id="{calc_id}-calc">\n'
    html += f'        <h4>{calc["title"]}</h4>\n'
    for crit_id, label, points in calc["criteria"]:
        html += f"""        <div class="calc-option">
          <input type="checkbox" id="{crit_id}" onchange="{func_name}()">
          <label for="{crit_id}">{label}</label>
        </div>\n"""
    # Default interpretation (first range)
    default_cls = calc["ranges"][0][2]
    default_text = calc["ranges"][0][3]
    html += f"""        <div class="score-result">
          <span class="score-label">{calc["title"]}</span>
          <span class="score-value" id="{calc_id}-score">0</span>
          <div class="score-interpretation {default_cls}" id="{calc_id}-interp">
            {default_text}
          </div>
        </div>
      </div>\n"""
    return html


def build_calculator_js(calc_id):
    """Build the JavaScript function for a calculator."""
    calc = CALCULATORS[calc_id]
    func_name = f"update_{calc_id}"
    criteria_js = json.dumps([{"id": c[0], "pts": c[2]} for c in calc["criteria"]])

    js = f"    function {func_name}() {{\n"
    js += f"      var criteria = {criteria_js};\n"
    js += "      var score = 0;\n"
    js += "      criteria.forEach(function(c) {\n"
    js += "        if (document.getElementById(c.id).checked) score += c.pts;\n"
    js += "      });\n"
    js += f"      document.getElementById('{calc_id}-score').textContent = score;\n"
    js += f"      var interp = document.getElementById('{calc_id}-interp');\n"

    for i, (min_val, max_val, cls, text) in enumerate(calc["ranges"]):
        escaped = text.replace("'", "\\'")
        if i == 0:
            js += f"      if (score <= {max_val}) {{ interp.className = 'score-interpretation {cls}'; interp.textContent = '{escaped}'; }}\n"
        elif i == len(calc["ranges"]) - 1:
            js += f"      else {{ interp.className = 'score-interpretation {cls}'; interp.textContent = '{escaped}'; }}\n"
        else:
            js += f"      else if (score <= {max_val}) {{ interp.className = 'score-interpretation {cls}'; interp.textContent = '{escaped}'; }}\n"

    js += "    }\n"
    return js


def pill_class(item_type):
    """Map item type to CSS pill class."""
    mapping = {
        "RCT": "pill-rct",
        "Meta-Analysis": "pill-meta",
        "Guideline": "pill-guideline",
        "FDA Action": "pill-fda",
        "Observational": "pill-rct",
    }
    return mapping.get(item_type, "pill-specialty")


def build_whatsnew_tab(items):
    """Build the What's New tab HTML."""
    html = '\n      <h3 class="section-title" style="margin-top:0;">Latest Updates (Past 30 Days)</h3>\n'

    if not items:
        html += '      <p style="color:var(--gray); font-style:italic; padding:20px 0;">No updates yet. Check back tomorrow!</p>\n'
        return html

    for item in items:
        wn_id = item.get("id", "wn-unknown")
        item_type = item.get("type", "Study")
        specialty = item.get("specialty", "")
        source = item.get("source", "")
        date = item.get("date", "")
        title = item.get("title", "")
        study_design = item.get("study_design", "")
        key_findings = item.get("key_findings", "")
        bottom_line = item.get("bottom_line", "")
        confidence = item.get("confidence", "moderate")
        source_url = item.get("source_url", "#")

        conf_class = f"confidence-{confidence}"
        conf_label = confidence.capitalize()

        html += f"""
      <div class="wn-card">
        <div class="wn-card-header">
          <div class="wn-tags">
            <span class="pill {pill_class(item_type)}">{item_type}</span>
            <span class="pill pill-specialty">{specialty}</span>
          </div>
          <div class="card-actions">
            <button class="action-btn star" title="Star this study" data-id="{wn_id}">&#9734;</button>
          </div>
        </div>
        <div class="wn-source">{source} &bull; {date}</div>
        <div class="wn-title">{title}</div>"""

        if study_design:
            html += f"""        <div class="wn-section-label">Study Design</div>
        <p class="wn-text">{study_design}</p>\n"""
        if key_findings:
            html += f"""        <div class="wn-section-label">Key Findings</div>
        <p class="wn-text">{key_findings}</p>\n"""
        if bottom_line:
            html += f"""        <div class="wn-bottom-line">
          <strong>Bottom Line:</strong> {bottom_line}
        </div>\n"""

        html += f"""        <div class="wn-footer">
          <div class="confidence {conf_class}">
            <span class="confidence-dot"></span>
            {conf_label} confidence
          </div>
          <a href="{source_url}" class="source-link" target="_blank" rel="noopener">View Source &#8594;</a>
        </div>
      </div>\n"""

    return html


def build_landmark_tab(content):
    """Build the Landmark Study tab HTML."""
    c = content
    title = c.get("title", "Unknown Trial")
    meta = c.get("meta", "")

    sections_order = [
        ("study_design",      "Study Design"),
        ("population",        "Population"),
        ("primary_endpoint",  "Primary Endpoint"),
        ("key_findings",      "Key Findings"),
        ("what_changed",      "What Changed in Practice"),
        ("critics_said",      "What Critics Said"),
        ("where_it_stands_now", "Where It Stands Now"),
    ]

    html = f"""
      <div class="landmark-card">
        <div class="landmark-header">
          <h3 class="landmark-title">{title}</h3>
          <button class="action-btn star" data-id="{title.lower().replace(' ', '-')[:30]}" title="Star this study">&#9734;</button>
        </div>
        <div class="landmark-meta">{meta}</div>\n"""

    for key, label in sections_order:
        text = c.get(key, "")
        if text:
            html += f"""
        <div class="landmark-section">
          <h4>{label}</h4>
          <p>{text}</p>
        </div>\n"""

    html += "      </div>\n"

    # Favorites section
    html += """
      <div class="favorites-section">
        <div class="favorites-header">
          <h4>Starred Studies</h4>
          <button class="btn btn-outline btn-sm" id="export-btn" onclick="exportFavorites()">Copy to Clipboard</button>
        </div>
        <ul class="favorites-list" id="favorites-list">
          <li class="favorites-empty">No favorites yet. Star studies to add them here.</li>
        </ul>
      </div>"""

    return html


def build_archive_tab(archive_items):
    """Build the Archive tab HTML."""
    html = """
      <h3 class="section-title" style="margin-top:0;">Archive</h3>
      <p style="font-size:0.9rem; color:var(--gray); margin-bottom:20px;">Studies and updates that have rolled out of the 30-day window, organized by month.</p>\n"""

    if not archive_items:
        html += '      <p style="color:var(--gray); font-style:italic;">No archived items yet. Items will appear here after 30 days.</p>\n'
        return html

    # Group by month
    months = {}
    for item in archive_items:
        date_iso = item.get("date_iso", "")
        if date_iso:
            try:
                dt = datetime.strptime(date_iso, "%Y-%m-%d")
                month_key = dt.strftime("%Y-%m")
                month_label = dt.strftime("%B %Y")
            except ValueError:
                month_key = "0000-00"
                month_label = "Unknown"
        else:
            month_key = "0000-00"
            month_label = "Unknown"

        if month_key not in months:
            months[month_key] = {"label": month_label, "items": []}
        months[month_key]["items"].append(item)

    # Sort months descending
    for month_key in sorted(months.keys(), reverse=True):
        month = months[month_key]
        html += f"""
      <div class="archive-month">
        <div class="archive-month-header" onclick="toggleArchiveMonth(this)">
          <h3>{month["label"]}</h3>
          <span class="archive-toggle">&#9654;</span>
        </div>
        <div class="archive-month-body">\n"""

        for item in month["items"]:
            item_type = item.get("type", "Study")
            specialty = item.get("specialty", "")
            date_str = item.get("date", "")
            title = item.get("title", "Untitled")
            bottom_line = item.get("bottom_line", "")
            key_findings = item.get("key_findings", "")
            detail = key_findings if key_findings else bottom_line

            html += f"""
          <div class="archive-card" onclick="toggleArchiveCard(this)">
            <div class="archive-card-header">
              <div>
                <div class="archive-card-title">{title}</div>
                <div class="archive-card-meta"><span class="pill {pill_class(item_type)}" style="font-size:0.65rem;padding:2px 8px;">{item_type}</span> &bull; {specialty} &bull; {date_str}</div>
              </div>
              <span class="accordion-toggle">+</span>
            </div>
            <div class="archive-card-details">
              <div class="archive-detail-content">
                <p>{detail}</p>
                <p><strong>Bottom Line:</strong> {bottom_line}</p>
              </div>
            </div>
          </div>\n"""

        html += """
        </div>
      </div>\n"""

    return html


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7 — JAVASCRIPT CONSTANT  (static interactive behaviour)
# ═══════════════════════════════════════════════════════════════════════

BASE_JS = """
    /* ===== TAB SWITCHING ===== */
    document.querySelectorAll('.tab-btn').forEach(function(btn) {
      btn.addEventListener('click', function() {
        document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
        document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
        btn.classList.add('active');
        var target = document.getElementById(btn.getAttribute('data-tab'));
        if (target) target.classList.add('active');
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });
    });

    /* ===== ACCORDION TOGGLE ===== */
    function toggleAccordion(header) {
      var accordion = header.parentElement;
      accordion.classList.toggle('open');
    }

    /* ===== ARCHIVE MONTH TOGGLE ===== */
    function toggleArchiveMonth(header) {
      header.parentElement.classList.toggle('open');
    }

    /* ===== ARCHIVE CARD EXPAND ===== */
    function toggleArchiveCard(card) {
      card.classList.toggle('expanded');
    }

    /* ===== STAR / FAVORITE BUTTONS ===== */
    document.querySelectorAll('.action-btn.star').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        btn.classList.toggle('active');
        var id = btn.getAttribute('data-id');
        var favorites = JSON.parse(localStorage.getItem('pdr-favorites') || '[]');

        if (btn.classList.contains('active')) {
          btn.innerHTML = '&#9733;';
          if (favorites.indexOf(id) === -1) favorites.push(id);
        } else {
          btn.innerHTML = '&#9734;';
          var idx = favorites.indexOf(id);
          if (idx > -1) favorites.splice(idx, 1);
        }

        localStorage.setItem('pdr-favorites', JSON.stringify(favorites));
        updateFavoritesList();
      });
    });

    /* ===== FAVORITES LIST ===== */
    function updateFavoritesList() {
      var favorites = JSON.parse(localStorage.getItem('pdr-favorites') || '[]');
      var list = document.getElementById('favorites-list');
      if (!list) return;

      if (favorites.length === 0) {
        list.innerHTML = '<li class="favorites-empty">No favorites yet. Star studies to add them here.</li>';
        return;
      }

      var html = '';
      favorites.forEach(function(id) {
        var btn = document.querySelector('.action-btn.star[data-id="' + id + '"]');
        var card = btn ? (btn.closest('.landmark-card') || btn.closest('.wn-card')) : null;
        var title = id;
        if (card) {
          var titleEl = card.querySelector('.landmark-title') || card.querySelector('.wn-title');
          if (titleEl) title = titleEl.textContent;
        }
        html += '<li><span>' + title + '</span><button class="action-btn" onclick="removeFavorite(\\'' + id + '\\')" style="opacity:1;font-size:0.9rem;color:var(--english-red);padding:2px 6px;" title="Remove">\\u2715</button></li>';
      });
      list.innerHTML = html;
    }

    function removeFavorite(id) {
      var favorites = JSON.parse(localStorage.getItem('pdr-favorites') || '[]');
      var idx = favorites.indexOf(id);
      if (idx > -1) favorites.splice(idx, 1);
      localStorage.setItem('pdr-favorites', JSON.stringify(favorites));

      var btn = document.querySelector('.action-btn.star[data-id="' + id + '"]');
      if (btn) {
        btn.classList.remove('active');
        btn.innerHTML = '&#9734;';
      }
      updateFavoritesList();
    }

    /* ===== EXPORT FAVORITES TO CLIPBOARD ===== */
    function exportFavorites() {
      var favorites = JSON.parse(localStorage.getItem('pdr-favorites') || '[]');
      if (favorites.length === 0) {
        alert('No favorites to export. Star some studies first!');
        return;
      }

      var text = 'Paging Dr. Oh \\u2014 Starred Studies\\n';
      text += '========================================\\n\\n';

      favorites.forEach(function(id, i) {
        var btn = document.querySelector('.action-btn.star[data-id="' + id + '"]');
        var card = btn ? (btn.closest('.landmark-card') || btn.closest('.wn-card')) : null;
        var title = id;
        var meta = '';
        if (card) {
          var titleEl = card.querySelector('.landmark-title') || card.querySelector('.wn-title');
          if (titleEl) title = titleEl.textContent;
          var metaEl = card.querySelector('.landmark-meta') || card.querySelector('.wn-source');
          if (metaEl) meta = metaEl.textContent.trim();
        }
        text += (i + 1) + '. ' + title + '\\n   ' + meta + '\\n\\n';
      });

      navigator.clipboard.writeText(text).then(function() {
        var exportBtn = document.getElementById('export-btn');
        var original = exportBtn.textContent;
        exportBtn.textContent = 'Copied!';
        setTimeout(function() { exportBtn.textContent = original; }, 2000);
      }).catch(function() {
        var ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        var exportBtn = document.getElementById('export-btn');
        var original = exportBtn.textContent;
        exportBtn.textContent = 'Copied!';
        setTimeout(function() { exportBtn.textContent = original; }, 2000);
      });
    }

    /* ===== RANDOMIZE BUTTON ===== */
    var randomizeBtn = document.getElementById('randomize-btn');
    if (randomizeBtn) {
      randomizeBtn.addEventListener('click', function() {
        randomizeBtn.textContent = 'Loading\\u2026';
        randomizeBtn.disabled = true;
        setTimeout(function() {
          randomizeBtn.textContent = 'Randomize Disease';
          randomizeBtn.disabled = false;
          alert('A brand-new disease is generated every day at 6 AM UTC.  Come back tomorrow for a fresh topic!');
        }, 600);
      });
    }

    /* ===== INITIALIZE ON LOAD ===== */
    (function init() {
      var favorites = JSON.parse(localStorage.getItem('pdr-favorites') || '[]');
      document.querySelectorAll('.action-btn.star').forEach(function(btn) {
        if (favorites.indexOf(btn.getAttribute('data-id')) > -1) {
          btn.classList.add('active');
          btn.innerHTML = '&#9733;';
        }
      });
      updateFavoritesList();
    })();
"""


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8 — PAGE ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════

def build_full_page(css, disease_html, whatsnew_html, landmark_html, archive_html, calc_ids):
    """Assemble the complete index.html."""

    # Build calculator JS for any calculators used on the page
    calc_js = "\n"
    for cid in calc_ids:
        if cid in CALCULATORS:
            calc_js += build_calculator_js(cid) + "\n"

    full_js = calc_js + BASE_JS

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Paging Dr. Oh &mdash; Evidence at the Bedside</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Lora:ital,wght@0,400;0,600;0,700;1,400&family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
{css}
  </style>
</head>
<body>

  <!-- HEADER -->
  <header class="site-header">
    <h1>Paging Dr. Oh</h1>
    <p class="tagline">Evidence at the Bedside &mdash; Updated Daily</p>
  </header>

  <!-- TAB NAVIGATION -->
  <nav class="tab-nav">
    <button class="tab-btn active" data-tab="disease">Disease of the Day</button>
    <button class="tab-btn" data-tab="whatsnew">What's New</button>
    <button class="tab-btn" data-tab="landmark">Landmark Study</button>
    <button class="tab-btn" data-tab="archive">Archive</button>
  </nav>

  <main>

    <!-- TAB 1: DISEASE OF THE DAY -->
    <section id="disease" class="tab-content active">
{disease_html}
    </section>

    <!-- TAB 2: WHAT'S NEW -->
    <section id="whatsnew" class="tab-content">
{whatsnew_html}
    </section>

    <!-- TAB 3: LANDMARK STUDY -->
    <section id="landmark" class="tab-content">
{landmark_html}
    </section>

    <!-- TAB 4: ARCHIVE -->
    <section id="archive" class="tab-content">
{archive_html}
    </section>

  </main>

  <!-- FOOTER -->
  <footer class="site-footer">
    <p>Paging Dr. Oh &mdash; For educational purposes only. Not a substitute for clinical judgment.</p>
    <p>Content generated via AI with evidence-based sources. Always verify with primary literature.</p>
  </footer>

  <script>
{full_js}
  </script>

</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════
# SECTION 9 — STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

def pick_disease(history):
    """Pick a disease, avoiding the last 30 shown."""
    recent = history.get("diseases_shown", [])[-30:]
    candidates = [d for d in DISEASES if d not in recent]
    if not candidates:
        # All diseases shown recently — reset and allow any
        candidates = list(set(DISEASES))
    return random.choice(candidates)


def pick_landmark_study(studies, history):
    """Pick a landmark study not recently shown."""
    recent_ids = history.get("landmark_studies_shown", [])[-60:]
    candidates = [s for s in studies if s.get("id") not in recent_ids]
    if not candidates:
        candidates = studies  # reset if all shown
    return random.choice(candidates) if candidates else {"id": "unknown", "name": "Unknown", "year": 0, "journal": "N/A", "one_liner": ""}


def rotate_archive(current_items, archive):
    """Move items older than 30 days from current to archive."""
    kept = []
    for item in current_items:
        date_iso = item.get("date_iso", "")
        if date_iso and date_iso < THIRTY_DAYS_AGO:
            archive.append(item)
        else:
            kept.append(item)
    return kept, archive


def update_history(history, disease_name, study_id, new_item_ids):
    """Update the history tracker."""
    history.setdefault("diseases_shown", []).append(disease_name)
    history.setdefault("landmark_studies_shown", []).append(study_id)
    history.setdefault("whats_new_ids", []).extend(new_item_ids)
    history["last_run"] = TODAY_ISO

    # Trim old history to keep file size reasonable
    history["diseases_shown"] = history["diseases_shown"][-90:]
    history["landmark_studies_shown"] = history["landmark_studies_shown"][-200:]
    history["whats_new_ids"] = history["whats_new_ids"][-500:]


# ═══════════════════════════════════════════════════════════════════════
# SECTION 10 — MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    print(f"=== Paging Dr. Oh — Daily Generation ===")
    print(f"Date: {TODAY_STR}")
    print()

    # 1. Load state files
    history          = load_json(HISTORY_PATH, default={"diseases_shown": [], "landmark_studies_shown": [], "whats_new_ids": [], "last_run": None})
    landmark_studies = load_json(LANDMARK_PATH, default=[])
    manual_additions = load_json(MANUAL_PATH, default=[])
    wn_current       = load_json(WN_PATH, default=[])
    archive          = load_json(ARCHIVE_PATH, default=[])

    # 2. Pick today's disease
    disease_name = pick_disease(history)
    print(f"Disease of the Day: {disease_name}")

    # 3. Pick today's landmark study
    if landmark_studies:
        study = pick_landmark_study(landmark_studies, history)
    else:
        print("WARNING: landmark_studies.json is empty or missing.")
        study = {"id": "placeholder", "name": "ARMA Trial", "acronym": "ARMA",
                 "year": 2000, "journal": "NEJM", "specialty": "Critical Care",
                 "one_liner": "Low tidal volume ventilation reduced ARDS mortality by 22%."}
    print(f"Landmark Study: {study.get('name', 'Unknown')}")

    # 4. Generate content via API
    print("\nGenerating Disease of the Day content...")
    disease_content = generate_disease_content(disease_name)

    print("Generating What's New content...")
    new_items = generate_whats_new(history)

    # Merge manual additions
    if isinstance(manual_additions, list) and manual_additions:
        for item in manual_additions:
            item.setdefault("date_iso", TODAY_ISO)
        new_items = manual_additions + new_items
        print(f"  Added {len(manual_additions)} manual addition(s).")

    print(f"  Found {len(new_items)} new item(s).")

    print("Generating Landmark Study content...")
    landmark_content = generate_landmark_content(study)

    # 5. Archive rotation
    wn_current, archive = rotate_archive(wn_current, archive)

    # 6. Add today's new items to the front of the current list
    wn_current = new_items + wn_current

    # 7. Extract CSS from template
    css = get_css_from_template()

    # 8. Determine which calculators are needed
    calc_ids = disease_content.get("calculators", [])
    if not calc_ids:
        calc_ids = DISEASE_CALCULATORS.get(disease_name, [])

    # 9. Build all tab HTML
    disease_html  = build_disease_tab(disease_content)
    whatsnew_html = build_whatsnew_tab(wn_current)
    landmark_html = build_landmark_tab(landmark_content)
    archive_html  = build_archive_tab(archive)

    # 10. Assemble full page
    page = build_full_page(css, disease_html, whatsnew_html, landmark_html, archive_html, calc_ids)

    # 11. Write index.html
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"\nWrote {OUTPUT_PATH} ({len(page):,} bytes)")

    # 12. Update state files
    new_item_ids = [item.get("id", "") for item in new_items if item.get("id")]
    update_history(history, disease_name, study.get("id", ""), new_item_ids)
    save_json(HISTORY_PATH, history)
    save_json(WN_PATH, wn_current)
    save_json(ARCHIVE_PATH, archive)
    save_json(MANUAL_PATH, [])  # Clear manual additions after processing

    print("State files updated.")
    print("=== Done! ===")


if __name__ == "__main__":
    main()
