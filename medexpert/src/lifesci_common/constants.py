"""Shared constants for MedExpert Life Sciences Research Bot.

Contains API endpoints, GRADE methodology definitions, domain routing
maps, and MCP server port assignments.
"""

# ---------------------------------------------------------------------------
# API Endpoint URLs
# ---------------------------------------------------------------------------

# PubMed / NCBI E-utilities
PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PUBMED_ESUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

# ClinicalTrials.gov v2
CLINICALTRIALS_API_URL = "https://clinicaltrials.gov/api/v2"
CLINICALTRIALS_STUDIES_URL = f"{CLINICALTRIALS_API_URL}/studies"

# OpenFDA
OPENFDA_BASE_URL = "https://api.fda.gov"
OPENFDA_DRUG_EVENT_URL = f"{OPENFDA_BASE_URL}/drug/event.json"
OPENFDA_DRUG_LABEL_URL = f"{OPENFDA_BASE_URL}/drug/label.json"
OPENFDA_DRUG_RECALL_URL = f"{OPENFDA_BASE_URL}/drug/enforcement.json"
OPENFDA_DEVICE_EVENT_URL = f"{OPENFDA_BASE_URL}/device/event.json"
OPENFDA_DEVICE_510K_URL = f"{OPENFDA_BASE_URL}/device/510k.json"
OPENFDA_DEVICE_PMA_URL = f"{OPENFDA_BASE_URL}/device/pma.json"
OPENFDA_DEVICE_CLASS_URL = f"{OPENFDA_BASE_URL}/device/classification.json"

# CDC SODA Open Data
CDC_WONDER_BASE_URL = "https://data.cdc.gov/resource"

# SEER / NCI
SEER_API_URL = "https://data.cdc.gov/resource"  # Uses CDC SODA for public stats

# NCBI Genomic (ClinVar, dbSNP)
CLINVAR_API_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DBSNP_API_URL = "https://api.ncbi.nlm.nih.gov/variation/v0"

# EPA Environmental
EPA_AQS_URL = "https://aqs.epa.gov/data/api"
EPA_EJSCREEN_URL = "https://ejscreen.epa.gov/mapper/ejscreenRESTbroker.aspx"

# US Census ACS (Social Determinants of Health)
CENSUS_ACS_URL = "https://api.census.gov/data"

# CMS Open Payments & Provider
CMS_OPEN_PAYMENTS_URL = "https://openpaymentsdata.cms.gov/api/1/datastore/query"
CMS_PROVIDER_URL = "https://data.cms.gov/provider-data/api/1/datastore/query"

# CrossRef (DOI resolution)
CROSSREF_WORKS_URL = "https://api.crossref.org/works"

# Regulatory guidance (FDA)
FDA_GUIDANCE_URL = f"{OPENFDA_BASE_URL}/device/registrationlisting.json"

# ---------------------------------------------------------------------------
# GRADE Methodology
# Scores based on Oxford Centre for Evidence-Based Medicine Levels of Evidence.
# Boundary rules: score >= 3.0 → High, [2.0, 3.0) → Moderate,
#                 [1.0, 2.0) → Low, < 1.0 → Very Low
# ---------------------------------------------------------------------------

GRADE_STUDY_TYPE_SCORES: dict[str, float] = {
    "meta_analysis": 4.0,
    "systematic_review": 4.0,
    "rct": 3.0,
    "cohort": 2.0,
    "case_control": 1.0,
    "cross_sectional": 1.0,
    "case_report": 0.5,
    "case_series": 0.5,
    "expert_opinion": 0.25,
    "narrative_review": 0.25,
}

GRADE_DOWNGRADE_FACTORS = {
    "small_sample": -1.0,
    "no_blinding": -1.0,
    "high_dropout": -1.0,
    "inconsistency": -0.5,
    "indirectness": -0.5,
    "imprecision": -0.5,
    "publication_bias": -0.5,
}

GRADE_UPGRADE_FACTORS = {
    "large_effect": 1.0,
    "dose_response": 0.5,
    "confounders_reduce_effect": 0.5,
}

GRADE_LEVELS = {
    "High": (3.0, float("inf")),
    "Moderate": (2.0, 3.0),
    "Low": (1.0, 2.0),
    "Very Low": (float("-inf"), 1.0),
}

# ---------------------------------------------------------------------------
# Domain-to-Agent Routing Map
# ---------------------------------------------------------------------------

