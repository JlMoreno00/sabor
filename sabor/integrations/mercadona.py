"""Mercadona catalog cache and price tools."""

from contextvars import copy_context
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
                pool.submit(copy_context().run, _mercadona_fetch_subcategory, sub_id, name): (sub_id, name)
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
def mercadona_get_price_drops() -> dict[str, Any]:
    """Get products with reduced prices from Mercadona.

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


