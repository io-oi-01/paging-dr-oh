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

import os, sys, json, random, re, traceback, time
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

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
STYLES_PATH    = "styles.css"
TEMPLATE_PATH  = "template.html"
OUTPUT_PATH    = "index.html"
HISTORY_PATH   = "history.json"
LANDMARK_PATH  = "landmark_studies.json"
MANUAL_PATH    = "manual_additions.json"
WN_PATH        = "whats_new_current.json"
ARCHIVE_PATH   = "archive.json"
JDD_PATH       = "jdd_conditions.json"

# JDD Inclusive Derm Atlas
JDD_INDEX_URL  = "https://jddonline.com/project-atlas-a-z/"

# LITFL Case Study Sources
LITFL_CASES_PATH = "litfl_cases.json"
LITFL_CLINICAL_INDEX_URL = "https://litfl.com/clinical-cases/"

LITFL_CATEGORIES = {
    "ecg": {
        "label": "ECG of the Day",
        "pill_text": "ECG Case",
        "specialty": "Cardiology",
        "url_template": "https://litfl.com/ecg-case-{num:03d}/",
        "max_case": 137,
        "questions": [
            "What is the rate and rhythm?",
            "Are there ST-segment or T-wave changes?",
            "What is the axis?",
            "What is your interpretation?",
        ],
    },
    "cxr": {
        "label": "CXR of the Day",
        "pill_text": "CXR Case",
        "specialty": "Radiology",
        "url_template": "https://litfl.com/cxr-case-{num:03d}/",
        "max_case": 92,
        "questions": [
            "What are the key findings?",
            "Is the mediastinum normal?",
            "Are there any opacities or effusions?",
            "What is your interpretation?",
        ],
    },
    "ct": {
        "label": "CT of the Day",
        "pill_text": "CT Case",
        "specialty": "Radiology",
        "url_template": "https://litfl.com/ct-case-{num:03d}/",
        "max_case": 92,
        "questions": [
            "What organ system is primarily affected?",
            "Are there any masses, fluid collections, or abnormal enhancements?",
            "What is your interpretation?",
        ],
    },
    "clinical": {
        "label": "Clinical Case of the Day",
        "pill_text": "Clinical Case",
        "specialty": "Emergency Medicine",
        "questions": [
            "What is the most likely diagnosis?",
            "What key findings support your differential?",
            "What initial workup would you order?",
        ],
    },
}

LITFL_ANSWER_BOUNDARIES = [
    r'<h[23][^>]*>[^<]*(ANSWER|INTERPRETATION|CLINICAL PEARLS)[^<]*</h[23]>',
    r'<strong>\s*Q1\.',
    r'Reveal the .* answer',
    r'<details[^>]*>\s*<summary[^>]*>[^<]*(ANSWER|INTERPRETATION|KEY POINTS|REVEAL)',
]


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


