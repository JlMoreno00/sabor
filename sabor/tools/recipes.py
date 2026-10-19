"""Mealie recipe tools."""

import json
from typing import Any
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.server import mcp

# MEALIE — Recipes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def search_recipes(query: str) -> dict[str, Any]:
    """Busca recetas en Mealie por nombre. Devuelve lista con nombre y slug."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/recipes",
            headers=mealie._mealie_headers(),
            params={
                "search": query,
                "perPage": 200,
                "page": 1,
                "orderBy": "name",
                "orderDirection": "asc",
            },
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:1000]}
        items = r.json().get("items") or []
        matches = [{"name": it.get("name"), "slug": it.get("slug")} for it in items]
        return {"ok": True, "query": query, "total": len(matches), "items": matches}
    except Exception as e:
        return {"ok": False, "error": f"search_failed: {e}"}


@mcp.tool()
def get_recipe(slug: str) -> dict[str, Any]:
    """Obtiene una receta completa por su slug (incluyendo ingredientes e instrucciones)."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {"ok": False, "status": r.status_code, "body": r.text[:1000]}
        data = r.json()
        return {
            "ok": True,
            "id": data.get("id"),
            "name": data.get("name"),
            "slug": data.get("slug"),
            "description": data.get("description", ""),
            "recipeYield": data.get("recipeYield", ""),
            "totalTime": data.get("totalTime", ""),
            "recipeIngredient": [
                i.get("display", "") for i in (data.get("recipeIngredient") or [])
            ],
            "recipeInstructions": [
                s.get("text", "") for s in (data.get("recipeInstructions") or [])
            ],
            "tags": [
                {"id": t.get("id"), "name": t.get("name"), "slug": t.get("slug")}
                for t in (data.get("tags") or [])
            ],
        }
    except Exception as e:
        return {"ok": False, "error": f"get_recipe_failed: {e}"}


@mcp.tool()
def create_recipe(
    name: str,
    url: str | None = None,
    description: str | None = None,
    recipe_yield: str | None = None,
    total_time: str | None = None,
    ingredients: list[str] | None = None,
    instructions: list[str] | None = None,
    tags: list[str] | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Crea o importa una receta en Mealie, con campos estructurados opcionales."""
    if err := mealie._check_mealie():
        return err
    try:
        if url:
            payload = {"url": url, "includeCategories": True, "includeTags": True}
            r = requests.post(
                f"{config.MEALIE_BASE_URL}/api/recipes/create/url",
                headers=mealie._mealie_headers(),
                json=payload,
                timeout=60,
            )
            result = {
                "ok": 200 <= r.status_code < 300,
                "status": r.status_code,
                "body": r.text[:2000],
            }
            return mealie._normalization_result(result, mealie._response_slug(r)) if result["ok"] else result
        structured_values = (
            description,
            recipe_yield,
            total_time,
            ingredients,
            instructions,
            tags,
            source_url,
        )
        is_structured = any(value is not None for value in structured_values)
        jsonld_recipe: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Recipe",
            "name": name,
        }
        if is_structured:
            optional_fields = {
                "description": description,
                "recipeYield": recipe_yield,
                "totalTime": total_time,
                "recipeIngredient": ingredients,
                "recipeInstructions": instructions,
                "keywords": tags,
                "url": source_url,
            }
            jsonld_recipe.update(
                {key: value for key, value in optional_fields.items() if value is not None}
            )
        else:
            jsonld_recipe.update(
                {
                    "recipeIngredient": ["Ingrediente a completar"],
                    "recipeInstructions": ["Instrucciones a completar"],
                    "recipeYield": "2 raciones",
                }
            )
        wrapped_payload = {"data": json.dumps(jsonld_recipe, ensure_ascii=False)}
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/recipes/create/html-or-json",
            headers=mealie._mealie_headers(),
            json=wrapped_payload,
            timeout=60,
        )
        result = {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "body": r.text[:2000],
        }
        return mealie._normalization_result(result, mealie._response_slug(r), enabled=is_structured) if result["ok"] else result
    except Exception as e:
        return {"ok": False, "error": f"create_failed: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
