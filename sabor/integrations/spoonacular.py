"""Spoonacular search and nutrition integration."""

from typing import Any
import requests
import sabor.config as config
from sabor.core.toolsets import tool

# SPOONACULAR — Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _spoonacular_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not config.SPOONACULAR_API_KEY:
        return {
            "ok": False,
            "error": "missing_api_key",
            "message": "SPOONACULAR_API_KEY no configurada.",
        }
    request_params: dict[str, Any] = dict(params or {})
    request_params["apiKey"] = config.SPOONACULAR_API_KEY
    try:
        r = requests.get(
            f"{config.SPOONACULAR_BASE}{path}",
            params=request_params,
            timeout=config.SPOONACULAR_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return {
            "ok": False,
            "error": "timeout",
            "message": f"Spoonacular timeout ({config.SPOONACULAR_TIMEOUT}s)",
        }
    except requests.exceptions.ConnectionError as exc:
        return {"ok": False, "error": "connection_error", "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": "unexpected_error", "message": str(exc)}
    if r.status_code == 401:
        return {
            "ok": False,
            "error": "auth_error",
            "message": "API key invalida (HTTP 401)",
        }
    if r.status_code == 402:
        return {
            "ok": False,
            "error": "quota_exceeded",
            "message": "Cuota diaria agotada (HTTP 402)",
        }
    if r.status_code == 429:
        return {
            "ok": False,
            "error": "rate_limited",
            "message": "Rate limit (HTTP 429)",
        }
    if not (200 <= r.status_code < 300):
        return {"ok": False, "error": f"http_{r.status_code}", "message": r.text[:400]}
    try:
        return {"ok": True, "data": r.json()}
    except Exception as exc:
        return {"ok": False, "error": "json_parse_error", "message": str(exc)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SPOONACULAR — Tools (prefixed to avoid name conflicts with Mealie)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@tool("legacy_spoonacular")
def spoonacular_search_recipes(
    query: str,
    number: int = 5,
    max_calories: float | None = None,
    min_calories: float | None = None,
    max_protein_g: float | None = None,
    min_protein_g: float | None = None,
    max_fat_g: float | None = None,
    min_fat_g: float | None = None,
    max_carbs_g: float | None = None,
    min_carbs_g: float | None = None,
) -> dict[str, Any]:
    """Busca recetas ONLINE en Spoonacular con filtros nutricionales opcionales. Usar cuando Mealie no tiene resultados."""
    params: dict[str, Any] = {"query": query, "number": max(1, min(number, 100))}
    nutrient_map = {
        "maxCalories": max_calories,
        "minCalories": min_calories,
        "maxProtein": max_protein_g,
        "minProtein": min_protein_g,
        "maxFat": max_fat_g,
        "minFat": min_fat_g,
        "maxCarbs": max_carbs_g,
        "minCarbs": min_carbs_g,
    }
    has_nutrient = False
    for api_name, value in nutrient_map.items():
        if value is not None:
            params[api_name] = value
            has_nutrient = True
    if has_nutrient:
        params["addRecipeNutrition"] = True
    params["addRecipeInformation"] = False
    result = _spoonacular_get("/recipes/complexSearch", params)
    if not result["ok"]:
        return result
    data = result["data"]
    recipes = []
    for r in data.get("results") or []:
        entry: dict[str, Any] = {
            "id": r.get("id"),
            "title": r.get("title"),
            "image": r.get("image"),
            "source_url": r.get("sourceUrl"),
        }
        nutrition = r.get("nutrition")
        if nutrition and isinstance(nutrition, dict):
            for n in nutrition.get("nutrients") or []:
                name = n.get("name", "")
                if name == "Calories":
                    entry["calories_kcal"] = n.get("amount")
                elif name == "Protein":
                    entry["protein_g"] = n.get("amount")
                elif name == "Fat":
                    entry["fat_g"] = n.get("amount")
                elif name == "Carbohydrates":
                    entry["carbs_g"] = n.get("amount")
        recipes.append(entry)
    return {
        "ok": True,
        "query": query,
        "total": data.get("totalResults", len(recipes)),
        "recipes": recipes,
    }


@tool("legacy_spoonacular")
def spoonacular_search_by_nutrients(
    min_protein_g: float | None = None,
    max_calories_kcal: float | None = None,
    max_carbs_g: float | None = None,
    number: int = 5,
) -> dict[str, Any]:
    """Busca recetas por criterios nutricionales en Spoonacular."""
    params: dict[str, Any] = {"number": max(1, min(number, 100))}
    if min_protein_g is not None:
        params["minProtein"] = min_protein_g
    if max_calories_kcal is not None:
        params["maxCalories"] = max_calories_kcal
    if max_carbs_g is not None:
        params["maxCarbs"] = max_carbs_g
    result = _spoonacular_get("/recipes/findByNutrients", params)
    if not result["ok"]:
        return result
    raw = result["data"]
    items = raw if isinstance(raw, list) else []
    recipes = [
        {
            "id": r.get("id"),
            "title": r.get("title"),
            "image": r.get("image"),
            "calories": r.get("calories"),
            "protein": r.get("protein"),
            "fat": r.get("fat"),
            "carbs": r.get("carbs"),
        }
        for r in items
    ]
    return {"ok": True, "filters": params, "recipes": recipes}


@tool("legacy_spoonacular")
def spoonacular_nutrition_100g(ingredient: str) -> dict[str, Any]:
    """Informacion nutricional por cada 100g de un ingrediente via Spoonacular."""
    search_result = _spoonacular_get(
        "/food/ingredients/search",
        {"query": ingredient, "number": 1, "metaInformation": True},
    )
    if not search_result["ok"]:
        return search_result
    results = (search_result["data"] or {}).get("results") or []
    if not results:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"Ingrediente '{ingredient}' no encontrado.",
        }
    ingredient_id = results[0]["id"]
    found_name = results[0].get("name", ingredient)
    info_result = _spoonacular_get(
        f"/food/ingredients/{ingredient_id}/information",
        {"amount": 100, "unit": "grams"},
    )
    if not info_result["ok"]:
        return info_result
    nutrients_raw = (info_result["data"].get("nutrition") or {}).get("nutrients") or []
    nutrients = {
        n["name"]: {"amount": n.get("amount"), "unit": n.get("unit")}
        for n in nutrients_raw
    }
    return {
        "ok": True,
        "ingredient": found_name,
        "spoonacular_id": ingredient_id,
        "amount_g": 100,
        "nutrients": nutrients,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