def get_css():
    """Read the CSS from styles.css (single source of truth for all styling)."""
    try:
        with open(STYLES_PATH, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"WARNING: {STYLES_PATH} not found. Using empty CSS.")
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
            {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
        ]

    # Retry with exponential backoff for rate limits
    for attempt in range(3):
        try:
            response = CLIENT.messages.create(**kwargs)
            parts = []
            for block in response.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
            return "\n".join(parts)
        except Exception as e:
            err_str = str(e).lower()
            print(f"  API call error (attempt {attempt+1}/3): {type(e).__name__}: {e}")
            if "rate_limit" in err_str or "rate limit" in err_str or "429" in err_str:
                if attempt < 2:
                    wait = 60 * (attempt + 1)  # 60s, then 120s
                    print(f"  Rate limited. Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    raise
            elif "overloaded" in err_str or "529" in err_str:
                if attempt < 2:
                    wait = 30 * (attempt + 1)
                    print(f"  API overloaded. Waiting {wait}s before retry...")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise


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


def extract_json_array(text):
    """Extract the first JSON array from text, even if nested inside an object."""
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    start = text.find("[")
    if start == -1:
        raise ValueError("No JSON array found in response")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
        if depth == 0:
            return json.loads(text[start : i + 1])
    raise ValueError("Unterminated JSON array in response")


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5 — CONTENT GENERATION  (API prompts & calls)
# ═══════════════════════════════════════════════════════════════════════

def _strip_html_to_text(html, max_chars=6000):
    """Strip HTML tags and return plain text, truncated to max_chars."""
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<h[1-4][^>]*>', '\n\n### ', html, flags=re.IGNORECASE)
    html = re.sub(r'<li[^>]*>', '\n- ', html, flags=re.IGNORECASE)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<p[^>]*>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    html = re.sub(r'&amp;', '&', html)
    html = re.sub(r'&lt;', '<', html)
    html = re.sub(r'&gt;', '>', html)
    html = re.sub(r'&nbsp;', ' ', html)
    text = re.sub(r'\n{3,}', '\n\n', html)
    text = re.sub(r'[ \t]{2,}', ' ', text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "...[truncated]"
    return text


def get_statpearls_search_url(disease_name):
    """
    Return an NCBI Bookshelf search URL for the given disease's StatPearls article.
    Used as a source hint for the disease content prompt and as a displayed link.
    Note: Direct StatPearls fetching requires the web_search tool (Anthropic handles the scraping).
    """
    query = urllib.parse.quote(f"{disease_name} StatPearls")
    return f"https://www.ncbi.nlm.nih.gov/books/?term={query}"


def fetch_wikijournalclub_page(study_info):
    """
    Try to fetch WikiJournalClub content for a landmark study.
    Returns (text, url) or (None, None) if not found / fetch fails.
    """
    acronym = study_info.get("acronym", "")
    name = study_info.get("name", "")

    # Build candidate URLs to try (WJC uses MediaWiki URL format)
    candidates = []
    if acronym:
        candidates.append(f"https://www.wikijournalclub.org/wiki/{urllib.parse.quote(acronym)}")
    if name:
        name_slug = re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_')
        candidates.append(f"https://www.wikijournalclub.org/wiki/{urllib.parse.quote(name_slug)}")

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PagingDrOh/1.0"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                final_url = resp.geturl()
                html = resp.read().decode("utf-8", errors="replace")

            # If redirected to main page or search page, the article doesn't exist
            if "Main_Page" in final_url or "Special:" in final_url or len(html) < 4000:
                continue

            text = _strip_html_to_text(html, max_chars=6000)
            if len(text) < 500:
                continue

            print(f"    Fetched WikiJournalClub: {len(text)} chars from {url}")
            return text, url

        except Exception:
            continue

    print(f"    WikiJournalClub not found for: {acronym or name}")
    return None, None


# ─── JDD Inclusive Derm Atlas ─────────────────────────────────────────────

def fetch_jdd_index():
    """
    Scrape the JDD Inclusive Derm Atlas A-Z index page.
    Returns a list of {"name": "Condition Name", "url": "https://..."} dicts.
    Falls back to cached jdd_conditions.json if the fetch fails.
    """
    try:
        req = urllib.request.Request(JDD_INDEX_URL, headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Find all links to /project-atlas/inclusive-derm/<slug>/
        pattern = r'<a\s+[^>]*href="(/project-atlas/inclusive-derm/[^"]+)"[^>]*>([^<]+)</a>'
        matches = re.findall(pattern, html, re.IGNORECASE)

        conditions = []
        seen_urls = set()
        for path, name in matches:
            name = name.strip()
            if not name or len(name) < 3:
                continue
            url = f"https://jddonline.com{path}" if path.startswith("/") else path
            if url not in seen_urls:
                seen_urls.add(url)
                conditions.append({"name": name, "url": url})

        if conditions:
            print(f"    Fetched JDD A-Z index: {len(conditions)} conditions")
            # Cache for future fallback
            save_json(JDD_PATH, conditions)
            return conditions
        else:
            print("    WARNING: JDD index returned 0 conditions, using cache")
            return load_json(JDD_PATH, default=[])

    except Exception as e:
        print(f"    WARNING: JDD index fetch failed ({type(e).__name__}: {e}), using cache")
        return load_json(JDD_PATH, default=[])


def pick_jdd_condition(conditions, history):
    """Pick a JDD condition not recently shown."""
    recent = history.get("jdd_conditions_shown", [])[-60:]
    candidates = [c for c in conditions if c["name"] not in recent]
    if not candidates:
        candidates = conditions  # reset if all shown
    return random.choice(candidates) if candidates else None


def fetch_jdd_condition_page(url):
    """
    Scrape a JDD condition page for title, description, and image URLs.
    Returns {"title": str, "description": str, "images": [url, ...], "source_url": str}
    or None on failure.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Extract title from <h1>
        h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
        title = h1_match.group(1).strip() if h1_match else "Dermatology Image"

        # Extract description: first substantial <p> tag (>50 chars of text)
        p_matches = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL | re.IGNORECASE)
        description = ""
        for p_html in p_matches:
            p_text = re.sub(r'<[^>]+>', ' ', p_html).strip()
            p_text = re.sub(r'\s+', ' ', p_text)
            if len(p_text) > 50:
                description = p_text[:500]
                break

        # Extract image URLs — look for images from cms.sanovaworks.com
        img_matches = re.findall(r'<img\s+[^>]*src="([^"]*cms\.sanovaworks\.com[^"]*)"', html, re.IGNORECASE)
        # Also try general content images (non-logo, non-icon)
        if not img_matches:
            img_matches = re.findall(r'<img\s+[^>]*src="([^"]*(?:uploads|content|images)[^"]*\.(?:jpg|jpeg|png|webp))"', html, re.IGNORECASE)

        # Filter out tiny thumbnails if we have larger versions
        images = []
        for img_url in img_matches:
            # Ensure absolute URL
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                img_url = f"https://jddonline.com{img_url}"
            if img_url not in images:
                images.append(img_url)

        if not title and not images:
            return None

        print(f"    Fetched JDD page: {title} — {len(images)} image(s)")
        return {
            "title": title,
            "description": description,
            "images": images[:6],  # Cap at 6 images max
            "source_url": url,
        }

    except Exception as e:
        print(f"    WARNING: JDD condition page fetch failed ({type(e).__name__}: {e})")
        return None


# ── LITFL Case Study Functions ──────────────────────────────────────────

def fetch_litfl_clinical_index():
    """
    Scrape the LITFL clinical cases index page.
    Returns a list of {"slug": "descriptive-slug", "url": "https://..."} dicts.
    Falls back to cached litfl_cases.json if the fetch fails.
    """
    try:
        req = urllib.request.Request(LITFL_CLINICAL_INDEX_URL,
                                     headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        pattern = r'<a\s+[^>]*href="(https?://litfl\.com/([^"]+)/)"[^>]*>'
        matches = re.findall(pattern, html, re.IGNORECASE)

        cases = []
        seen = set()
        exclude_slugs = {"clinical-cases", "top-100", "ecg-library",
                         "cxr-library", "ct-scan-library", "category",
                         "litfl-top-100", "clinical-case-database"}
        for url, slug in matches:
            slug = slug.strip("/")
            if slug in seen or slug in exclude_slugs:
                continue
            if re.match(r'(ecg|cxr|ct)-case-\d+', slug):
                continue
            if slug.startswith("top-100") or slug.startswith("category/"):
                continue
            seen.add(slug)
            cases.append({"slug": slug, "url": url})

        if cases:
            print(f"    Fetched LITFL clinical index: {len(cases)} cases")
            save_json(LITFL_CASES_PATH, cases)
            return cases
        else:
            print("    WARNING: LITFL clinical index returned 0 cases, using cache")
            return load_json(LITFL_CASES_PATH, default=[])

    except Exception as e:
        print(f"    WARNING: LITFL clinical index fetch failed ({type(e).__name__}: {e}), using cache")
        return load_json(LITFL_CASES_PATH, default=[])


def pick_litfl_cases(history, clinical_cases=None):
    """
    Pick one LITFL case per category, avoiding recently shown.
    Returns dict: {"ecg": {"num": int, "url": str}, ...
                    "clinical": {"slug": str, "url": str}}.
    """
    recent = history.get("litfl_cases_shown", [])[-120:]
    picks = {}

    for cat_key, cfg in LITFL_CATEGORIES.items():
        if cat_key == "clinical":
            if not clinical_cases:
                picks[cat_key] = None
                continue
            candidates = [c for c in clinical_cases if f"clinical-{c['slug']}" not in recent]
            if not candidates:
                candidates = clinical_cases
            pick = random.choice(candidates) if candidates else None
            picks[cat_key] = pick
        else:
            max_num = cfg["max_case"]
            all_nums = list(range(1, max_num + 1))
            candidates = [n for n in all_nums if f"{cat_key}-{n:03d}" not in recent]
            if not candidates:
                candidates = all_nums
            num = random.choice(candidates)
            url = cfg["url_template"].format(num=num)
            picks[cat_key] = {"num": num, "url": url}

    return picks


def fetch_litfl_case_page(url, category):
    """
    Scrape a LITFL case page for clinical stem and images.
    Anti-spoiler: stops extracting at ANSWER/INTERPRETATION/CLINICAL PEARLS
    headings, <strong>Q1. markers, Reveal-the-answer blocks, or
    <details><summary> answer accordions.
    Returns {"clinical_stem": str, "images": [url, ...], "source_url": str}
    or None on failure.
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Find the LAST .entry-content block.  LITFL pages often have
        # 2-3 nested entry-content divs: outer ones hold navigation/search
        # widgets while the innermost one holds the actual article.
        entry_matches = list(re.finditer(
            r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>',
            html, re.IGNORECASE))
        if not entry_matches:
            return None
        # Use content starting from the LAST entry-content opening tag
        content = html[entry_matches[-1].end():]

        # ── Truncate at answer boundaries (ANTI-SPOILER) ──
        for boundary in LITFL_ANSWER_BOUNDARIES:
            m = re.search(boundary, content, re.IGNORECASE)
            if m:
                content = content[:m.start()]

        # Truncate at <hr> if it appears after the first image
        hr_positions = [m.start() for m in re.finditer(r'<hr\s*/?>', content, re.IGNORECASE)]
        img_positions = [m.start() for m in re.finditer(r'<img\s', content, re.IGNORECASE)]
        if hr_positions and img_positions:
            first_img = min(img_positions)
            post_img_hrs = [pos for pos in hr_positions if pos > first_img]
            if post_img_hrs:
                content = content[:post_img_hrs[0]]

        # ── Extract clinical stem (paragraph text before first figure/image) ──
        # The clinical vignette is in the first paragraph(s) before the
        # diagnostic image.  We stop at the first <figure> or clinical
        # <img> to avoid picking up breadcrumbs/navigation text.
        figure_pos = re.search(r'<figure[\s>]', content, re.IGNORECASE)
        stem_zone = content[:figure_pos.start()] if figure_pos else content[:2000]
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', stem_zone, re.DOTALL | re.IGNORECASE)
        stem_parts = []
        for p_html in paragraphs:
            p_text = re.sub(r'<[^>]+>', ' ', p_html).strip()
            p_text = re.sub(r'\s+', ' ', p_text)
            if len(p_text) > 20:
                stem_parts.append(p_text)

        clinical_stem = " ".join(stem_parts)[:800]

        # ── Extract image URLs (handle lazy-loading) ──
        # LITFL uses lazy-loaded images: the real URL lives in
        # data-orig-file, data-large-file, or data-lazy-src attributes
        # while src= is often an SVG placeholder.
        img_tags = re.findall(r'<img\s+[^>]+>', content, re.IGNORECASE)
        images = []
        skip_urls = [
            'avatar', 'logo', 'icon', 'gravatar', 'emoji', '/ads/',
            'banner', 'widget', 'badge', '1x1', 'pixel',
            'google-web-search', 'fastlane', 'litfl-life',
        ]
        skip_classes = ['default-logo', 'site-logo', 'avatar', 'emoji']
        for tag in img_tags:
            # Skip images with logo/branding CSS classes
            cls_match = re.search(r'class="([^"]+)"', tag, re.IGNORECASE)
            if cls_match:
                cls_val = cls_match.group(1).lower()
                if any(sk in cls_val for sk in skip_classes):
                    continue
            # Skip small images (icons, decorations)
            w_match = re.search(r'width="(\d+)"', tag, re.IGNORECASE)
            if w_match and int(w_match.group(1)) < 100:
                continue
            # Try data attributes first (real high-res URL)
            img_url = None
            for attr in ['data-orig-file', 'data-large-file', 'data-lazy-src']:
                m = re.search(rf'{attr}="([^"]+)"', tag, re.IGNORECASE)
                if m:
                    candidate = m.group(1)
                    if candidate and not candidate.startswith('data:'):
                        img_url = candidate
                        break
            # Fall back to src= if no data attr found
            if not img_url:
                m = re.search(r'src="([^"]+)"', tag, re.IGNORECASE)
                if m:
                    candidate = m.group(1)
                    if not candidate.startswith('data:'):
                        img_url = candidate

            if not img_url:
                continue

            # Skip non-clinical images by URL keywords
            if any(skip in img_url.lower() for skip in skip_urls):
                continue
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            elif img_url.startswith("/"):
                img_url = f"https://litfl.com{img_url}"
            if img_url not in images:
                images.append(img_url)

        if not clinical_stem and not images:
            return None

        print(f"    Fetched LITFL {category}: {len(images)} image(s), "
              f"{len(clinical_stem)} chars stem")
        return {
            "clinical_stem": clinical_stem,
            "images": images[:4],
            "source_url": url,
        }

    except Exception as e:
        print(f"    WARNING: LITFL {category} page fetch failed "
              f"({type(e).__name__}: {e})")
        return None

def generate_disease_content(disease_name, source_url=None, use_web_search=True):
    """
    Call Claude to generate the Disease of the Day content.
    Uses web search targeting StatPearls / NCBI Bookshelf as the primary source.
    source_url: a pre-constructed StatPearls search URL to include in the header link.
    use_web_search: if False, skip web search tool (fallback when search unavailable).
    """
    statpearls_url = source_url or get_statpearls_search_url(disease_name)
    if use_web_search:
        source_block = f"""Use web search to find the StatPearls / NCBI Bookshelf article for {disease_name}.
Search: site:ncbi.nlm.nih.gov/books {disease_name} StatPearls

CRITICAL ACCURACY RULES:
1. Base content PRIMARILY on what you find in StatPearls / NCBI Bookshelf.
2. Supplement with other peer-reviewed sources only for sections not covered.
3. Never invent diagnostic test statistics (Sn, Sp, LR, NNT) — only include them when found in sources.
4. Include the StatPearls article URL you found in the "source_url" field of the response.
5. Mark any content from your training data (not from sources) with "Clinical context:" prefix."""
    else:
        source_block = f"""Provide evidence-based content on {disease_name} using your medical knowledge.
Reference StatPearls and NCBI Bookshelf as primary sources.
Use the StatPearls search URL for the source_url: {statpearls_url}

CRITICAL ACCURACY RULES:
1. Only include diagnostic test statistics (Sn, Sp, LR, NNT) you are confident about.
2. Mark any uncertain content with "Clinical context:" prefix."""
    use_search = use_web_search

    prompt = f"""You are a medical education content creator for "Paging Dr. Oh," a daily
clinical reference for internal medicine physicians and hospitalists.

Today's disease is: **{disease_name}**
Today's date is: {TODAY_STR}

{source_block}

FORMAT EVERYTHING AS BULLET POINTS for rapid bedside scanning.

Rules:
- Every bullet should be an HTML <li> element.
- Use <strong> for key terms.  Use HTML entities for symbols (&ge; &le; &rarr; &mdash; &lt; &gt;).
- Include diagnostic test statistics INLINE (Sn, Sp, PPV, NPV, +LR, -LR) wherever data exists in the source.
- Add superscript reference numbers like <sup>1</sup> throughout the text.
- Treatment must be stratified by clinical setting (outpatient / inpatient non-ICU / ICU when applicable).
- Include 5-10 numbered references from peer-reviewed sources (PubMed preferred).
- Suggest 1-3 relevant clinical calculator IDs from this list ONLY:
  {json.dumps(list(CALCULATORS.keys()))}
  Pick only calculators that are clinically relevant to {disease_name}. If none fit, use [].
- Suggest 2-4 society guidelines with URL links.

Return ONLY valid JSON (no markdown fences, no extra text):

{{
  "disease_name": "{disease_name}",
  "source_url": "URL of the StatPearls/NCBI article you found, or {statpearls_url}",
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
        raw = call_claude(prompt, use_search=use_search, max_tokens=16000)
        return parse_json_response(raw)
    except Exception as e:
        print(f"ERROR generating disease content: {e}")
        traceback.print_exc()
        return {
            "disease_name": disease_name,
            "source_url": statpearls_url,
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


# ═══════════════════════════════════════════════════════════════════════
# SECTION 5b — RSS FEED SYSTEM  (What's New content via journal feeds)
# ═══════════════════════════════════════════════════════════════════════

RSS_FEED_URL   = "https://www.2minutemedicine.com/feed/"
RSS_FEED_LABEL = "2 Minute Medicine"

MEDSCAPE_FEED_URL   = "https://www.medscape.com/cx/rssfeeds/2700.xml"
MEDSCAPE_FEED_LABEL = "Medscape"

# Namespaces commonly used in RSS/RDF feeds
RSS_NAMESPACES = {
    "rss1":  "http://purl.org/rss/1.0/",
    "dc":    "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/basic/2.0/",
    "atom":  "http://www.w3.org/2005/Atom",
}


def fetch_rss_items(label, url, max_items=10):
    """Fetch and parse an RSS feed, returning a list of {title, link, date, source, description, categories}."""
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_xml = resp.read()
        root = ET.fromstring(raw_xml)
    except Exception as e:
        print(f"    WARNING: Could not fetch {label} RSS: {e}")
        return []

    # --- RSS 2.0 (<channel><item>...) ---
    for item_el in root.findall(".//item")[:max_items]:
        title = (item_el.findtext("title") or "").strip()
        link  = (item_el.findtext("link") or "").strip()
        date  = (item_el.findtext("pubDate") or
                 item_el.findtext(f"{{{RSS_NAMESPACES['dc']}}}date") or "").strip()
        # Extract description text (strip HTML tags for a clean summary)
        desc_raw = item_el.findtext("description") or ""
        desc_text = re.sub(r"<[^>]+>", " ", desc_raw).strip()
        desc_text = re.sub(r"\s+", " ", desc_text)[:500]  # clean up whitespace, cap at 500 chars
        # Extract categories
        categories = [cat.text.strip() for cat in item_el.findall("category") if cat.text]
        if title:
            items.append({"title": title, "link": link, "date": date, "source": label,
                          "description": desc_text, "categories": categories})

    return items


def fetch_feed():
    """Fetch the 2 Minute Medicine RSS feed."""
    print(f"    Fetching {RSS_FEED_LABEL}...")
    articles = fetch_rss_items(RSS_FEED_LABEL, RSS_FEED_URL, max_items=10)
    print(f"      Got {len(articles)} article(s).")
    return articles


def fetch_medscape_feed():
    """Fetch the Medscape Medical News RSS feed (up to 20 items)."""
    print(f"    Fetching {MEDSCAPE_FEED_LABEL}...")
    articles = fetch_rss_items(MEDSCAPE_FEED_LABEL, MEDSCAPE_FEED_URL, max_items=20)
    print(f"      Got {len(articles)} Medscape article(s).")
    return articles


def filter_medscape_relevance(articles):
    """Pre-filter Medscape articles by keyword/category relevance scoring.

    Pure Python — no Claude API call. Reduces 20 RSS items to ~10-12 before
    sending to Claude for curation.

    Scoring tiers:
      3 pts — high-yield hospitalist / critical care terms
      2 pts — core IM subspecialties and study types
      1 pt  — adjacent specialties
     -5 pts — exclude: irrelevant / consumer health topics
     +1 pt  — news-type category boost

    Threshold: score >= 2. Returns top 12 sorted by score (descending).
    """
    # ── Keyword tiers (matched against title + description + categories) ──
    HIGH_YIELD = [                                                       # 3 pts
        'hospitalist', 'critical care', 'intensive care', 'icu',
        'sepsis', 'heart failure', 'fda approves', 'fda approval',
        'guideline update', 'practice-changing', 'practice changing',
        'vte', 'pulmonary embolism', 'dvt', 'aki', 'acute kidney',
        'copd', 'ards', 'pneumonia', 'atrial fibrillation', 'afib',
        'acute coronary', 'myocardial infarction', 'mi ',
        'cardiac arrest', 'shock', 'intubation', 'mechanical ventilation',
        'vasopressor', 'delirium', 'gi bleeding', 'cirrhosis',
        'diabetic ketoacidosis', 'dka', 'hyponatremia', 'hyperkalemia',
    ]
    MEDIUM = [                                                           # 2 pts
        'internal medicine', 'emergency medicine', 'cardiology',
        'pulmonology', 'pulmonary', 'infectious disease',
        'nephrology', 'gastroenterology', 'endocrinology',
        'neurology', 'hematology', 'oncology', 'rheumatology',
        'diabetes', 'hypertension', 'stroke', 'antibiotic',
        'antimicrobial', 'vaccine', 'clinical trial', 'meta-analysis',
        'randomized', 'mortality', 'covid', 'influenza', 'rsv',
        'transplant', 'transfusion', 'thrombosis', 'anticoagul',
        'statin', 'insulin', 'opioid', 'pain management',
        'palliative', 'end of life',
    ]
    LOW = [                                                              # 1 pt
        'primary care', 'geriatrics', 'surgery', 'surgical',
        'psychiatry', 'radiology', 'public health', 'epidemiology',
        'anesthesiology', 'pediatrics', 'ob-gyn', 'obstetrics',
    ]
    EXCLUDE = [                                                          # -5 pts
        'cosmetic', 'aesthetic', 'wellness', 'veterinary', 'dental',
        'optometry', 'ophthalmology', 'dermatology', 'medical billing',
        'career advice', 'medical school', 'residency match',
        'patient satisfaction', 'medical tourism', 'weight loss supplement',
        'alternative medicine', 'homeopathy', 'chiropractic',
    ]
    NEWS_CATEGORIES = ['news', 'news alert', 'clinical summary']         # +1 pt

    scored = []
    for article in articles:
        text = ' '.join([
            article.get('title', ''),
            article.get('description', ''),
            ' '.join(article.get('categories', [])),
        ]).lower()

        score = 0
        for kw in HIGH_YIELD:
            if kw in text:
                score += 3
                break  # one hit per tier is enough
        for kw in MEDIUM:
            if kw in text:
                score += 2
                break
        for kw in LOW:
            if kw in text:
                score += 1
                break
        for kw in EXCLUDE:
            if kw in text:
                score -= 5
                break
        # Category boost
        cats_lower = [c.lower() for c in article.get('categories', [])]
        if any(nc in cats_lower for nc in NEWS_CATEGORIES):
            score += 1

        scored.append((score, article))

    # Filter and sort
    passing = [(s, a) for s, a in scored if s >= 2]
    passing.sort(key=lambda x: x[0], reverse=True)
    result = [a for _, a in passing[:12]]
    print(f"    Medscape relevance filter: {len(articles)} -> {len(result)} articles (threshold >= 2)")
    return result


def curate_medscape_items(articles, existing_ids):
    """Send pre-filtered Medscape articles to Claude for curation.

    Single Claude call (no PubMed lookup needed). Claude selects 3-5 items,
    classifies them, and generates summary + why_it_matters fields.

    Returns list of curated item dicts with card_type='medscape'.
    """
    if not articles:
        return []

    article_summaries = [
        {"index": i, "title": a["title"], "description": a.get("description", "")[:400],
         "categories": a.get("categories", []), "date": a["date"][:30], "link": a["link"]}
        for i, a in enumerate(articles)
    ]

    prompt = f"""You are a medical news curator for a hospitalist / internal medicine physician.

Below are {len(article_summaries)} recent articles from Medscape Medical News.
Select the 3-5 MOST important items for a hospitalist, internist, EM, or critical care physician.

SELECTION CRITERIA — prioritize:
- Practice-changing news: new guidelines, FDA approvals, drug safety alerts
- High-impact clinical studies reported in Medscape
- Infectious disease outbreaks, vaccine updates, public health alerts
- Conference highlights with immediate clinical relevance

Skip any items with these IDs (already covered): {json.dumps(existing_ids[-50:])}

ARTICLE TYPE — classify each as one of:
- "news" — general medical news
- "fda-regulatory" — FDA approvals, warnings, safety alerts
- "guideline" — guideline updates or consensus statements
- "conference" — conference highlights or presentations
- "clinical-summary" — clinical reviews or practice updates

For each selected item, return a JSON array:
[
  {{
    "article_index": 0,
    "id": "ms-short-slug-{TODAY.year}",
    "article_type": "news",
    "specialty": "e.g. Cardiology",
    "title": "Headline exactly as given",
    "summary": "2-3 sentence summary of the news item. Be factual and concise.",
    "why_it_matters": "1-2 sentences explaining relevance to hospitalists/IM physicians.",
    "tags": ["keyword1", "keyword2", "keyword3"]
  }}
]

IMPORTANT RULES:
- IDs MUST start with "ms-" prefix
- Do NOT fabricate study_design, sample_size, PICO fields, or confidence levels
- summary should be a prose paragraph, NOT bullet points
- why_it_matters should focus on how this affects clinical practice
- tags should be 2-4 relevant clinical keywords

Return ONLY valid JSON (no markdown fences, no extra text)."""

    try:
        raw = call_claude(prompt, use_search=False, max_tokens=3000)
        print(f"  DEBUG: Medscape Claude response length: {len(raw)} chars")
        selected = extract_json_array(raw)
        print(f"  DEBUG: Parsed {len(selected)} Medscape items from response")
    except Exception as e:
        print(f"  WARNING: Medscape curation call failed: {e}")
        return []

    # Enrich with source metadata from original RSS data
    items = []
    for sel in selected:
        idx = sel.get("article_index")
        if idx is not None and idx < len(articles):
            article = articles[idx]
            sel["source"] = MEDSCAPE_FEED_LABEL
            sel["source_url"] = article["link"]
            sel["date"] = article["date"][:30] if article["date"] else TODAY_STR
            sel["date_iso"] = TODAY_ISO
            sel["card_type"] = "medscape"
        items.append(sel)
    return items


def fetch_2mm_article(url):
    """Fetch a 2 Minute Medicine article page and extract DOI/PMID/journal clues.

    Returns dict with keys: doi, pmid, journal_clue, article_text (first ~2000 chars of body text).
    All values may be empty strings if not found. No API calls — just HTTP + regex.
    """
    result = {"doi": "", "pmid": "", "journal_clue": "", "article_text": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    WARNING: Could not fetch 2MM article: {e}")
        return result

    # Extract DOI (common patterns in 2MM articles)
    doi_match = re.search(r'(?:doi\.org/|DOI:\s*)(10\.\d{4,}/[^\s"<>]+)', html)
    if doi_match:
        result["doi"] = doi_match.group(1).rstrip(".")

    # Extract PMID
    pmid_match = re.search(r'(?:pubmed\.ncbi\.nlm\.nih\.gov/|PMID:\s*)(\d{7,9})', html)
    if pmid_match:
        result["pmid"] = pmid_match.group(1)

    # Extract journal name clue from common patterns
    journal_match = re.search(r'(?:published in|appeared in|from)\s+(?:the\s+)?([A-Z][A-Za-z\s&]+?)(?:\.|,|\s+on\s)', html)
    if journal_match:
        result["journal_clue"] = journal_match.group(1).strip()[:80]

    # Extract body text (strip HTML, take first ~2000 chars for context)
    body_match = re.search(r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    if not body_match:
        body_match = re.search(r'<article[^>]*>(.*?)</article>', html, re.DOTALL)
    if body_match:
        text = re.sub(r'<[^>]+>', ' ', body_match.group(1))
        text = re.sub(r'\s+', ' ', text).strip()
        result["article_text"] = text[:2000]

    return result


def search_pubmed(query, doi=None):
    """Search PubMed via E-utilities for a PMID. Free, no API key needed.

    Tries DOI first (exact match), then title search.
    Returns PMID string or empty string if not found.
    """
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

    # Try DOI first (most reliable)
    if doi:
        params = urllib.parse.urlencode({
            "db": "pubmed", "term": f"{doi}[DOI]", "retmode": "json", "retmax": "1"
        })
        try:
            req = urllib.request.Request(f"{base}?{params}", headers={"User-Agent": "PagingDrOh/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            ids = data.get("esearchresult", {}).get("idlist", [])
            if ids:
                return ids[0]
        except Exception:
            pass

    # Fallback: title search
    if query:
        # Clean up query: remove special chars, limit length
        clean_q = re.sub(r'[^\w\s]', '', query)[:200]
        params = urllib.parse.urlencode({
            "db": "pubmed", "term": f"{clean_q}[Title]", "retmode": "json", "retmax": "1"
        })
        try:
            req = urllib.request.Request(f"{base}?{params}", headers={"User-Agent": "PagingDrOh/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            ids = data.get("esearchresult", {}).get("idlist", [])
            if ids:
                return ids[0]
        except Exception:
            pass

    return ""


def fetch_pubmed_abstract(pmid):
    """Fetch a structured abstract from PubMed via E-utilities efetch.

    Returns dict with: title, authors, abstract, journal, doi, pub_date.
    All values may be empty strings. No API key needed.
    """
    result = {"title": "", "authors": "", "abstract": "", "journal": "", "doi": "", "pub_date": ""}
    if not pmid:
        return result

    url = (f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
           f"?db=pubmed&id={pmid}&rettype=xml&retmode=xml")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PagingDrOh/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_xml = resp.read()
        root = ET.fromstring(raw_xml)
    except Exception as e:
        print(f"    WARNING: PubMed fetch failed for PMID {pmid}: {e}")
        return result

    article = root.find(".//MedlineCitation/Article")
    if article is None:
        return result

    # Title
    result["title"] = (article.findtext("ArticleTitle") or "").strip()

    # Authors (first 3 + et al.)
    authors = []
    for author in article.findall(".//AuthorList/Author")[:3]:
        last = author.findtext("LastName") or ""
        initials = author.findtext("Initials") or ""
        if last:
            authors.append(f"{last} {initials}".strip())
    total_authors = len(article.findall(".//AuthorList/Author"))
    if total_authors > 3:
        authors.append("et al.")
    result["authors"] = ", ".join(authors)

    # Abstract — handle structured (labeled sections) and plain
    abstract_parts = []
    for abstract_text in article.findall(".//Abstract/AbstractText"):
        label = abstract_text.get("Label", "")
        text = "".join(abstract_text.itertext()).strip()
        if label:
            abstract_parts.append(f"{label}: {text}")
        elif text:
            abstract_parts.append(text)
    result["abstract"] = "\n".join(abstract_parts)

    # Journal
    result["journal"] = (article.findtext(".//Journal/Title") or "").strip()

    # DOI
    for aid in root.findall(".//PubmedData/ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            result["doi"] = (aid.text or "").strip()
            break

    # Publication date
    pub_date = article.find(".//Journal/JournalIssue/PubDate")
    if pub_date is not None:
        year = pub_date.findtext("Year") or ""
        month = pub_date.findtext("Month") or ""
        result["pub_date"] = f"{month} {year}".strip()

    return result


def curate_rss_items(rss_articles, existing_ids):
    """Send 2 Minute Medicine article summaries to Claude to pick the most relevant."""
    if not rss_articles:
        return []

    # Build compact list for Claude — include description for better context
    article_summaries = [
        {"index": i, "title": a["title"], "description": a.get("description", "")[:300],
         "categories": a.get("categories", []), "date": a["date"][:30], "link": a["link"]}
        for i, a in enumerate(rss_articles)
    ]

    prompt = f"""You are a medical news curator for a hospitalist / internal medicine physician.

Below are {len(article_summaries)} recent article summaries from 2 Minute Medicine.
Select the 5-10 most relevant items for a hospitalist/IM doctor.

SELECTION CRITERIA — prioritize:
- Studies with direct impact on hospital or outpatient IM practice
- FDA drug approvals, safety alerts, new indications
- Practice-changing RCTs, meta-analyses, or guideline updates
- Public health updates relevant to clinical practice

RANKING: Prefer items with higher IM relevance and practice-changing potential.

Skip any items with these IDs (already covered): {json.dumps(existing_ids[-50:])}

ARTICLES:
{json.dumps(article_summaries, indent=2)}

ACCURACY RULES — these are strict:
1. source_summary_bullets: Summarize ONLY what is stated in the title and description.
   Use 1-3 bullets. Do NOT invent statistics or results not mentioned.
2. clinical_interpretation_bullets: 1-2 bullets of your clinical context/relevance.
   These MUST start with "Clinical context:" so they are clearly labeled as your assessment.
3. sample_size and primary_outcome: ONLY fill these if explicitly stated in the description.
   Use empty string "" if not mentioned.
4. limitations: list only real limitations you can infer from the study design, or leave as [].
5. confidence: "high" for RCTs/meta-analyses; "moderate" for observational studies,
   guidelines; "preliminary" for news items or FDA actions without trial data.

For each selected item, return a JSON array:
[
  {{
    "article_index": 0,
    "id": "short-slug-{TODAY.year}",
    "type": "RCT" | "Meta-Analysis" | "Guideline" | "Review" | "FDA Action" | "Observational" | "Public Health",
    "specialty": "e.g. Cardiology",
    "title": "Full title exactly as given",
    "study_design": "Brief description of study design, or empty string",
    "sample_size": "e.g. N=3,572 or empty string if not stated",
    "primary_outcome": "e.g. 30-day all-cause mortality or empty string if not stated",
    "source_summary_bullets": ["bullet summarizing what the article says"],
    "clinical_interpretation_bullets": ["Clinical context: why this matters in practice"],
    "limitations": ["known limitation for this study design"],
    "bottom_line": "One-sentence clinical takeaway",
    "confidence": "high" | "moderate" | "preliminary"
  }}
]

Return ONLY valid JSON (no markdown fences, no extra text)."""

    try:
        raw = call_claude(prompt, use_search=False, max_tokens=4000)
        print(f"  DEBUG: Claude raw response length: {len(raw)} chars")
        selected = extract_json_array(raw)
        print(f"  DEBUG: Parsed {len(selected)} items from response")
    except Exception as e:
        print(f"  WARNING: Curation call failed: {e}")
        print(f"  DEBUG: Raw response preview: {raw[:500] if 'raw' in dir() else 'N/A'}")
        return []

    # Enrich with source info from the original RSS data
    items = []
    for sel in selected:
        idx = sel.get("article_index")
        if idx is not None and idx < len(rss_articles):
            article = rss_articles[idx]
            sel["source"] = RSS_FEED_LABEL
            sel["source_url"] = article["link"]
            sel["date"] = article["date"][:30] if article["date"] else TODAY_STR
            sel["date_iso"] = TODAY_ISO
        items.append(sel)
    return items


def enrich_whats_new(curated_items, primary_sources):
    """Use Claude to build rich cards from curated items + primary-source abstracts.

    curated_items: list of dicts from curate_rss_items()
    primary_sources: dict mapping item index → {source_basis, abstract, pubmed_data, ...}

    Returns list of enriched item dicts with new fields. One Claude API call.
    """
    if not curated_items:
        return curated_items

    # Build the enrichment payload — pair each item with its primary source material
    items_for_claude = []
    for i, item in enumerate(curated_items):
        src = primary_sources.get(i, {})
        entry = {
            "index": i,
            "title": item.get("title", ""),
            "type": item.get("type", ""),
            "specialty": item.get("specialty", ""),
            "twomm_description": item.get("source_summary_bullets", []),
            "source_basis": src.get("source_basis", "2MM summary only"),
            "abstract_text": src.get("abstract", "")[:3000],
            "pubmed_title": src.get("pubmed_title", ""),
            "pubmed_authors": src.get("pubmed_authors", ""),
            "pubmed_journal": src.get("pubmed_journal", ""),
            "pubmed_doi": src.get("pubmed_doi", ""),
            "original_source_url": src.get("original_source_url", ""),
        }
        items_for_claude.append(entry)

    prompt = f"""You are an evidence-based medicine summarizer for a hospitalist / internal medicine physician.

Below are {len(items_for_claude)} medical studies/updates. Each includes:
- A title and type from 2 Minute Medicine (discovery source)
- The source_basis label indicating what primary-source material is available
- If available: the PubMed abstract text (the primary source)
- If no abstract: only the 2MM summary bullets (fallback)

For EACH item, build a structured card. When a PubMed abstract is available, derive ALL
factual content from that abstract — not from 2MM. When only 2MM is available, use that
but keep the card shorter and lower-confidence.

ITEMS:
{json.dumps(items_for_claude, indent=2)}

ACCURACY RULES (strict):
1. key_results: Extract ONLY from the abstract or stated source. Do NOT invent statistics.
2. population, intervention_exposure, comparator: ONLY fill if explicitly stated. Use "" if unknown.
3. limitations: Only real limitations inferable from the study design. Use [] if uncertain.
4. clinical_interpretation: YOUR clinical assessment — clearly separate from source findings.
5. If source_basis is "2MM summary only": keep card shorter, set confidence to "moderate" or "preliminary",
   and do NOT invent methodological details not present in the 2MM description.
6. practice_readiness: "Practice-changing", "Informative", or "Preliminary" — be conservative.

Return a JSON array with one object per item:
[
  {{
    "index": 0,
    "why_this_matters": "1-2 sentence plain-language clinical relevance",
    "study_design": "Design description from abstract, or empty string",
    "population": "Study population from abstract, or empty string",
    "intervention_exposure": "Intervention or exposure, or empty string",
    "comparator": "Control/comparator group, or empty string",
    "primary_outcome": "Primary outcome measure, or empty string",
    "key_results": ["Finding 1 with stats", "Finding 2"],
    "limitations": ["Limitation 1"],
    "clinical_interpretation": ["Your clinical assessment of what this means"],
    "practice_readiness": "Practice-changing" | "Informative" | "Preliminary",
    "bottom_line": "One-sentence clinical takeaway"
  }}
]

Return ONLY valid JSON (no markdown fences, no extra text)."""

    try:
        raw = call_claude(prompt, use_search=False, max_tokens=8000)
        enriched = extract_json_array(raw)
        print(f"  Enrichment: got {len(enriched)} enriched cards")
    except Exception as e:
        print(f"  WARNING: Enrichment call failed: {e}. Using curated items as-is.")
        return curated_items

    # Merge enriched data back into curated items
    enrichment_map = {e.get("index", -1): e for e in enriched}
    for i, item in enumerate(curated_items):
        enr = enrichment_map.get(i, {})
        src = primary_sources.get(i, {})

        # New enriched fields (overwrite curation-level fields if enrichment succeeded)
        if enr:
            item["why_this_matters"] = enr.get("why_this_matters", "")
            item["study_design"] = enr.get("study_design", "") or item.get("study_design", "")
            item["population"] = enr.get("population", "")
            item["intervention_exposure"] = enr.get("intervention_exposure", "")
            item["comparator"] = enr.get("comparator", "")
            item["primary_outcome"] = enr.get("primary_outcome", "") or item.get("primary_outcome", "")
            item["key_results"] = enr.get("key_results", item.get("source_summary_bullets", []))
            item["limitations"] = enr.get("limitations", item.get("limitations", []))
            item["clinical_interpretation"] = enr.get("clinical_interpretation", item.get("clinical_interpretation_bullets", []))
            item["practice_readiness"] = enr.get("practice_readiness", "")
            item["bottom_line"] = enr.get("bottom_line", "") or item.get("bottom_line", "")

        # Source metadata (from HTTP scraping, not Claude)
        item["source_basis"] = src.get("source_basis", "2MM summary only")
        item["original_source_url"] = src.get("original_source_url", "")
        item["pubmed_id"] = src.get("pmid", "")
        # Rename source_url → twomm_url, keep source_url for backward compat
        item["twomm_url"] = item.get("source_url", "")

    return curated_items


def generate_whats_new(history):
    """Fetch 2MM + Medscape RSS -> curate -> find primary sources -> enrich -> merge.

    Two independent pipelines:
      1. 2MM: RSS -> Claude curate -> PubMed lookup -> Claude enrich (2 Claude calls)
      2. Medscape: RSS -> Python relevance filter -> Claude curate (1 Claude call)
    Results merged and sorted by recency. Medscape failure cannot break 2MM.
    """
    existing_ids = history.get("whats_new_ids", [])

    # ── Pipeline 1: 2 Minute Medicine (unchanged) ──
    print("  Fetching 2 Minute Medicine RSS feed...")
    rss_articles = fetch_feed()
    print(f"  Total 2MM articles fetched: {len(rss_articles)}")

    twomm_items = []
    if rss_articles:
        # Claude call #1 - curate 5-10 best 2MM items
        print("  Asking Claude to curate 2MM items...")
        twomm_items = curate_rss_items(rss_articles, existing_ids)
        print(f"  Claude selected {len(twomm_items)} 2MM item(s).")

        if twomm_items:
            # PubMed lookup (HTTP only, no API cost)
            print("  Searching for primary sources (PubMed) - parallel...")

            def _lookup_primary_source(i, item):
                """Look up a single item's primary source. Thread-safe HTTP calls only."""
                twomm_url = item.get("source_url", "")
                title = item.get("title", "")
                src = {"source_basis": "2MM summary only", "abstract": "", "pmid": "",
                       "pubmed_title": "", "pubmed_authors": "", "pubmed_journal": "",
                       "pubmed_doi": "", "original_source_url": twomm_url}
                try:
                    clues = fetch_2mm_article(twomm_url) if twomm_url else {}
                    doi = clues.get("doi", "")
                    pmid = clues.get("pmid", "")
                    if not pmid:
                        pmid = search_pubmed(title, doi=doi)
                    if pmid:
                        pubmed_data = fetch_pubmed_abstract(pmid)
                        if pubmed_data.get("abstract"):
                            src["source_basis"] = "PubMed abstract"
                            src["abstract"] = pubmed_data["abstract"]
                            src["pmid"] = pmid
                            src["pubmed_title"] = pubmed_data.get("title", "")
                            src["pubmed_authors"] = pubmed_data.get("authors", "")
                            src["pubmed_journal"] = pubmed_data.get("journal", "")
                            src["pubmed_doi"] = pubmed_data.get("doi", "")
                            if pubmed_data.get("doi"):
                                src["original_source_url"] = f"https://doi.org/{pubmed_data['doi']}"
                            else:
                                src["original_source_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                            print(f"    [{i+1}] Found PubMed abstract (PMID: {pmid}) - {title[:50]}")
                        else:
                            print(f"    [{i+1}] PMID {pmid} found but no abstract - {title[:50]}")
                    else:
                        print(f"    [{i+1}] No PubMed match - {title[:50]}")
                except Exception as e:
                    print(f"    [{i+1}] PubMed lookup error: {e}")
                return i, src

            primary_sources = {}
            with ThreadPoolExecutor(max_workers=3) as pubmed_pool:
                futures = [pubmed_pool.submit(_lookup_primary_source, i, item) for i, item in enumerate(twomm_items)]
                for future in as_completed(futures):
                    idx, src = future.result()
                    primary_sources[idx] = src

            found_count = sum(1 for s in primary_sources.values() if s["source_basis"] != "2MM summary only")
            print(f"  Primary sources found: {found_count}/{len(twomm_items)}")

            # Claude call #2 - enrich 2MM items with primary-source material
            print("  Enriching 2MM cards from primary sources...")
            twomm_items = enrich_whats_new(twomm_items, primary_sources)
    else:
        print("  WARNING: No 2MM RSS articles fetched. Skipping 2MM curation.")

    # ── Pipeline 2: Medscape (independent, failure-isolated) ──
    medscape_items = []
    try:
        print("  Fetching Medscape RSS feed...")
        ms_articles = fetch_medscape_feed()
        if ms_articles:
            # Pure Python relevance filter (no API cost)
            ms_filtered = filter_medscape_relevance(ms_articles)
            if ms_filtered:
                # Claude call #3 - curate Medscape items (single call)
                print("  Asking Claude to curate Medscape items...")
                medscape_items = curate_medscape_items(ms_filtered, existing_ids)
                print(f"  Claude selected {len(medscape_items)} Medscape item(s).")
        else:
            print("  WARNING: No Medscape articles fetched.")
    except Exception as e:
        print(f"  WARNING: Medscape pipeline failed ({type(e).__name__}: {e})")
        print("  Continuing with 2MM items only.")

    # ── Merge and sort by recency ──
    all_items = twomm_items + medscape_items
    if all_items:
        all_items = _sort_by_recency(all_items)
    print(f"  Total What's New items: {len(twomm_items)} 2MM + {len(medscape_items)} Medscape = {len(all_items)}")

    return all_items


def dedup_and_merge(new_items, existing_items):
    """Compare today's new items against existing 30-day items to find duplicates.

    Uses Claude (no web search) to identify when the same study/guideline/FDA action
    was reported by different sources. Merges duplicates by incrementing mention_count
    on the existing item and removing the duplicate from new_items.

    Returns (remaining_new_items, updated_existing_items).
    """
    if not new_items or not existing_items:
        return new_items, existing_items

    # Build compact summaries to keep token count low
    new_summaries = [
        {"index": i, "title": it.get("title", ""), "type": it.get("type", ""),
         "source": it.get("source", ""), "key_findings": it.get("key_findings", "")[:200]}
        for i, it in enumerate(new_items)
    ]
    existing_summaries = [
        {"index": i, "title": it.get("title", ""), "type": it.get("type", ""),
         "source": it.get("source", ""), "key_findings": it.get("key_findings", "")[:200]}
        for i, it in enumerate(existing_items)
    ]

    prompt = f"""You are a medical content deduplication assistant.

Below are two lists of medical news items. "new_items" were just fetched today.
"existing_items" are already in the database from previous days.

Identify any new_item that covers the SAME underlying study, guideline, or FDA action
as an existing_item — even if reported by a different source or with different wording.

NEW ITEMS:
{json.dumps(new_summaries, indent=2)}

EXISTING ITEMS:
{json.dumps(existing_summaries, indent=2)}

Return ONLY a valid JSON object (no markdown fences):
{{
  "merges": [
    {{"new_index": 0, "existing_index": 5, "reason": "Both cover the same trial"}}
  ]
}}

If there are no duplicates, return: {{"merges": []}}"""

    try:
        raw = call_claude(prompt, use_search=False, max_tokens=2000)
        result = parse_json_response(raw)
        merges = result.get("merges", [])
    except Exception as e:
        print(f"  WARNING: Dedup call failed: {e}. Skipping merge step.")
        return new_items, existing_items

    # Apply merge instructions
    merged_new_indices = set()
    for merge in merges:
        new_idx = merge.get("new_index")
        existing_idx = merge.get("existing_index")
        if new_idx is None or existing_idx is None:
            continue
        if new_idx >= len(new_items) or existing_idx >= len(existing_items):
            continue

        new_item = new_items[new_idx]
        existing_item = existing_items[existing_idx]

        # Increment mention count
        existing_item["mention_count"] = existing_item.get("mention_count", 1) + 1

        # Add new source to sources_seen
        sources = existing_item.get("sources_seen", [existing_item.get("source", "Unknown")])
        new_source = new_item.get("source", "Unknown")
        if new_source not in sources:
            sources.append(new_source)
        existing_item["sources_seen"] = sources

        # Update last_seen, preserve first_seen
        existing_item["last_seen"] = TODAY_ISO

        merged_new_indices.add(new_idx)
        print(f"    Merged: \"{new_item.get('title', '')[:60]}\" → existing (now {existing_item['mention_count']} mentions)")

    # Remove merged items from new_items
    remaining = [item for i, item in enumerate(new_items) if i not in merged_new_indices]
    return remaining, existing_items


def remove_wn_duplicates(items):
    """Remove duplicate What's New items based on id, source_url, and normalized title.

    Keeps the FIRST occurrence (most recent, since newest items are prepended).
    No LLM call needed — pure Python string matching.
    """
    if not items:
        return items

    seen_ids   = set()
    seen_urls  = set()
    seen_titles = set()
    unique = []

    for item in items:
        # Check by id
        item_id = item.get("id", "").strip()
        if item_id and item_id in seen_ids:
            continue

        # Check by source_url or twomm_url
        url = (item.get("source_url", "") or item.get("twomm_url", "")).strip().rstrip("/")
        if url and url in seen_urls:
            if item_id:
                seen_ids.add(item_id)
            continue

        # Check by normalized title (lowercase, strip punctuation/whitespace)
        title = item.get("title", "")
        norm_title = re.sub(r'[^a-z0-9]', '', title.lower())
        if norm_title and norm_title in seen_titles:
            if item_id:
                seen_ids.add(item_id)
            if url:
                seen_urls.add(url)
            continue

        # Not a duplicate — keep it
        if item_id:
            seen_ids.add(item_id)
        if url:
            seen_urls.add(url)
        if norm_title:
            seen_titles.add(norm_title)
        unique.append(item)

    removed = len(items) - len(unique)
    if removed:
        print(f"  Removed {removed} duplicate(s) from What's New ({len(unique)} items remaining).")
    return unique



def _sort_by_recency(items):
    """Sort What's New items by date (newest first).

    Handles both ISO dates (2026-03-12) and RFC-822 dates
    (Wed, 12 Mar 2026 10:00:00 GMT) from different RSS sources.
    Items without parseable dates sort to the end.
    """
    def _parse_date(item):
        date_iso = item.get("date_iso", "")
        if date_iso:
            try:
                return datetime.fromisoformat(date_iso.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        date_str = item.get("date", "")
        if not date_str:
            return datetime.min
        # Try ISO format first
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass
        # Try RFC-822 format (from RSS pubDate)
        for fmt in ["%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                     "%d %b %Y %H:%M:%S %Z", "%d %b %Y"]:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except (ValueError, TypeError):
                continue
        return datetime.min

    return sorted(items, key=_parse_date, reverse=True)

def generate_landmark_content(study_info, source_text=None, source_url=None):
    """
    Call Claude to generate a deep-dive analysis of a landmark trial.
    If source_text (from WikiJournalClub) is provided, Claude extracts from it.
    Otherwise generates from training data.
    """
    trial_name = study_info.get('name', 'Unknown')
    trial_year = study_info.get('year', 'Unknown')
    trial_journal = study_info.get('journal', 'Unknown')
    trial_brief = study_info.get('one_liner', '')

    if source_text:
        source_block = f"""SOURCE TEXT (WikiJournalClub — {source_url or ""}):
---
{source_text}
---

ACCURACY RULES:
1. Extract statistics (ARR, RRR, NNT, CI, p-values) ONLY from the source text above.
2. Do NOT invent numbers not present in the source.
3. If a field is not covered in the source, write "See source for details." """
    else:
        source_block = f"""Use your knowledge of this well-known trial to provide accurate statistics
(ARR, RRR, NNT, 95% CI, p-values). This is a landmark study so cite specific numbers."""

    prompt = f"""You are a medical education content creator for "Paging Dr. Oh."

Analyze this landmark clinical trial for an internal medicine physician audience:

Trial: {trial_name}
Year: {trial_year}
Journal: {trial_journal}
Brief: {trial_brief}

{source_block}

Return ONLY valid JSON (no markdown fences):
{{
  "title": "{trial_name}",
  "meta": "{trial_journal} &bull; {trial_year} &bull; Authors/Group",
  "source_url": "{source_url or ""}",
  "clinical_question": "What clinical question did this trial address?",
  "study_design": "Design, setting, methodology, randomization, blinding...",
  "population": "Who was enrolled, key inclusion/exclusion criteria, sample size (N=?)...",
  "intervention": "What was the intervention arm?",
  "comparator": "What was the comparator/control arm?",
  "primary_endpoint": "Primary outcome measure...",
  "key_findings": "Results with specific statistics (ARR, RRR, NNT, 95% CI, p-values)...",
  "strengths": "Major methodological strengths...",
  "weaknesses": "Limitations and major criticisms...",
  "practice_impact": "How did this trial change clinical practice?",
  "why_it_matters_now": "Current relevance, how subsequent evidence refined the findings..."
}}"""
    try:
        raw = call_claude(prompt, use_search=False, max_tokens=8000)
        return parse_json_response(raw)
    except Exception as e:
        print(f"WARNING: Landmark study generation failed: {e}")
        return {
            "title": trial_name,
            "meta": f"{trial_journal} &bull; {trial_year}",
            "source_url": source_url or "",
            "clinical_question": "Content generation failed. Please try again tomorrow.",
            "study_design": "", "population": "", "intervention": "", "comparator": "",
            "primary_endpoint": "", "key_findings": "", "strengths": "",
            "weaknesses": "", "practice_impact": "", "why_it_matters_now": "",
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


def _build_single_disease_html(content):
    """Build the inner HTML for one disease (no outer pool wrapper)."""
    d = content
    disease_name = d.get("disease_name", "Unknown")
    source_url = d.get("source_url", "")

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

    # -- Source link at bottom of disease page --
    if source_url:
        html += f"""
      <div class="statpearls-wrap" style="margin-top:24px;">
        <a href="{source_url}" target="_blank" rel="noopener" class="statpearls-link">Read on StatPearls &#8594;</a>
      </div>\n"""

    return html


def build_disease_tab(content_pool):
    """
    Build the Disease of the Day tab HTML.
    content_pool is a list of disease content dicts (1-3 items).
    The first item is shown by default; others are hidden and revealed by Randomize.
    """
    if not content_pool:
        return '<p style="color:var(--gray);">Content unavailable. Try again tomorrow.</p>'

    html = ""
    for i, content in enumerate(content_pool):
        display = '' if i == 0 else ' style="display:none"'
        html += f'      <div class="disease-view" id="disease-view-{i}"{display}>\n'
        html += _build_single_disease_html(content)
        html += "\n      </div>\n"

    if len(content_pool) > 1:
        html += f"""
      <div class="randomize-wrap">
        <button class="btn btn-primary" onclick="randomizeDisease({len(content_pool)})">&#8635; Show Me Another Disease</button>
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


def _build_wn_card(item, is_trending=False):
    """Build HTML for a single What's New card. Handles both old and enriched formats."""
    wn_id = item.get("id", "wn-unknown")
    item_type = item.get("type", "Study")
    specialty = item.get("specialty", "")
    source = item.get("source", "")
    date = item.get("date", "")
    title = item.get("title", "")
    study_design = item.get("study_design", "")
    sample_size = item.get("sample_size", "")
    primary_outcome = item.get("primary_outcome", "")
    bottom_line = item.get("bottom_line", "")
    confidence = item.get("confidence", "moderate")

    # New enriched fields (with backward-compatible fallbacks)
    source_basis = item.get("source_basis", "")
    why_this_matters = item.get("why_this_matters", "")
    population = item.get("population", "")
    intervention_exposure = item.get("intervention_exposure", "")
    comparator = item.get("comparator", "")
    key_results = item.get("key_results", item.get("source_summary_bullets", []))
    clinical_interpretation = item.get("clinical_interpretation", item.get("clinical_interpretation_bullets", []))
    limitations = item.get("limitations", [])
    practice_readiness = item.get("practice_readiness", "")
    original_source_url = item.get("original_source_url", "")
    twomm_url = item.get("twomm_url", item.get("source_url", "#"))
    # Primary link: prefer original source, fallback to 2MM
    primary_link = original_source_url if original_source_url else twomm_url

    conf_class = f"confidence-{confidence}"
    conf_label = confidence.capitalize()
    card_class = "wn-card"

    # Source basis badge
    basis_badge = ""
    if source_basis and source_basis != "2MM summary only":
        basis_badge = f'<span class="wn-basis-badge">Based on: {source_basis}</span>'
    elif source_basis == "2MM summary only":
        basis_badge = '<span class="wn-basis-badge wn-basis-fallback">Based on: 2MM summary</span>'

    html = f"""
      <div class="{card_class}">
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
        {basis_badge}
        <div class="wn-title">{title}</div>"""

    # Why This Matters (new enriched field)
    if why_this_matters:
        html += f"""        <div class="wn-why-matters">{why_this_matters}</div>\n"""

    # Study design + sample size + primary outcome row
    meta_parts = []
    if study_design:
        meta_parts.append(f"<strong>Design:</strong> {study_design}")
    if sample_size:
        meta_parts.append(f"<strong>N:</strong> {sample_size}")
    if primary_outcome:
        meta_parts.append(f"<strong>Outcome:</strong> {primary_outcome}")
    if meta_parts:
        html += f"""        <div class="wn-study-meta">{"&ensp;&bull;&ensp;".join(meta_parts)}</div>\n"""

    # PICO details (new enriched fields — only show if at least one is populated)
    pico_parts = []
    if population:
        pico_parts.append(f"<strong>Population:</strong> {population}")
    if intervention_exposure:
        pico_parts.append(f"<strong>Intervention:</strong> {intervention_exposure}")
    if comparator:
        pico_parts.append(f"<strong>Comparator:</strong> {comparator}")
    if pico_parts:
        pico_html = "</div><div class='wn-pico-item'>".join(pico_parts)
        html += f"""        <div class="wn-pico"><div class="wn-pico-item">{pico_html}</div></div>\n"""

    # Key Results (replaces source_summary_bullets for enriched cards)
    if key_results:
        label = "Key Results" if source_basis and source_basis != "2MM summary only" else "From the Source"
        bullets_html = "".join(f"<li>{b}</li>" for b in key_results)
        html += f"""        <div class="wn-section-label">{label}</div>
        <ul class="wn-bullets">{bullets_html}</ul>\n"""

    # Clinical Interpretation (replaces clinical_interpretation_bullets)
    if clinical_interpretation:
        bullets_html = "".join(f"<li>{b}</li>" for b in clinical_interpretation)
        html += f"""        <div class="wn-section-label wn-interp-label">Clinical Interpretation</div>
        <ul class="wn-bullets wn-interp-bullets">{bullets_html}</ul>\n"""

    # Limitations
    if limitations:
        lim_html = "".join(f"<li>{l}</li>" for l in limitations)
        html += f"""        <div class="wn-section-label">Limitations</div>
        <ul class="wn-bullets wn-limitations">{lim_html}</ul>\n"""

    # Bottom line
    if bottom_line:
        html += f"""        <div class="wn-bottom-line">
          <strong>Bottom Line:</strong> {bottom_line}
        </div>\n"""

    # Footer: confidence + practice readiness + links
    readiness_html = ""
    if practice_readiness:
        readiness_html = f'<span class="wn-readiness">&#x1F4CA; {practice_readiness}</span>'

    html += f"""        <div class="wn-footer">
          <div class="confidence {conf_class}">
            <span class="confidence-dot"></span>
            {conf_label} confidence
            {readiness_html}
          </div>
          <div class="wn-links">
            <a href="{primary_link}" class="source-link" target="_blank" rel="noopener">View Source &#8594;</a>"""

    # Secondary link to 2MM if primary link is different
    if original_source_url and twomm_url and original_source_url != twomm_url:
        html += f"""
            <a href="{twomm_url}" class="wn-secondary-link" target="_blank" rel="noopener">2MM &#8594;</a>"""

    html += """
          </div>
        </div>
      </div>\n"""

    return html


def _build_medscape_card(item):
    """Build HTML for a single Medscape news card.

    Different schema from 2MM cards: no PICO, no confidence, no study_design.
    Shows: article_type pill, specialty pill, title, summary paragraph,
    why_it_matters box, keyword tags, and Medscape source link.
    """
    wn_id = item.get("id", "ms-unknown")
    article_type = item.get("article_type", "news")
    specialty = item.get("specialty", "")
    source = item.get("source", "Medscape")
    date = item.get("date", "")
    title = item.get("title", "")
    summary = item.get("summary", "")
    why_it_matters = item.get("why_it_matters", "")
    tags = item.get("tags", [])
    source_url = item.get("source_url", "#")

    # Article type pill class mapping
    type_pill_map = {
        "fda-regulatory": "pill-fda",
        "guideline": "pill-guideline",
        "news": "pill-medscape-news",
        "conference": "pill-medscape-conf",
        "clinical-summary": "pill-medscape-clinical",
    }
    type_pill = type_pill_map.get(article_type, "pill-medscape-news")

    # Human-readable article type labels
    type_labels = {
        "fda-regulatory": "FDA / Regulatory",
        "guideline": "Guideline",
        "news": "News",
        "conference": "Conference",
        "clinical-summary": "Clinical Summary",
    }
    type_label = type_labels.get(article_type, article_type.replace("-", " ").title())

    parts = []
    parts.append(f'      <div class="wn-card medscape-card">')
    parts.append(f'        <div class="wn-card-header">')
    parts.append(f'          <div class="wn-tags">')
    parts.append(f'            <span class="pill {type_pill}">{type_label}</span>')
    if specialty:
        parts.append(f'            <span class="pill pill-specialty">{specialty}</span>')
    parts.append(f'          </div>')
    parts.append(f'          <div class="card-actions">')
    parts.append(f'            <button class="action-btn star" title="Star this article" data-id="{wn_id}">&#9734;</button>')
    parts.append(f'          </div>')
    parts.append(f'        </div>')
    parts.append(f'        <div class="wn-source"><span class="medscape-source-badge">&#9679; {source}</span> &bull; {date}</div>')
    parts.append(f'        <div class="wn-title">{title}</div>')

    if summary:
        parts.append(f'        <div class="medscape-summary">{summary}</div>')

    if why_it_matters:
        parts.append(f'        <div class="medscape-why-matters"><strong>Why it matters:</strong> {why_it_matters}</div>')

    if tags:
        tag_chips = ''.join(f'<span class="medscape-tag">{t}</span>' for t in tags[:4])
        parts.append(f'        <div class="medscape-tags">{tag_chips}</div>')

    parts.append(f'        <div class="wn-footer">')
    parts.append(f'          <div class="medscape-source-badge">&#9679; Medscape</div>')
    parts.append(f'          <div class="wn-links">')
    parts.append(f'            <a href="{source_url}" class="source-link" target="_blank" rel="noopener">Read on Medscape &#8594;</a>')
    parts.append(f'          </div>')
    parts.append(f'        </div>')
    parts.append(f'      </div>')

    return chr(10).join(parts) + chr(10)


def build_whatsnew_tab(items):
    """Build the What's New tab HTML. Dispatches to source-specific card builders."""
    parts = []
    parts.append('')
    parts.append('      <h3 class="section-title" style="margin-top:0;">Latest Updates (Past 30 Days)</h3>')
    parts.append('      <p style="color:var(--text-muted,var(--gray)); font-size:0.85rem; margin-bottom:16px;">Selected from <a href="https://www.2minutemedicine.com" target="_blank" rel="noopener">2 Minute Medicine</a> and <a href="https://www.medscape.com" target="_blank" rel="noopener">Medscape</a>, enriched from primary sources</p>')
    html = chr(10).join(parts) + chr(10)

    if not items:
        html += '      <p style="color:var(--gray); font-style:italic; padding:20px 0;">No updates yet. Check back tomorrow!</p>' + chr(10)
        return html

    for item in items:
        if item.get("card_type") == "medscape":
            html += _build_medscape_card(item)
        else:
            html += _build_wn_card(item, is_trending=False)

    return html


def build_landmark_tab(content):
    """Build the Landmark Study tab HTML with enhanced fields."""
    c = content
    title = c.get("title", "Unknown Trial")
    meta = c.get("meta", "")
    source_url = c.get("source_url", "")

    # Enhanced section order (new fields + renamed existing)
    sections_order = [
        ("clinical_question",  "Clinical Question"),
        ("study_design",       "Study Design"),
        ("population",         "Population"),
        ("intervention",       "Intervention"),
        ("comparator",         "Comparator / Control"),
        ("primary_endpoint",   "Primary Endpoint"),
        ("key_findings",       "Key Findings"),
        ("strengths",          "Strengths"),
        ("weaknesses",         "Weaknesses / Limitations"),
        ("practice_impact",    "Practice Impact"),
        ("why_it_matters_now", "Why It Matters Now"),
        # Legacy field names (backward compat with old cached data)
        ("what_changed",       "What Changed in Practice"),
        ("critics_said",       "What Critics Said"),
        ("where_it_stands_now", "Where It Stands Now"),
    ]

    wjc_link = (
        f'<a href="{source_url}" target="_blank" rel="noopener" class="statpearls-link" style="font-size:0.8rem;">'
        f'Read on WikiJournalClub &#8594;</a>'
        if source_url else ""
    )

    safe_id = re.sub(r'[^a-z0-9-]', '-', title.lower())[:30]
    html = f"""
      <div class="landmark-card">
        <div class="landmark-header">
          <h3 class="landmark-title">{title}</h3>
          <button class="action-btn star" data-id="{safe_id}" title="Star this study">&#9734;</button>
        </div>
        <div class="landmark-meta">{meta}</div>\n"""

    seen_labels = set()
    for key, label in sections_order:
        if label in seen_labels:
            continue
        text = c.get(key, "")
        if text and text != "See source for details.":
            seen_labels.add(label)
            html += f"""
        <div class="landmark-section">
          <h4>{label}</h4>
          <p>{text}</p>
        </div>\n"""

    # Source link at the bottom of the card
    if wjc_link:
        html += f"        {wjc_link}\n"

    html += "      </div>\n"

    return html


# ═══════════════════════════════════════════════════════════════════════
# SECTION 7b — PODCASTS
# ═══════════════════════════════════════════════════════════════════════

# ── Podcast configuration ──────────────────────────────────────────
# To add a new podcast:
#   1. Add an entry here with all fields filled in
#   2. Find the RSS feed URL (check Apple Podcasts or the podcast website)
#   3. The "pill_tag" appears as a secondary pill next to "PODCAST"
#   4. "fallback_description" is shown only when the RSS feed is unreachable
PODCAST_CONFIGS = [
    {
        "id": "core-im",
        "podcast_name": "Core IM",
        "podcast_slug": "core-im",
        "pill_tag": "5 Pearls",
        "apple_podcasts_url": "https://podcasts.apple.com/us/podcast/core-im-internal-medicine-podcast/id1297791208",
        "website_url": "https://www.coreimpodcast.com",
        "rss_feed_url": "https://feeds.redcircle.com/2c03e755-c428-4b8e-9150-95ef1ed2492b",
        "fallback_description": "Exploring the core topics of internal medicine with deep-dive episodes on diagnosis, management, and clinical reasoning.",
    },
    {
        "id": "curbsiders",
        "podcast_name": "The Curbsiders",
        "podcast_slug": "the-curbsiders",
        "pill_tag": "Deep Dive",
        "apple_podcasts_url": "https://podcasts.apple.com/us/podcast/the-curbsiders-internal-medicine-podcast/id1198732014",
        "website_url": "https://thecurbsiders.com",
        "rss_feed_url": "https://audioboom.com/channels/5034728.rss",
        "fallback_description": "Internal medicine podcast bringing you clinical pearls and practice-changing knowledge through expert interviews.",
    },
    {
        "id": "clinical-problem-solvers",
        "podcast_name": "The Clinical Problem Solvers",
        "podcast_slug": "clinical-problem-solvers",
        "pill_tag": "Clinical Reasoning",
        "apple_podcasts_url": "https://podcasts.apple.com/us/podcast/the-clinical-problem-solvers/id1446215559",
        "website_url": "https://clinicalproblemsolving.com",
        "rss_feed_url": "https://clinicalproblemsolving.com/category/episodes/feed/",
        "fallback_description": "Tackling clinical reasoning through real cases and schema-based approaches to internal medicine.",
    },
    {
        "id": "harrisons-podclass",
        "podcast_name": "Harrison's PodClass",
        "podcast_slug": "harrisons-podclass",
        "pill_tag": "Board Review",
        "apple_podcasts_url": "https://podcasts.apple.com/us/podcast/harrisons-podclass-internal-medicine-cases-and-board-prep/id1453765092",
        "website_url": "https://www.buzzsprout.com/259885",
        "rss_feed_url": "https://rss.buzzsprout.com/259885.rss",
        "fallback_description": "Bite-sized internal medicine education from the authority behind Harrison's Principles of Internal Medicine.",
    },
]

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def fetch_podcast_latest(config):
    """Fetch the latest episode from a podcast RSS feed.

    Returns dict with structured fields. No LLM call — just HTTP + XML parsing.
    Falls back gracefully if feed is unavailable.
    """
    result = {
        "id": config["id"],
        "podcast_name": config["podcast_name"],
        "podcast_slug": config["podcast_slug"],
        "pill_tag": config.get("pill_tag", ""),
        "apple_podcasts_url": config["apple_podcasts_url"],
        "website_url": config.get("website_url", ""),
        "artwork_url": "",
        "latest_episode_title": "",
        "latest_episode_url": "",
        "latest_episode_date": "",
        "latest_episode_summary": "",
        "extraction_status": "failed",
        "fallback_used": True,
        "fallback_description": config.get("fallback_description", ""),
    }

    rss_url = config.get("rss_feed_url", "")
    if not rss_url:
        return result

    try:
        req = urllib.request.Request(rss_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PagingDrOh/1.0; +https://io-oi.neocities.org)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw_xml = resp.read()
        root = ET.fromstring(raw_xml)
    except Exception as e:
        print(f"    WARNING: Could not fetch podcast feed for {config['podcast_name']}: {e}")
        return result

    channel = root.find("channel") or root
    itunes_ns = f"{{{ITUNES_NS}}}"

    # Channel-level artwork
    itunes_img = channel.find(f"{itunes_ns}image")
    if itunes_img is not None:
        result["artwork_url"] = itunes_img.get("href", "")
    if not result["artwork_url"]:
        img_url_el = channel.find("image/url")
        if img_url_el is not None and img_url_el.text:
            result["artwork_url"] = img_url_el.text.strip()

    # Latest episode (first <item>)
    item = channel.find("item")
    if item is None:
        return result

    result["latest_episode_title"] = (item.findtext("title") or "").strip()

    # Episode link
    result["latest_episode_url"] = (item.findtext("link") or "").strip()

    # Episode date
    result["latest_episode_date"] = (item.findtext("pubDate") or "").strip()[:30]

    # Episode summary — try itunes:summary, then itunes:subtitle, then description
    summary = (item.findtext(f"{itunes_ns}summary") or "").strip()
    if not summary:
        summary = (item.findtext(f"{itunes_ns}subtitle") or "").strip()
    if not summary:
        desc_raw = item.findtext("description") or ""
        summary = re.sub(r'<[^>]+>', ' ', desc_raw).strip()
        summary = re.sub(r'\s+', ' ', summary)
    # Cap summary at 300 chars for card display
    if len(summary) > 300:
        summary = summary[:297].rsplit(' ', 1)[0] + "..."
    result["latest_episode_summary"] = summary

    # Episode-level artwork (override channel if present)
    ep_img = item.find(f"{itunes_ns}image")
    if ep_img is not None and ep_img.get("href"):
        result["artwork_url"] = ep_img.get("href", "")

    result["extraction_status"] = "success"
    result["fallback_used"] = False

    return result


def fetch_all_podcasts():
    """Fetch latest episode metadata for all configured podcasts. No LLM calls."""
    print("  Fetching podcast feeds...")
    podcasts = []
    for config in PODCAST_CONFIGS:
        print(f"    Fetching {config['podcast_name']}...")
        podcast = fetch_podcast_latest(config)
        status = "OK" if podcast["extraction_status"] == "success" else "fallback"
        ep = podcast.get("latest_episode_title", "")[:50]
        print(f"      [{status}] {ep}")
        podcasts.append(podcast)
        time.sleep(0.3)  # Be polite to feed servers
    ok_count = sum(1 for p in podcasts if p["extraction_status"] == "success")
    print(f"  Podcasts: {ok_count}/{len(podcasts)} feeds fetched successfully")
    return podcasts


def build_podcast_tab(podcasts):
    """Build the Podcasts tab HTML. Episode-first cards, mobile-friendly.

    Card hierarchy: podcast name -> latest episode title -> date -> description -> links.
    Falls back gracefully if RSS feed was unreachable.
    """
    html = '\n      <h3 class="section-title" style="margin-top:0;">Podcasts</h3>\n'
    html += '      <p style="color:var(--text-muted,var(--gray)); font-size:0.85rem; margin-bottom:16px;">Latest episodes from high-yield internal medicine podcasts</p>\n'

    if not podcasts:
        html += '      <p style="color:var(--text-muted); font-style:italic;">No podcast data available. Check back tomorrow!</p>\n'
        return html

    html += '      <div class="podcast-grid">\n'

    for p in podcasts:
        html += _build_one_podcast_card(p)

    html += '      </div>\n'
    return html


def _build_one_podcast_card(p):
    """Render a single podcast episode card. Shared rendering for all podcasts.

    Keeps podcast name visible but puts latest episode front-and-center.
    If the RSS feed was unreachable, shows 'Latest episode unavailable' with a
    link to Apple Podcasts / the podcast website.
    """
    name = p.get("podcast_name", "Unknown Podcast")
    pill_tag = p.get("pill_tag", "")
    artwork = p.get("artwork_url", "")
    ep_title = p.get("latest_episode_title", "")
    ep_date = p.get("latest_episode_date", "")
    ep_summary = p.get("latest_episode_summary", "")
    apple_url = p.get("apple_podcasts_url", "#")
    website_url = p.get("website_url", "")
    ep_url = p.get("latest_episode_url", "")
    fallback_desc = p.get("fallback_description", "")

    # -- Pills --
    pills_html = '<span class="pill pill-podcast">Podcast</span>'
    if pill_tag:
        pills_html += f' <span class="pill pill-specialty">{pill_tag}</span>'

    # -- Artwork or letter placeholder --
    if artwork:
        img_html = f'<img src="{artwork}" alt="{name}" class="podcast-artwork" loading="lazy">'
    else:
        img_html = f'<div class="podcast-artwork podcast-artwork-placeholder">{name[0]}</div>'

    # -- Episode content (or graceful fallback) --
    if ep_title:
        nice_date = ""
        if ep_date:
            try:
                parsed = datetime.strptime(ep_date.strip()[:25], "%a, %d %b %Y %H:%M:%S")
                nice_date = parsed.strftime("%B %d, %Y")
            except Exception:
                nice_date = ep_date[:16]

        ep_html = f'<div class="podcast-ep-title">{ep_title}</div>\n'
        if nice_date:
            ep_html += f'              <div class="podcast-ep-date">{nice_date}</div>\n'
        if ep_summary:
            ep_html += f'          <div class="podcast-ep-summary">{ep_summary}</div>\n'
    else:
        # Fallback: feed was unreachable -- keep card visible
        ep_html = '<div class="podcast-ep-title" style="color:var(--text-muted); font-style:italic;">Latest episode unavailable</div>\n'
        if fallback_desc:
            ep_html += f'          <div class="podcast-ep-summary">{fallback_desc}</div>\n'

    # -- Action links --
    buttons_html = f'<a href="{apple_url}" class="source-link" target="_blank" rel="noopener">Apple Podcasts &#8594;</a>'
    if ep_url and ep_url != apple_url:
        buttons_html += f' <a href="{ep_url}" class="source-link" target="_blank" rel="noopener" style="opacity:0.7; font-size:0.78rem;">Show Notes &#8594;</a>'
    elif website_url:
        buttons_html += f' <a href="{website_url}" class="source-link" target="_blank" rel="noopener" style="opacity:0.7; font-size:0.78rem;">Website &#8594;</a>'

    return f"""        <div class="podcast-card">
          <div class="wn-tags" style="margin-bottom:8px;">
            {pills_html}
          </div>
          <div class="podcast-card-top">
            {img_html}
            <div class="podcast-card-info">
              <div class="podcast-name">{name}</div>
              {ep_html}            </div>
          </div>
          <div class="podcast-actions">{buttons_html}</div>
        </div>\n"""



def build_archive_tab(_unused=None):
    """
    Build the Archive tab HTML.
    Archive is now entirely client-side via localStorage starred items.
    The Python server-side archive.json is no longer used.
    """
    return """
      <div class="archive-intro">
        <h3 class="section-title" style="margin-top:0;">Your Starred Items</h3>
        <p style="font-size:0.9rem; color:var(--gray); margin-bottom:20px;">
          Star any card (&#9734;) on the What&apos;s New or Landmark Trial tabs to save it here.
          Starred items persist on this device across page reloads.
        </p>
      </div>
      <div id="archive-content">
        <p class="archive-empty-msg">No starred items yet. Use the &#9734; button on any card to save it here.</p>
      </div>
      <div class="archive-actions-bar" style="margin-top:16px; display:none" id="archive-actions">
        <button class="archive-action-btn" id="export-btn" onclick="exportFavorites()">
          <span class="archive-action-icon">&#x1F4CB;</span> Copy to Clipboard
        </button>
        <button class="archive-action-btn archive-action-btn-danger" onclick="clearAllStarred()">
          <span class="archive-action-icon">&#x2715;</span> Clear All
        </button>
      </div>"""


def build_jdd_card(jdd_content):
    """Build a .wn-card for the JDD Inclusive Derm Atlas image."""
    if not jdd_content:
        return ""

    title = jdd_content.get("title", "Dermatology Image")
    desc = jdd_content.get("description", "")
    source_url = jdd_content.get("source_url", "#")
    images = jdd_content.get("images", [])
    card_id = f"img-jdd-{title.lower().replace(' ', '-')[:30]}"

    html = f"""
      <div class="wn-card">
        <div class="wn-card-header">
          <div class="wn-tags">
            <span class="pill pill-image">Derm Image</span>
            <span class="pill pill-specialty">Dermatology</span>
          </div>
          <div class="card-actions">
            <button class="action-btn star" title="Star" data-id="{card_id}">&#9734;</button>
          </div>
        </div>
        <div class="wn-source">JDD Atlas &bull; {TODAY_STR}</div>
        <div class="wn-title">{title}</div>\n"""

    if desc:
        html += f'        <div class="wn-study-meta">{desc}</div>\n'

    for i, img_url in enumerate(images):
        html += (f'        <img src="{img_url}" alt="{title} — clinical image {i+1}" '
                 f'class="image-card-img" loading="lazy">\n')

    html += """        <div class="wn-bottom-line">
          <strong>Visual Diagnosis:</strong> Study the images above and consider the clinical description. What is your diagnosis?
        </div>\n"""

    html += f"""        <div class="wn-footer">
          <div class="confidence confidence-high">
            <span class="confidence-dot"></span>
            High-yield visual
          </div>
          <div class="wn-links">
            <a href="{source_url}" class="source-link" target="_blank" rel="noopener">View on JDD Atlas &#8594;</a>
          </div>
        </div>
      </div>\n"""

    return html


def build_litfl_card(category_key, case_content):
    """Build a .wn-card for a single LITFL case (ECG/CXR/CT/Clinical).

    Card hierarchy (user spec):
      1. Title  (e.g. "ECG of the Day")
      2. Source & date
      3. Image thumbnail(s)
      4. Clinical stem / vignette
      5. Key questions
      6. Challenge prompt + source link
    """
    cfg = LITFL_CATEGORIES[category_key]
    label = cfg["label"]
    pill_text = cfg["pill_text"]
    specialty = cfg["specialty"]
    questions = cfg["questions"]

    stem = case_content.get("clinical_stem", "")
    images = case_content.get("images", [])
    source_url = case_content.get("source_url", "#")

    # Build unique ID for starring
    if category_key == "clinical":
        slug = source_url.rstrip("/").split("/")[-1]
        card_id = f"img-clinical-{slug}"
    else:
        num_match = re.search(r'-(\d+)/?$', source_url.rstrip("/"))
        num_str = num_match.group(1) if num_match else "000"
        card_id = f"img-{category_key}-{num_str}"

    # ── Build card HTML using list + join (avoids escape issues) ──
    parts = []
    parts.append(f'''
      <div class="wn-card">
        <div class="wn-card-header">
          <div class="wn-tags">
            <span class="pill pill-image">{pill_text}</span>
            <span class="pill pill-specialty">{specialty}</span>
          </div>
          <div class="card-actions">
            <button class="action-btn star" title="Star" data-id="{card_id}">&#9734;</button>
          </div>
        </div>
        <div class="wn-title">{label}</div>
        <div class="wn-source">LITFL &bull; {TODAY_STR}</div>''')

    # ── Images FIRST (before stem) ──
    if images:
        for i, img_url in enumerate(images):
            parts.append(f'        <img src="{img_url}" alt="{label} image {i+1}" class="image-card-img" loading="lazy">')
    else:
        parts.append('        <div class="wn-study-meta" style="text-align:center;color:var(--text-muted);">Image not available</div>')

    # ── Clinical stem / vignette ──
    if stem:
        parts.append(f'        <div class="wn-study-meta">{stem}</div>')

    # ── Key Questions ──
    parts.append('        <div class="wn-section-label">Key Questions</div>')
    parts.append('        <ul class="wn-bullets">')
    for q in questions:
        parts.append(f'          <li>{q}</li>')
    parts.append('        </ul>')

    # ── Challenge bottom line ──
    parts.append('        <div class="wn-bottom-line">')
    parts.append('          <strong>Challenge:</strong> Can you identify the diagnosis? Click &ldquo;View on LITFL&rdquo; for the answer.')
    parts.append('        </div>')

    # ── Footer: confidence badge + source link ──
    parts.append(f'''        <div class="wn-footer">
          <div class="confidence confidence-moderate">
            <span class="confidence-dot"></span>
            Self-Assessment
          </div>
          <div class="wn-links">
            <a href="{source_url}" class="source-link" target="_blank" rel="noopener">View on LITFL &#8594;</a>
          </div>
        </div>
      </div>''')

    return chr(10).join(parts) + chr(10)

def build_image_tab(jdd_content, litfl_results=None):
    """
    Build the Image of the Day tab HTML.
    Includes: JDD Derm Atlas card + 4 LITFL case cards + NEJM reminder.
    """
    if litfl_results is None:
        litfl_results = {}

    html = '\n      <h3 class="section-title" style="margin-top:0;">Image of the Day</h3>\n'
    html += '      <p style="color:var(--text-muted,var(--gray)); font-size:0.85rem; margin-bottom:16px;">Visual cases from <a href="https://jddonline.com" target="_blank" rel="noopener">JDD Atlas</a> and <a href="https://litfl.com" target="_blank" rel="noopener">LITFL</a> &mdash; test your interpretation skills</p>\n'

    # ── JDD Derm card ──
    html += build_jdd_card(jdd_content)

    # ── LITFL case cards ──
    for cat_key in ["ecg", "cxr", "ct", "clinical"]:
        content = litfl_results.get(cat_key)
        if content:
            html += build_litfl_card(cat_key, content)

    # ── NEJM Image Challenge reminder card ──
    html += """
      <div class="nejm-reminder-card">
        <div class="nejm-reminder-header">
          <span class="nejm-reminder-icon">&#x1F9E0;</span>
          <h4 class="nejm-reminder-title">NEJM Image Challenge</h4>
        </div>
        <p class="nejm-reminder-text">
          Test your diagnostic skills with the weekly NEJM Image Challenge.
          A new clinical image with multiple-choice questions every week.
        </p>
        <a href="https://www.nejm.org/image-challenge" target="_blank" rel="noopener" class="btn btn-outline btn-sm">
          Take the Challenge &#8594;
        </a>
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

    /* ===== STARRED ITEMS (localStorage Archive) ===== */
    var PDR_STARRED_KEY = 'pdr-starred-v2';

    function getStarred() {
      try { return JSON.parse(localStorage.getItem(PDR_STARRED_KEY) || '{}'); }
      catch(e) { return {}; }
    }

    function saveStarred(starred) {
      try { localStorage.setItem(PDR_STARRED_KEY, JSON.stringify(starred)); } catch(e) {}
    }

    document.querySelectorAll('.action-btn.star').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.stopPropagation();
        var id = btn.getAttribute('data-id');
        var starred = getStarred();

        if (btn.classList.contains('active')) {
          btn.classList.remove('active');
          btn.innerHTML = '&#9734;';
          delete starred[id];
        } else {
          btn.classList.add('active');
          btn.innerHTML = '&#9733;';
          var card = btn.closest('.wn-card, .landmark-card');
          var title = id;
          var source = '';
          var typeLabel = '';
          var bottomLine = '';
          var sourceUrl = '';
          if (card) {
            var titleEl = card.querySelector('.wn-title, .landmark-title');
            if (titleEl) title = titleEl.textContent.trim();
            var sourceEl = card.querySelector('.wn-source, .landmark-meta');
            if (sourceEl) source = sourceEl.textContent.trim();
            var pills = card.querySelectorAll('.pill');
            if (pills.length > 0) typeLabel = pills[0].textContent.trim();
            var blEl = card.querySelector('.wn-bottom-line, .bottom-line');
            if (blEl) bottomLine = blEl.textContent.replace('Bottom Line:', '').replace('Bottom line:', '').trim();
            var linkEl = card.querySelector('.source-link, .statpearls-link');
            if (linkEl) sourceUrl = linkEl.href;
          }
          var landmarkSections = [];
          if (card && card.classList.contains('landmark-card')) {
            card.querySelectorAll('.landmark-section').forEach(function(sec) {
              var h = sec.querySelector('h4');
              var p = sec.querySelector('p');
              if (h && p) landmarkSections.push({ heading: h.textContent.trim(), text: p.textContent.trim() });
            });
          }
          starred[id] = {
            id: id, title: title, source: source,
            typeLabel: typeLabel, bottomLine: bottomLine,
            sourceUrl: sourceUrl,
            landmarkSections: landmarkSections.length > 0 ? landmarkSections : undefined,
            cardType: card ? (card.classList.contains('landmark-card') ? 'landmark' : 'whats_new') : 'unknown',
            starredAt: new Date().toISOString().split('T')[0]
          };
        }

        saveStarred(starred);
        renderArchive();
      });
    });

    /* ===== RENDER ARCHIVE TAB ===== */
    function renderArchive() {
      var container = document.getElementById('archive-content');
      var actionsBar = document.getElementById('archive-actions');
      if (!container) return;
      var starred = getStarred();
      var ids = Object.keys(starred);
      if (ids.length === 0) {
        container.innerHTML = '<p class="archive-empty-msg">No starred items yet. Use the &#9734; button on any card to save it here.</p>';
        if (actionsBar) actionsBar.style.display = 'none';
        return;
      }
      if (actionsBar) actionsBar.style.display = '';
      ids.sort(function(a, b) { return (starred[b].starredAt||'').localeCompare(starred[a].starredAt||''); });
      var html = '';
      ids.forEach(function(id) {
        var item = starred[id];
        var typeClass = item.cardType === 'landmark' ? 'pill-landmark' : 'pill-saved';
        html += '<div class="archive-card">';
        html += '<div class="archive-card-top">';
        html += '<div class="meta-pills" style="margin-bottom:0;">';
        html += '<span class="pill ' + typeClass + '">' + (item.cardType === 'landmark' ? 'Landmark' : 'Saved') + '</span>';
        if (item.typeLabel) html += '<span class="pill pill-specialty">' + item.typeLabel + '</span>';
        html += '</div>';
        html += '<button class="action-btn btn-remove-fav" data-id="' + id + '" onclick="removeStarred(this.dataset.id)" title="Remove">&#x2715;</button>';
        html += '</div>';
        html += '<div class="archive-card-title">' + (item.title || id) + '</div>';
        html += '<div class="archive-card-meta">' + (item.source || '') + ' &bull; Starred ' + (item.starredAt || '') + '</div>';
        if (item.bottomLine) {
          html += '<div class="wn-bottom-line"><strong>Bottom Line:</strong> ' + item.bottomLine + '</div>';
        }
        if (item.landmarkSections && item.landmarkSections.length > 0) {
          var showHeadings = ['Clinical Question', 'Key Findings', 'Practice Impact'];
          html += '<div style="margin-top:10px; font-size:0.85rem; line-height:1.6;">';
          item.landmarkSections.forEach(function(sec) {
            if (showHeadings.indexOf(sec.heading) !== -1) {
              html += '<p style="margin-bottom:6px;"><strong style="color:var(--english-red);">' + sec.heading + ':</strong> ' + sec.text + '</p>';
            }
          });
          html += '</div>';
        }
        if (item.sourceUrl) html += '<a href="' + item.sourceUrl + '" target="_blank" rel="noopener" class="source-link" style="display:block; text-align:right;">View Source &#8594;</a>';
        html += '</div>';
      });
      container.innerHTML = html;
    }

    function removeStarred(id) {
      var starred = getStarred();
      delete starred[id];
      saveStarred(starred);
      var btn = document.querySelector('.action-btn.star[data-id="' + id + '"]');
      if (btn) { btn.classList.remove('active'); btn.innerHTML = '&#9734;'; }
      renderArchive();
    }

    function clearAllStarred() {
      if (!confirm('Remove all starred items from archive?')) return;
      saveStarred({});
      document.querySelectorAll('.action-btn.star.active').forEach(function(b) {
        b.classList.remove('active'); b.innerHTML = '&#9734;';
      });
      renderArchive();
    }

    /* ===== EXPORT TO CLIPBOARD ===== */
    function exportFavorites() {
      var starred = getStarred();
      var ids = Object.keys(starred);
      if (ids.length === 0) { alert('No starred items yet.'); return; }
      var text = 'Paging Dr. Oh \\u2014 Starred Items\\n========================================\\n\\n';
      ids.forEach(function(id, i) {
        var item = starred[id];
        text += (i + 1) + '. ' + (item.title || id) + '\\n';
        if (item.source) text += '   ' + item.source + '\\n';
        if (item.bottomLine) text += '   Bottom line: ' + item.bottomLine + '\\n';
        text += '\\n';
      });
      navigator.clipboard.writeText(text).then(function() {
        var btn = document.getElementById('export-btn');
        if (btn) { var o = btn.textContent; btn.textContent = 'Copied!'; setTimeout(function(){ btn.textContent = o; }, 2000); }
      }).catch(function() {
        var ta = document.createElement('textarea'); ta.value = text;
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
      });
    }

    /* ===== RANDOMIZE DISEASE ===== */
    var _currentDiseaseIdx = 0;
    function randomizeDisease(poolSize) {
      document.getElementById('disease-view-' + _currentDiseaseIdx).style.display = 'none';
      var candidates = [];
      for (var i = 0; i < poolSize; i++) {
        if (i !== _currentDiseaseIdx) candidates.push(i);
      }
      _currentDiseaseIdx = candidates[Math.floor(Math.random() * candidates.length)];
      document.getElementById('disease-view-' + _currentDiseaseIdx).style.display = '';
      var diseaseSection = document.getElementById('disease') || document.getElementById('disease-tab');
      if (diseaseSection) diseaseSection.scrollIntoView({behavior: 'smooth'});
    }

    /* ===== INITIALIZE ON LOAD ===== */
    (function init() {
      var starred = getStarred();
      document.querySelectorAll('.action-btn.star').forEach(function(btn) {
        if (starred[btn.getAttribute('data-id')]) {
          btn.classList.add('active');
          btn.innerHTML = '&#9733;';
        }
      });
      renderArchive();
    })();
"""


# ═══════════════════════════════════════════════════════════════════════
# SECTION 8 — PAGE ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════

def build_full_page(css, disease_html, whatsnew_html, landmark_html, image_html, podcast_html, archive_html, calc_ids):
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
    <button class="tab-btn active" data-tab="whatsnew">What's New</button>
    <button class="tab-btn" data-tab="landmark">Landmark Trial</button>
    <button class="tab-btn" data-tab="disease">Disease of the Day</button>
    <button class="tab-btn" data-tab="imageofday">Image of the Day</button>
    <button class="tab-btn" data-tab="podcasts">Podcasts</button>
    <button class="tab-btn" data-tab="archive">Archive</button>
  </nav>

  <main>

    <!-- TAB 1: WHAT'S NEW -->
    <section id="whatsnew" class="tab-content active">
{whatsnew_html}
    </section>

    <!-- TAB 2: LANDMARK TRIAL -->
    <section id="landmark" class="tab-content">
{landmark_html}
    </section>

    <!-- TAB 3: DISEASE OF THE DAY -->
    <section id="disease" class="tab-content">
{disease_html}
    </section>

    <!-- TAB 4: IMAGE OF THE DAY -->
    <section id="imageofday" class="tab-content">
{image_html}
    </section>

    <!-- TAB 5: PODCASTS -->
    <section id="podcasts" class="tab-content">
{podcast_html}
    </section>

    <!-- TAB 6: ARCHIVE -->
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


def _ensure_trending_fields(items):
    """Backfill trending fields on items created before this update."""
    for item in items:
        item.setdefault("mention_count", 1)
        item.setdefault("sources_seen", [item.get("source", "Unknown")])
        item.setdefault("first_seen", item.get("date_iso", TODAY_ISO))
        item.setdefault("last_seen", item.get("date_iso", TODAY_ISO))
    return items


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


def update_history(history, disease_name, study_id, new_item_ids,
                   jdd_condition_name=None, litfl_shown_ids=None):
    """Update the history tracker."""
    history.setdefault("diseases_shown", []).append(disease_name)
    history.setdefault("landmark_studies_shown", []).append(study_id)
    history.setdefault("whats_new_ids", []).extend(new_item_ids)
    if jdd_condition_name:
        history.setdefault("jdd_conditions_shown", []).append(jdd_condition_name)
    if litfl_shown_ids:
        history.setdefault("litfl_cases_shown", []).extend(litfl_shown_ids)
    history["last_run"] = TODAY_ISO

    # Trim old history to keep file size reasonable
    history["diseases_shown"] = history["diseases_shown"][-90:]
    history["landmark_studies_shown"] = history["landmark_studies_shown"][-200:]
    history["whats_new_ids"] = history["whats_new_ids"][-500:]
    if "jdd_conditions_shown" in history:
        history["jdd_conditions_shown"] = history["jdd_conditions_shown"][-200:]
    if "litfl_cases_shown" in history:
        history["litfl_cases_shown"] = history["litfl_cases_shown"][-200:]


# ═══════════════════════════════════════════════════════════════════════
# SECTION 10 — MAIN
# ═══════════════════════════════════════════════════════════════════════

def api_health_check():
    """Quick API test to catch key/model/permissions issues early."""
    print("Running API health check...")
    try:
        response = CLIENT.messages.create(
            model=MODEL,
            max_tokens=50,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        text = response.content[0].text if response.content else ""
        print(f"  Basic API call: OK  (response: {text[:50]})")
    except Exception as e:
        print(f"  *** BASIC API CALL FAILED: {type(e).__name__}: {e}")
        print(f"  Check that ANTHROPIC_API_KEY is valid and has credits.")
        print(f"  Model: {MODEL}")
        sys.exit(1)

    # Test web search tool
    try:
        response = CLIENT.messages.create(
            model=MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": "What is 2+2?"}],
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
        )
        print(f"  Web search tool: OK")
    except Exception as e:
        print(f"  *** WEB SEARCH TOOL FAILED: {type(e).__name__}: {e}")
        print(f"  Web search may not be available on this API plan.")
        print(f"  Disease generation will fall back to non-search mode.")
        return False  # signal that web search is unavailable

    print("  Health check passed!\n")
    return True


def main():
    print(f"=== Paging Dr. Oh — Daily Generation ===")
    print(f"Date: {TODAY_STR}")
    print()

    # 0. API health check — fail fast with clear error if key/model is wrong
    web_search_available = api_health_check()

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

    # ── Phase 1: Kick off ALL independent HTTP fetches in parallel ──
    # These run in background threads while we do Claude API calls below.
    # Saves 15-30s vs doing them sequentially after the API work.
    print("\n--- Parallel HTTP fetches starting (JDD, LITFL, podcasts, WikiJournalClub) ---")
    http_pool = ThreadPoolExecutor(max_workers=5)

    def _fetch_jdd_safe():
        """Fetch JDD image content (HTTP only). Returns (jdd_content, condition_name)."""
        try:
            conditions = fetch_jdd_index()
            if conditions:
                pick = pick_jdd_condition(conditions, history)
                if pick:
                    content = fetch_jdd_condition_page(pick["url"])
                    return content, pick["name"]
            return None, None
        except Exception as e:
            print(f"  WARNING: JDD fetch failed ({type(e).__name__}: {e})")
            return None, None

    def _fetch_litfl_safe():
        """Fetch all 4 LITFL cases in parallel. Returns (results_dict, shown_ids)."""
        try:
            clinical_cases = fetch_litfl_clinical_index()
            picks = pick_litfl_cases(history, clinical_cases)

            results = {}
            shown_ids = []
            with ThreadPoolExecutor(max_workers=4) as inner_pool:
                futures = {}
                for cat_key, pick in picks.items():
                    if pick is None:
                        continue
                    url = pick.get("url")
                    if url:
                        futures[cat_key] = inner_pool.submit(
                            fetch_litfl_case_page, url, cat_key)

                for cat_key, future in futures.items():
                    try:
                        content = future.result(timeout=20)
                        if content:
                            results[cat_key] = content
                            pick = picks[cat_key]
                            if cat_key == "clinical":
                                shown_ids.append(f"clinical-{pick['slug']}")
                            else:
                                shown_ids.append(f"{cat_key}-{pick['num']:03d}")
                    except Exception as e:
                        print(f"  WARNING: LITFL {cat_key} fetch failed: {e}")

            return results, shown_ids
        except Exception as e:
            print(f"  WARNING: LITFL fetch failed ({type(e).__name__}: {e})")
            return {}, []

    def _fetch_podcasts_safe():
        """Fetch podcast feeds (HTTP only). Returns list."""
        try:
            return fetch_all_podcasts()
        except Exception as e:
            print(f"  WARNING: Podcast fetch failed ({type(e).__name__}: {e})")
            return []

    jdd_future = http_pool.submit(_fetch_jdd_safe)
    litfl_future = http_pool.submit(_fetch_litfl_safe)
    podcast_future = http_pool.submit(_fetch_podcasts_safe)
    wjc_future = http_pool.submit(fetch_wikijournalclub_page, study)

    # ── Phase 2: What's New (RSS HTTP + Claude curate + PubMed HTTP + Claude enrich) ──
    print("\nFetching What's New from RSS feeds...")
    new_items = generate_whats_new(history)

    # Merge manual additions
    if isinstance(manual_additions, list) and manual_additions:
        for item in manual_additions:
            item.setdefault("date_iso", TODAY_ISO)
        new_items = manual_additions + new_items
        print(f"  Added {len(manual_additions)} manual addition(s).")

    print(f"  Found {len(new_items)} new item(s).")

    # ── Phase 3: Disease of the Day (single Claude API call) ──
    print(f"\nGenerating disease content: {disease_name}...")
    disease_content = generate_disease_content(disease_name, use_web_search=web_search_available)
    disease_pool = [disease_content]
    disease_pool_names = [disease_name]
    print(f"  Done: {disease_name}")

    # ── Phase 4: Collect HTTP results + generate landmark ──
    # WikiJournalClub fetch should be done by now (started in Phase 1)
    print("\nCollecting parallel HTTP results...")
    wjc_text, wjc_url = wjc_future.result()
    if wjc_text:
        print(f"  WikiJournalClub source ready: {wjc_url}")
    else:
        print("  WikiJournalClub not found — generating from training data")

    print(f"Pausing 10s before landmark generation...")
    time.sleep(10)

    print("Generating Landmark Study content...")
    landmark_content = generate_landmark_content(study, source_text=wjc_text, source_url=wjc_url)

    # ── Phase 5: Collect remaining HTTP results ──
    jdd_content, jdd_condition_name = jdd_future.result()
    if jdd_condition_name:
        print(f"  JDD Image: {jdd_condition_name}")
    else:
        print("  WARNING: No JDD image available")

    litfl_results, litfl_shown_ids = litfl_future.result()
    if litfl_results:
        print(f"  LITFL Cases: {len(litfl_results)} loaded ({', '.join(litfl_results.keys())})")
    else:
        print("  WARNING: No LITFL cases available")

    podcasts = podcast_future.result()
    print(f"  Podcasts fetched: {len(podcasts)} feed(s)")

    http_pool.shutdown(wait=False)
    print("--- All parallel work complete ---\n")

    # 7. Add today's new items to the front of the current list
    wn_current = new_items + wn_current

    # 7b. Remove duplicates (by id, URL, or title)
    wn_current = remove_wn_duplicates(wn_current)

    # 9. Read CSS from styles.css
    css = get_css()

    # Determine which calculators are needed (from primary disease)
    primary_disease_content = disease_pool[0] if disease_pool else {}
    calc_ids = primary_disease_content.get("calculators", [])
    if not calc_ids:
        calc_ids = DISEASE_CALCULATORS.get(disease_name, [])

    # Build all tab HTML
    disease_html  = build_disease_tab(disease_pool)
    whatsnew_html = build_whatsnew_tab(wn_current)
    landmark_html = build_landmark_tab(landmark_content)
    image_html    = build_image_tab(jdd_content, litfl_results)
    podcast_html  = build_podcast_tab(podcasts)
    archive_html  = build_archive_tab()

    # 10. Assemble full page
    page = build_full_page(css, disease_html, whatsnew_html, landmark_html, image_html, podcast_html, archive_html, calc_ids)

    # 11. Write index.html
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"\nWrote {OUTPUT_PATH} ({len(page):,} bytes)")

    # 12. Update state files
    new_item_ids = [item.get("id", "") for item in new_items if item.get("id")]
    # Record disease in history to avoid repeating it soon
    update_history(history, disease_name, study.get("id", ""), new_item_ids,
                   jdd_condition_name=jdd_condition_name,
                   litfl_shown_ids=litfl_shown_ids)
    # Deduplicate history IDs (update_history extends the list)
    history["whats_new_ids"] = list(dict.fromkeys(history["whats_new_ids"]))
    save_json(HISTORY_PATH, history)
    save_json(WN_PATH, wn_current)
    save_json(ARCHIVE_PATH, archive)
    save_json(MANUAL_PATH, [])  # Clear manual additions after processing

    print("State files updated.")
    print("=== Done! ===")


if __name__ == "__main__":
    main()