DOMAIN_AGENT_ROUTING = {
    "literature": {
        "agent": "LiteratureSpecialist",
        "keywords": [
            "pubmed",
            "study",
            "research",
            "journal",
            "paper",
            "meta-analysis",
            "systematic review",
            "clinical trial results",
            "evidence",
            "literature",
            "peer-reviewed",
            "publication",
            "article",
            "abstract",
        ],
    },
    "clinical_trials": {
        "agent": "ClinicalTrialsSpecialist",
        "keywords": [
            "clinical trial",
            "trial",
            "enrollment",
            "recruiting",
            "phase",
            "randomized",
            "placebo",
            "arm",
            "endpoint",
            "clinicaltrials.gov",
            "nct",
            "intervention",
            "investigational",
        ],
    },
    "drugs": {
        "agent": "DrugSpecialist",
        "keywords": [
            "drug",
            "medication",
            "pharmaceutical",
            "adverse event",
            "side effect",
            "fda approval",
            "label",
            "recall",
            "interaction",
            "contraindication",
            "dosage",
            "pharmacology",
            "pharmacokinetics",
        ],
    },
    "regulatory": {
        "agent": "RegulatorySpecialist",
        "keywords": [
            "510k",
            "pma",
            "fda",
            "regulatory",
            "clearance",
            "approval",
            "device",
            "classification",
            "guidance",
            "compliance",
            "submission",
        ],
    },
    "epidemiology": {
        "agent": "EpidemiologySpecialist",
        "keywords": [
            "prevalence",
            "incidence",
            "mortality",
            "morbidity",
            "outbreak",
            "epidemic",
            "pandemic",
            "surveillance",
            "cdc",
            "statistics",
            "demographic",
            "population",
            "vaccination",
            "immunization",
        ],
    },
    "genomics": {
        "agent": "GenomicsSpecialist",
        "keywords": [
            "gene",
            "genetic",
            "genomic",
            "variant",
            "mutation",
            "snp",
            "clinvar",
            "pathogenic",
            "dna",
            "rna",
            "chromosome",
            "allele",
            "genotype",
            "phenotype",
            "hereditary",
            "sequencing",
        ],
    },
    "environmental": {
        "agent": "EnvironmentalSpecialist",
        "keywords": [
            "environmental",
            "pollution",
            "air quality",
            "water quality",
            "toxin",
            "exposure",
            "contaminant",
            "epa",
            "environmental justice",
            "hazardous",
            "carcinogen",
        ],
    },
    "provider_intel": {
        "agent": "ProviderIntelSpecialist",
        "keywords": [
            "provider",
            "physician",
            "doctor",
            "hospital",
            "payment",
            "open payments",
            "quality",
            "rating",
            "cms",
            "medicare",
            "specialist",
            "practice",
        ],
    },
}

# ---------------------------------------------------------------------------
# MCP Server Port Assignments
# ---------------------------------------------------------------------------

MCP_PORTS = {
    "pubmed": 9001,
    "clinicaltrials": 9002,
    "openfda": 9003,
    "cdc": 9004,
    "regulatory": 9005,
    "seer": 9006,
    "genomic": 9007,
    "environmental": 9008,
    "census_sdoh": 9009,
    "provider_intel": 9010,
    "knowledge_graph": 9011,
    "societies": 9012,
    "vigibase": 9013,
    "imaging": 9014,
    "pharmacovigilance": 9015,
}

# ---------------------------------------------------------------------------
# Memory Plane Namespaces
# ---------------------------------------------------------------------------

MEMORY_NAMESPACES = [
    "citations",
    "evidence",
    "intermediate",
    "learning",
    "verification",
]

# ---------------------------------------------------------------------------
# Report Modes
# ---------------------------------------------------------------------------

REPORT_MODES = [
    "quick_answer",
    "research_brief",
    "full_synthesis",
    "advisory_board_report",
]

# ---------------------------------------------------------------------------
# Prompt Evolution
# ---------------------------------------------------------------------------

EVOLVABLE_AGENTS = [
    "LiteratureSpecialist",
    "ClinicalTrialsSpecialist",
    "DrugSpecialist",
    "RegulatorySpecialist",
    "EpidemiologySpecialist",
    "GenomicsSpecialist",
    "EnvironmentalSpecialist",
    "ProviderIntelSpecialist",
]

# Frozen agents — prompts must NEVER be modified by the evolution engine.
FROZEN_AGENTS = [
    "OrchestratorAgent",
    "VerifierAgent",
    "ReviserAgent",
]

# Grader pass thresholds (per-grader)
GRADER_THRESHOLDS = {
    "entity": 0.8,
    "fidelity": 0.3,
    "citation": 0.7,
    "quality": 0.6,
    "llm_judge": 0.75,
}

