"""Environment configuration and runtime defaults."""

import os
from pathlib import Path

HOST = os.getenv("SABOR_HOST", "0.0.0.0")
PORT = int(os.getenv("SABOR_PORT", "8080"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Shared Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MEALIE_BASE_URL = os.getenv("MEALIE_BASE_URL", "http://localhost:9925").rstrip("/")
MEALIE_API_TOKEN = os.getenv("MEALIE_API_TOKEN", "")

SPOONACULAR_API_KEY = os.getenv("SPOONACULAR_API_KEY", "")
SPOONACULAR_BASE = "https://api.spoonacular.com"
SPOONACULAR_TIMEOUT = 20

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("VIDEO_RECIPE_GEMINI_MODEL", "gemini-2.5-flash-lite")
_GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]
_GEMINI_BASE = "https://generativelanguage.googleapis.com"

BRING_EMAIL = os.getenv("BRING_EMAIL", "")
BRING_PASSWORD = os.getenv("BRING_PASSWORD", "")
# Public Bring web-client identifier, also published by miaucl/bring-api.
BRING_PUBLIC_CLIENT_ID = "cof4Nc6D8saplXjE3h3HXqHH8m7VU2i1Gs0g85Sp"  # gitleaks:allow

MEMORY_FILE = Path(os.getenv("MEMORY_FILE", "/data/MEMORY.md"))

SABOR_NORMALIZE_IMPORTS = os.getenv("SABOR_NORMALIZE_IMPORTS", "true").strip().casefold() in {
    "1", "true", "yes", "on"
}
SABOR_INGREDIENT_PARSER = os.getenv("SABOR_INGREDIENT_PARSER", "openai").strip() or "openai"
SABOR_NORMALIZE_CONFIDENCE = float(os.getenv("SABOR_NORMALIZE_CONFIDENCE", "0.75"))
CATALOG_DIR = Path(__file__).resolve().parent.parent / "catalog"

SABOR_NUTRITION_ENABLED = os.getenv("SABOR_NUTRITION_ENABLED", "false").strip().casefold() in {
    "1", "true", "yes", "on"
}
SABOR_NUTRITION_DB = Path(os.getenv("SABOR_NUTRITION_DB", "/data/nutrition.db"))
SABOR_NUTRITION_PROVIDER = os.getenv("SABOR_NUTRITION_PROVIDER", "spoonacular")
SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE = float(os.getenv("SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE", "0.80"))
_NUTRITION_RECIPE_ISSUE_LIMIT = 50
_NUTRITION_COOKBOOK_CARD_ISSUE_LIMIT = 10
_NUTRITION_OUTPUT_FIELD_CAPS = {
    "id": 128, "slug": 200, "name": 300, "ingredientId": 128,
    "originalText": 300, "reason": 128, "message": 300, "action": 300,
    "suggestion": 300, "cookbook": 300,
}
SABOR_NUTRITION_MIN_PUBLISH_COVERAGE = float(os.getenv("SABOR_NUTRITION_MIN_PUBLISH_COVERAGE", "0.90"))
SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE = float(os.getenv("SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE", "0.80"))
SABOR_NUTRITION_PUBLISH_ESTIMATES = os.getenv("SABOR_NUTRITION_PUBLISH_ESTIMATES", "false").strip().casefold() in {
    "1", "true", "yes", "on"
}
SABOR_NUTRITION_PROFILE_TTL_DAYS = int(os.getenv("SABOR_NUTRITION_PROFILE_TTL_DAYS", "180"))
SABOR_NUTRITION_NOT_FOUND_TTL_DAYS = int(os.getenv("SABOR_NUTRITION_NOT_FOUND_TTL_DAYS", "30"))
SABOR_NUTRITION_SOURCE_LANGUAGE = os.getenv("SABOR_NUTRITION_SOURCE_LANGUAGE", "es").strip().casefold()
SABOR_NUTRITION_DEFAULT_CUP_ML = float(os.getenv("SABOR_NUTRITION_DEFAULT_CUP_ML", "240"))
SABOR_NUTRITION_AUTO_ON_IMPORT = os.getenv("SABOR_NUTRITION_AUTO_ON_IMPORT", "false").strip().casefold() in {
    "1", "true", "yes", "on"
}
SABOR_NUTRITION_AUTO_PUBLISH = os.getenv("SABOR_NUTRITION_AUTO_PUBLISH", "false").strip().casefold() in {
    "1", "true", "yes", "on"
}

VIDEO_WORKDIR = Path(os.getenv("VIDEO_RECIPE_WORKDIR", "/tmp/video-recipe-mcp"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
