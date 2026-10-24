"""Shared compatibility parsing without importing the compatibility module."""
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
from .paths import catalog_dir, data_dir


@dataclass(frozen=True)
class LegacyConfig:
    """Read-only attribute namespace; mutable constants are returned as copies."""
    values: Mapping[str, Any] = field(repr=False)

    def __getattr__(self, name):
        try:
            return deepcopy(self.values[name])
        except KeyError:
            raise AttributeError(name) from None


def load_legacy_config(env: Mapping[str, str]) -> LegacyConfig:
    SABOR_DATA_DIR = data_dir(env)
    HOST = env.get("SABOR_HOST", "0.0.0.0")
    PORT = int(env.get("SABOR_PORT", "8080"))

    MEALIE_BASE_URL = env.get("MEALIE_BASE_URL", "http://localhost:9925").rstrip("/")
    MEALIE_API_TOKEN = env.get("MEALIE_API_TOKEN", "")

    SABOR_STORE = env.get("SABOR_STORE", "").strip() or (
        "mealie" if env.get("MEALIE_BASE_URL", "").strip() else "sqlite")
    SABOR_STORE_DB = env.get("SABOR_STORE_DB", "").strip() or str(SABOR_DATA_DIR / "sabor.db")

    SPOONACULAR_API_KEY = env.get("SPOONACULAR_API_KEY", "")
    SPOONACULAR_BASE = "https://api.spoonacular.com"
    SPOONACULAR_TIMEOUT = 20

    GEMINI_API_KEY = env.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = env.get("VIDEO_RECIPE_GEMINI_MODEL", "gemini-2.5-flash-lite")
    _GEMINI_FALLBACK_MODELS = [
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ]
    _GEMINI_BASE = "https://generativelanguage.googleapis.com"

    BRING_EMAIL = env.get("BRING_EMAIL", "")
    BRING_PASSWORD = env.get("BRING_PASSWORD", "")
    BRING_PUBLIC_CLIENT_ID = "cof4Nc6D8saplXjE3h3HXqHH8m7VU2i1Gs0g85Sp"  # gitleaks:allow

    MEMORY_FILE = Path(env.get("MEMORY_FILE") or SABOR_DATA_DIR / "MEMORY.md")

    SABOR_NORMALIZE_IMPORTS = env.get("SABOR_NORMALIZE_IMPORTS", "true").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    SABOR_INGREDIENT_PARSER = env.get("SABOR_INGREDIENT_PARSER", "openai").strip() or "openai"
    SABOR_NORMALIZE_CONFIDENCE = float(env.get("SABOR_NORMALIZE_CONFIDENCE", "0.75"))
    CATALOG_DIR = catalog_dir()

    SABOR_NUTRITION_ENABLED = env.get("SABOR_NUTRITION_ENABLED", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    SABOR_NUTRITION_DB = Path(env.get("SABOR_NUTRITION_DB") or SABOR_DATA_DIR / "nutrition.db")
    SABOR_NUTRITION_PROVIDER = env.get("SABOR_NUTRITION_PROVIDER", "spoonacular")
    SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE = float(env.get("SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE", "0.80"))
    _NUTRITION_RECIPE_ISSUE_LIMIT = 50
    _NUTRITION_COOKBOOK_CARD_ISSUE_LIMIT = 10
    _NUTRITION_OUTPUT_FIELD_CAPS = {
        "id": 128, "slug": 200, "name": 300, "ingredientId": 128,
        "originalText": 300, "reason": 128, "message": 300, "action": 300,
        "suggestion": 300, "cookbook": 300,
    }
    SABOR_NUTRITION_MIN_PUBLISH_COVERAGE = float(env.get("SABOR_NUTRITION_MIN_PUBLISH_COVERAGE", "0.90"))
    SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE = float(env.get("SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE", "0.80"))
    SABOR_NUTRITION_PUBLISH_ESTIMATES = env.get("SABOR_NUTRITION_PUBLISH_ESTIMATES", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    SABOR_NUTRITION_PROFILE_TTL_DAYS = int(env.get("SABOR_NUTRITION_PROFILE_TTL_DAYS", "180"))
    SABOR_NUTRITION_NOT_FOUND_TTL_DAYS = int(env.get("SABOR_NUTRITION_NOT_FOUND_TTL_DAYS", "30"))
    SABOR_NUTRITION_SOURCE_LANGUAGE = env.get("SABOR_NUTRITION_SOURCE_LANGUAGE", "es").strip().casefold()
    SABOR_NUTRITION_DEFAULT_CUP_ML = float(env.get("SABOR_NUTRITION_DEFAULT_CUP_ML", "240"))
    SABOR_NUTRITION_AUTO_ON_IMPORT = env.get("SABOR_NUTRITION_AUTO_ON_IMPORT", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }
    SABOR_NUTRITION_AUTO_PUBLISH = env.get("SABOR_NUTRITION_AUTO_PUBLISH", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }

    VIDEO_WORKDIR = Path(env.get("VIDEO_RECIPE_WORKDIR", "/tmp/video-recipe-mcp"))

    BRING_CATALOG_DIR = env.get("BRING_CATALOG_DIR", "")
    return LegacyConfig(MappingProxyType({k: v for k, v in locals().items()
                                         if k.isupper() or k.startswith("_GEMINI")}))