# Composite weights for each grader
GRADER_WEIGHTS = {
    "entity": 0.20,
    "fidelity": 0.20,
    "citation": 0.25,
    "quality": 0.15,
    "llm_judge": 0.20,
}

# Composite score threshold below which a specialist is considered underperforming
EVOLUTION_COMPOSITE_THRESHOLD = 0.55

# Number of recent sessions to consider for rolling average
EVOLUTION_ROLLING_WINDOW = 5

# Maximum prompt versions to keep per agent (oldest non-baseline pruned)
MAX_PROMPT_VERSIONS = 50

# Number of consecutive poor sessions that trigger auto-rollback
ROLLBACK_SESSION_COUNT = 3

# ---------------------------------------------------------------------------
# Specialist Calibration (DEFRA WeightCalibrator adaptation)
# ---------------------------------------------------------------------------

CALIBRATION_MIN_SAMPLES = 10
CALIBRATION_NEGATIVE_THRESHOLD = 0.30
CALIBRATION_POSITIVE_THRESHOLD = 0.05
CALIBRATION_ADJUSTMENT_STEP = 0.05
CALIBRATION_MIN_WEIGHT = 0.3
CALIBRATION_MAX_WEIGHT = 1.0
CALIBRATION_WINDOW_DAYS = 90
CALIBRATION_FEEDBACK_WEIGHT = 0.6
CALIBRATION_VERIFICATION_WEIGHT = 0.4

# Medical entity regex patterns for entity preservation grader
MEDICAL_ENTITY_PATTERNS = {
    "pmid": r"\b\d{7,8}\b",
    "nct_id": r"\bNCT\d{8,11}\b",
    "doi": r"\b10\.\d{4,}/\S+",
    "gene_symbol": r"\b[A-Z][A-Z0-9]{1,5}\b",
    "dosage": r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|mL|IU|units?)\b",
}

# LLM-as-judge configuration
LLM_JUDGE_MODEL = "openai/gemini-2.5-flash-001"
LLM_JUDGE_TEMPERATURE = 0.1
LLM_JUDGE_TIMEOUT_SECONDS = 15
LLM_JUDGE_BATCH_SIZE = 10
LLM_JUDGE_QUEUE_TRIGGER = 20


# ---------------------------------------------------------------------------
# Medical Triage Pipeline
# ---------------------------------------------------------------------------

TRIAGE_EMERGENCY_KEYWORDS = [
    "chest pain",
    "can't breathe",
    "difficulty breathing",
    "shortness of breath",
    "stroke",
    "sudden numbness",
    "face drooping",
    "arm weakness",
    "speech difficulty",
    "severe bleeding",
    "unconscious",
    "loss of consciousness",
    "seizure",
    "anaphylaxis",
    "severe allergic reaction",
    "throat swelling",
    "suicidal",
    "self harm",
    "self-harm",
    "overdose",
    "poisoning",
    "choking",
]

TRIAGE_TIER1_SPECIALISTS = [
    "family_physician",
    "internist",
    "emergency_medicine",
]

TRIAGE_MAX_TIER2_SPECIALISTS = 3

TRIAGE_NBA_ROUTES = {
    "emergency_services": {"urgency": "immediate", "color": "red"},
    "hospital": {"urgency": "urgent", "color": "orange"},
    "specialist_referral": {"urgency": "soon", "color": "yellow"},
    "gp_visit": {"urgency": "routine", "color": "blue"},
    "pharmacist": {"urgency": "low", "color": "green"},
    "self_care": {"urgency": "minimal", "color": "gray"},
}

TRIAGE_SPECIALIST_TIMEOUT_S = 30
TRIAGE_PIPELINE_TIMEOUT_S = 120
TRIAGE_CONSENSUS_JACCARD_THRESHOLD = 0.6
TRIAGE_MAX_INTAKE_QUESTIONS = 10

# ---------------------------------------------------------------------------
# Runtime Validation
# ---------------------------------------------------------------------------


def _validate_routing():
    """Validate DOMAIN_AGENT_ROUTING structure at import time."""
    for domain, info in DOMAIN_AGENT_ROUTING.items():
        if "agent" not in info:
            raise ValueError(f"DOMAIN_AGENT_ROUTING['{domain}'] missing 'agent' key")
        if "keywords" not in info or not info["keywords"]:
            raise ValueError(
                f"DOMAIN_AGENT_ROUTING['{domain}'] missing or empty 'keywords'"
            )


_validate_routing()
