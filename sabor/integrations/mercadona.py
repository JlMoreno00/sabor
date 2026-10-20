"""Mercadona catalog cache and price tools."""

import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
import requests
from sabor.core.toolsets import tool

# SUPERMARKET — Mercadona Prices
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Mercadona API (public, no auth required)
MERCADONA_BASE_URL = "https://tienda.mercadona.es/api"
MERCADONA_WAREHOUSE = os.getenv("MERCADONA_WAREHOUSE", "mad1")
MERCADONA_TIMEOUT = 20
_MERCADONA_CATALOG_TTL = 6 * 3600  # 6 hours
_MERCADONA_FETCH_WORKERS = 8
_MERCADONA_FETCH_DELAY = 0.3

_mercadona_catalog: list[dict[str, Any]] = []
_mercadona_catalog_loaded_at: float = 0.0
_mercadona_catalog_loading: bool = False


def _mercadona_normalize(text: str) -> str:
    """Lowercase and strip accents from text for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _mercadona_relevance_score(product_name_norm: str, query_norm: str) -> int:
    """Return relevance score: 3=exact, 2=starts_with, 1=all_words, 0=substring, -1=no match."""
    if product_name_norm == query_norm:
        return 3
    if product_name_norm.startswith(query_norm):
        return 2
    query_words = query_norm.split()
    if all(w in product_name_norm for w in query_words):
        return 1
    if query_norm in product_name_norm:
        return 0
    return -1


def _mercadona_cost_query(text: str) -> str:
    """Strip leading amounts/units and connector words for cost matching."""
    words = re.findall(r"\w+(?:[.,/]\d+)?", _mercadona_normalize(text))
    prefixes = {"un", "una", "uno", "dos", "g", "gr", "gramos", "gramo",
                "kg", "kilogramo", "kilogramos", "ml", "l", "litro", "litros",
                "mililitro", "mililitros", "de"}
    while words and (words[0] in prefixes or words[0][0].isdigit()):
        words.pop(0)
    return " ".join(w for w in words if w not in {"de", "del"})


def _mercadona_cost_score(product: str, query: str) -> int:
    """Require every query token to be a whole product-name word."""
    words = query.split()
    if not words or not set(words).issubset(set(re.findall(r"\w+", product))):
        return -1
    return _mercadona_relevance_score(product, query)


def _mercadona_get(path: str) -> dict:
    """GET helper — returns {ok: True, data: ...} or {ok: False, error: ...}."""
    try:
        url = f"{MERCADONA_BASE_URL}{path}"
        params = {"lang": "es", "wh": MERCADONA_WAREHOUSE}
        resp = requests.get(
            url,
            params=params,
            timeout=MERCADONA_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 404:
            return {"ok": False, "error": "not_found", "status": 404}
        if not (200 <= resp.status_code < 300):
            return {"ok": False, "error": f"http_{resp.status_code}", "body": resp.text[:500]}
        return {"ok": True, "data": resp.json()}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "timeout"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _mercadona_extract_product(product: dict, category_name: str) -> dict:
    """Flatten a Mercadona product dict into a simplified structure."""
    pi = product.get("price_instructions") or {}
    return {
        "id": str(product.get("id", "")),
        "name": product.get("display_name", ""),
        "category": category_name,
        "unit_price": pi.get("unit_price"),
        "bulk_price": pi.get("bulk_price"),
        "reference_price": pi.get("reference_price"),
        "previous_unit_price": pi.get("previous_unit_price"),
        "price_decreased": pi.get("price_decreased", False),
        "iva": pi.get("iva"),
        "size_format": pi.get("size_format"),
        "unit_size": pi.get("unit_size"),
        "thumbnail": product.get("thumbnail"),
        "is_new": pi.get("is_new", False),
    }


def _mercadona_fetch_subcategory(sub_id: int, cat_name: str) -> list[dict]:
    """Fetch all products from a single Mercadona subcategory.

    The API hierarchy is 3 levels deep:
      /categories/ -> root cats -> subcats -> sub-subcats (contain products)
    """
    result = _mercadona_get(f"/categories/{sub_id}/")
    if not result.get("ok"):
        return []
    data = result["data"]
    all_products: list[dict] = []
    for sub_cat in data.get("categories", []):
        sub_cat_name = sub_cat.get("name") or cat_name
        for p in sub_cat.get("products", []):
            all_products.append(_mercadona_extract_product(p, sub_cat_name))
    for p in data.get("products", []):
        all_products.append(_mercadona_extract_product(p, cat_name))
    return all_products


def _mercadona_load_catalog() -> tuple[bool, str]:
    """Fetch the entire Mercadona catalog in parallel. Returns (success, message)."""
    global _mercadona_catalog, _mercadona_catalog_loaded_at, _mercadona_catalog_loading
    _mercadona_catalog_loading = True
    try:
        root = _mercadona_get("/categories/")
        if not root.get("ok"):
            return False, f"Error al obtener categorias: {root.get('error')}"
        raw = root["data"]
        if isinstance(raw, list):
            categories = raw
        else:
            categories = raw.get("results", raw.get("categories", []))

        # Collect all (sub_id, cat_name) pairs
        tasks: list[tuple[int, str]] = []
        for cat in categories:
            cat_name = cat.get("name", "")
            for sub in cat.get("categories", []):
                tasks.append((sub.get("id"), cat_name))

        all_products: list[dict] = []
        with ThreadPoolExecutor(max_workers=_MERCADONA_FETCH_WORKERS) as pool:
            futures = {
                pool.submit(_mercadona_fetch_subcategory, sub_id, name): (sub_id, name)
                for sub_id, name in tasks
                if sub_id is not None
            }
            for future in as_completed(futures):
                try:
                    batch = future.result()
                    all_products.extend(batch)
                    time.sleep(_MERCADONA_FETCH_DELAY)
                except Exception:
                    pass

        _mercadona_catalog = all_products
        _mercadona_catalog_loaded_at = time.time()
        return True, f"Catalogo cargado: {len(all_products)} productos"
    except Exception as e:
        return False, f"Fallo al cargar catalogo: {e}"
    finally:
        _mercadona_catalog_loading = False


def _mercadona_ensure_catalog() -> dict | None:
    """Lazy-load catalog if stale or empty. Returns error dict on failure, None on success."""
    now = time.time()
    if _mercadona_catalog and (now - _mercadona_catalog_loaded_at) < _MERCADONA_CATALOG_TTL:
        return None
    if _mercadona_catalog_loading:
        return {
            "ok": False,
            "error": "catalog_loading",
            "message": "El catalogo se esta cargando, intenta de nuevo en unos segundos",
        }
    ok, msg = _mercadona_load_catalog()
    if not ok:
        return {"ok": False, "error": "catalog_load_failed", "message": msg}
    return None


@tool("legacy_pricing")
def mercadona_search_products(query: str, limit: int = 10) -> dict[str, Any]:
    """Search Mercadona products by name with relevance ranking.

    Returns a ranked list of matching products with prices and category info.
    Loads the full catalog on first call (may take 10-30 seconds).
    """
    if err := _mercadona_ensure_catalog():
        return err
    q_norm = _mercadona_normalize(query.strip())
    if not q_norm:
        return {
            "ok": False,
            "error": "query_empty",
            "message": "La busqueda no puede estar vacia",
        }

    scored: list[tuple[int, dict]] = []
    for product in _mercadona_catalog:
        name_norm = _mercadona_normalize(product.get("name", ""))
        score = _mercadona_relevance_score(name_norm, q_norm)
        if score >= 0:
            scored.append((score, product))

    scored.sort(key=lambda x: (-x[0], x[1].get("name", "")))
    results = [p for _, p in scored[:limit]]
    return {
        "ok": True,
        "query": query,
        "total_matches": len(scored),
        "results": results,
    }


@tool("legacy_pricing")
def mercadona_get_product_price(product_id: str) -> dict[str, Any]:
    """Get detailed price information for a specific Mercadona product by ID.

    Returns full product details including unit price, bulk price, reference price,
    IVA, size info, and whether the price has been reduced.
    """
    result = _mercadona_get(f"/products/{product_id}/")
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error"),
            "message": f"No se pudo obtener el producto {product_id}",
        }
    product = result["data"]
    pi = product.get("price_instructions") or {}
    photos = product.get("photos") or []
    return {
        "ok": True,
        "id": str(product.get("id", "")),
        "name": product.get("display_name", ""),
        "unit_price": pi.get("unit_price"),
        "bulk_price": pi.get("bulk_price"),
        "reference_price": pi.get("reference_price"),
        "previous_unit_price": pi.get("previous_unit_price"),
        "price_decreased": pi.get("price_decreased", False),
        "iva": pi.get("iva"),
        "size_format": pi.get("size_format"),
        "unit_size": pi.get("unit_size"),
        "thumbnail": photos[0].get("thumbnail") if photos else None,
        "is_new": product.get("is_new", False),
    }


@tool("legacy_pricing")
def mercadona_estimate_recipe_cost(ingredients: list[str]) -> dict[str, Any]:
    """Estimate the cost of a recipe by searching Mercadona prices for each ingredient.

    For each ingredient, finds the best-matching product and returns its price.
    Returns a breakdown per ingredient plus total estimated cost in EUR.
    """
    if err := _mercadona_ensure_catalog():
        return err
    breakdown: list[dict] = []
    total_cost = 0.0
    not_found: list[str] = []

    for ingredient in ingredients:
        q_norm = _mercadona_cost_query(ingredient)
        best_score = -1
        best_product = None
        for product in _mercadona_catalog:
            name_norm = _mercadona_normalize(product.get("name", ""))
            score = _mercadona_cost_score(name_norm, q_norm)
            if score > best_score:
                best_score = score
                best_product = product

        if best_product and best_score >= 0:
            price = best_product.get("unit_price")
            try:
                price_f = float(price) if price is not None else 0.0
            except (ValueError, TypeError):
                price_f = 0.0
            total_cost += price_f
            breakdown.append(
                {
                    "ingredient": ingredient,
                    "matched_product": best_product.get("name"),
                    "product_id": best_product.get("id"),
                    "unit_price": price,
                    "category": best_product.get("category"),
                    "relevance_score": best_score,
                }
            )
        else:
            not_found.append(ingredient)
            breakdown.append(
                {
                    "ingredient": ingredient,
                    "matched_product": None,
                    "unit_price": None,
                    "relevance_score": -1,
                }
            )

    return {
        "ok": True,
        "breakdown": breakdown,
        "total_estimated_cost": round(total_cost, 2),
        "total_full_package_cost": round(total_cost, 2),
        "cost_basis": "full_package_price",
        "note": "Sum of full package prices, not the cost of quantities used.",
        "not_found": not_found,
        "currency": "EUR",
    }


@tool("legacy_pricing")
def mercadona_estimate_shopping_list_cost(items: list[str]) -> dict[str, Any]:
    """Estimate the total cost of a shopping list using Mercadona prices.

    Searches for each item in the Mercadona catalog and returns a price breakdown
    with total estimated cost in EUR.
    """
    return mercadona_estimate_recipe_cost(items)


@tool("legacy_pricing")
def mercadona_get_price_drops() -> dict[str, Any]:
    """Get products with reduced prices (bajadas de precio) from Mercadona.

    Returns all products where price_decreased is True, sorted by category.
    Loads the catalog if not yet cached.
    """
    if err := _mercadona_ensure_catalog():
        return err
    drops = [p for p in _mercadona_catalog if p.get("price_decreased")]
    drops.sort(key=lambda x: (x.get("category", ""), x.get("name", "")))
    return {
        "ok": True,
        "total": len(drops),
        "products": drops,
    }


@tool("legacy_pricing")
def mercadona_get_new_arrivals() -> dict[str, Any]:
    """Get new products recently added to Mercadona's catalog.

    Returns all products where is_new is True, sorted by category.
    Loads the catalog if not yet cached.
    """
    if err := _mercadona_ensure_catalog():
        return err
    new_products = [p for p in _mercadona_catalog if p.get("is_new")]
    new_products.sort(key=lambda x: (x.get("category", ""), x.get("name", "")))
    return {
        "ok": True,
        "total": len(new_products),
        "products": new_products,
    }


