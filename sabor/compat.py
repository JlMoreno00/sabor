"""Legacy module attribute forwarding for existing Python callers and patches.

Tool implementations use their owning modules. Forwarding reads and writes keeps
sabor_mcp helper/configuration patches effective without shared execution globals.
"""

import importlib
import sys
from types import ModuleType

import copy
import json
import hashlib
import math
import os
import re
import sysconfig
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID
import requests
from mcp.server.fastmcp import FastMCP

# Names that were defined by the original entrypoint, grouped by their new owner.
_EXPORTS = {
    "sabor.config": (
        "HOST", "PORT", "MEALIE_BASE_URL", "MEALIE_API_TOKEN", "SPOONACULAR_API_KEY",
        "SPOONACULAR_BASE", "SPOONACULAR_TIMEOUT", "GEMINI_API_KEY", "GEMINI_MODEL",
        "_GEMINI_FALLBACK_MODELS", "_GEMINI_BASE", "BRING_EMAIL", "BRING_PASSWORD",
        "BRING_PUBLIC_CLIENT_ID", "MEMORY_FILE", "SABOR_NORMALIZE_IMPORTS",
        "SABOR_INGREDIENT_PARSER", "SABOR_NORMALIZE_CONFIDENCE", "CATALOG_DIR",
        "SABOR_NUTRITION_ENABLED", "SABOR_NUTRITION_DB", "SABOR_NUTRITION_PROVIDER",
        "SABOR_NUTRITION_MIN_PROFILE_CONFIDENCE", "_NUTRITION_RECIPE_ISSUE_LIMIT",
        "_NUTRITION_COOKBOOK_CARD_ISSUE_LIMIT", "_NUTRITION_OUTPUT_FIELD_CAPS",
        "SABOR_NUTRITION_MIN_PUBLISH_COVERAGE", "SABOR_NUTRITION_MIN_PUBLISH_CONFIDENCE",
        "SABOR_NUTRITION_PUBLISH_ESTIMATES", "SABOR_NUTRITION_PROFILE_TTL_DAYS",
        "SABOR_NUTRITION_NOT_FOUND_TTL_DAYS", "SABOR_NUTRITION_SOURCE_LANGUAGE",
        "SABOR_NUTRITION_DEFAULT_CUP_ML", "SABOR_NUTRITION_AUTO_ON_IMPORT",
        "SABOR_NUTRITION_AUTO_PUBLISH", "VIDEO_WORKDIR",
    ),
    "sabor.server": (
        "mcp",
    ),
    "sabor.integrations.mealie": (
        "_mealie_headers", "_check_mealie", "_catalog_key", "_load_catalog_manifest",
        "_get_all_mealie_items", "_reconcile_catalog_kind", "_reconcile_spanish_catalog",
        "spanish_catalog_status", "apply_spanish_catalog", "_response_slug",
        "_ingredient_source_text", "_sanitize_ingredient_references", "_normalization_result",
        "_normalize_imported_recipe",
    ),
    "sabor.tools.recipes": (
        "search_recipes", "get_recipe", "create_recipe",
    ),
    "sabor.tools.shopping": (
        "get_shopping_lists", "create_shopping_list", "delete_shopping_list",
         "clear_shopping_list", 
        "get_or_create_shopping_list",
    ),
    "sabor.tools.mealplan": (
        "get_mealplan_week", "set_mealplan_entry", "delete_mealplan_entry",
        "clear_mealplan_week",
    ),
    "sabor.tools.tags": (
        "get_or_create_tag", "tag_recipe",
    ),
    "sabor.tools.comments": (
        "add_recipe_comment", "log_cooking_event",
    ),
    "sabor.integrations.spoonacular": (
        "_spoonacular_get", "spoonacular_search_recipes", 
        "spoonacular_nutrition_100g",
    ),
    "sabor.tools.memory": (
        "_read_memory", "_write_memory", "_ensure_section", "_get_section_content",
        "_replace_section", "_rating_to_stars", "_MEMORY_TEMPLATE", "save_feedback",
        "read_memory", "update_pantry", "update_learned_preference",
    ),
    "sabor.sync.bring_client": (
        "_QUANTITY_NOUNS", "_PREP_WORDS", "_MANUAL_ALIASES", "_load_bring_catalog", "_CAT_ES",
        "_CAT_EN", "_remove_accents", "_catalog_match", "_bring_auth", "_bring_login_headers",
        "_bring_authed_headers", "_bring_login", "_bring_ensure_auth", "_bring_req",
         "_clean_food_name", 
          
    ),
    "sabor.integrations.mercadona": (
        "MERCADONA_BASE_URL", "MERCADONA_WAREHOUSE", "MERCADONA_TIMEOUT",
        "_MERCADONA_CATALOG_TTL", "_MERCADONA_FETCH_WORKERS", "_MERCADONA_FETCH_DELAY",
        "_mercadona_catalog", "_mercadona_catalog_loaded_at", "_mercadona_catalog_loading",
        "_mercadona_normalize", "_mercadona_relevance_score", "_mercadona_get",
        "_mercadona_extract_product", "_mercadona_fetch_subcategory",
        "_mercadona_load_catalog", "_mercadona_ensure_catalog", "mercadona_search_products",
        "mercadona_get_product_price", "mercadona_estimate_recipe_cost",
        "mercadona_estimate_shopping_list_cost", "mercadona_get_price_drops",
        "mercadona_get_new_arrivals",
    ),
    "sabor.tools.nutrition": (
        "_NUTRITION_SERVICE_INSTANCE", "_NUTRITION_RECIPE_LOCKS", "_MEALPLAN_LOCKS",
        "_NUTRITION_CORRECTION_STAGES", "_NUTRITION_CORRECTION_STAGE_LOCK", "_nutrition_error",
        "_keyed_lock", "_nutrition_recipe_revision", "_nutrition_continuation",
        "_nutrition_failure", "_authoritative_mealie_food", "_nutrition_service",
        "_nutrition_fetch_recipe", "_nutrition_readiness_assessment",
        "_nutrition_bounded_string", "_nutrition_bound_readiness_output",
        "nutrition_recipe_readiness", "_NUTRITION_CORRECTION_OPERATIONS",
        "_nutrition_authoritative_reference", "_nutrition_canonical_uuid4",
        "_nutrition_url_safe_id", "_nutrition_apply_corrections", "_nutrition_proposed_fields",
        "_nutrition_stage_correction", "nutrition_correct_recipe", "_nutrition_calculate_slug",
        "_nutrition_correction_action", "ensure_recipe_nutrition", "_nutrition_after_import",
        "nutrition_status", "nutrition_food_profile", "nutrition_set_food_profile",
        "nutrition_set_food_mapping", "nutrition_set_conversion_override",
        "nutrition_calculate_recipe", "nutrition_publish_recipe",
        "_nutrition_personal_recipes", "_nutrition_readiness_cookbook_summaries",
        "nutrition_cookbook_readiness", "nutrition_backfill_personal_recipes",
        "nutrition_unresolved_report",
    ),
}


def install(module: ModuleType) -> None:
    """Expose the original namespace while keeping each definition single-owned."""
    owners = {
        name: importlib.import_module(module_name)
        for module_name, names in _EXPORTS.items()
        for name in names
    }
    # Preserve access to imports such as requests, Path, and date as well.
    legacy_imports = ("copy", "json", "hashlib", "math", "os", "re", "sysconfig", "tempfile", "threading", "time", "unicodedata", "urllib", "ThreadPoolExecutor", "as_completed", "date", "Path", "Any", "UUID", "requests", "FastMCP", )
    owners.update({name: sys.modules[__name__] for name in legacy_imports})

    class LegacyModule(ModuleType):
        def __getattr__(self, name):
            if name in owners:
                return getattr(owners[name], name)
            raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

        def __setattr__(self, name, value):
            if name in owners:
                setattr(owners[name], name, value)
            else:
                super().__setattr__(name, value)

        def __delattr__(self, name):
            if name in owners:
                delattr(owners[name], name)
            else:
                super().__delattr__(name)

        def __dir__(self):
            return sorted(set(super().__dir__()) | owners.keys())

    module.__class__ = LegacyModule
