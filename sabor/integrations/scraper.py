"""Web recipe scraping and Mealie import."""

import urllib.error
import urllib.request
from typing import Any
import requests
from recipe_scrapers import scrape_html
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.server import mcp

# RECIPE SCRAPER — Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def _fetch_html(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _normalize_scraper(s: Any) -> dict[str, Any]:
    def safe(fn):
        try:
            return fn()
        except Exception:
            return None

    return {
        "title": safe(s.title),
        "total_time": safe(s.total_time),
        "yields": safe(s.yields),
        "image": safe(s.image),
        "host": safe(s.host),
        "ingredients": safe(s.ingredients) or [],
        "instructions": safe(s.instructions),
        "description": safe(s.description),
        "nutrients": safe(s.nutrients),
    }


@mcp.tool()
def scrape_recipe(url: str) -> dict[str, Any]:
    """Extrae una receta de cualquier URL (solo lectura, no guarda en Mealie). Soporta wild_mode."""
    try:
        html = _fetch_html(url)
    except urllib.error.HTTPError as e:
        return {"ok": False, "url": url, "error": f"http_{e.code}", "detail": str(e)}
    except Exception as e:
        return {
            "ok": False,
            "url": url,
            "error": "fetch_failed",
            "detail": str(e)[:300],
        }
    try:
        s = scrape_html(html, org_url=url)
        data = _normalize_scraper(s)
        if data["title"] and (data["ingredients"] or data["instructions"]):
            return {"ok": True, "url": url, "mode": "native", "recipe": data}
    except Exception:
        pass
    try:
        s = scrape_html(html, org_url=url, wild_mode=True)
        data = _normalize_scraper(s)
        if data["title"]:
            return {"ok": True, "url": url, "mode": "wild", "recipe": data}
        return {
            "ok": False,
            "url": url,
            "error": "no_recipe_found",
            "detail": "La pagina no contiene marcado de receta.",
        }
    except Exception as e:
        return {
            "ok": False,
            "url": url,
            "error": "wild_mode_failed",
            "detail": str(e)[:300],
        }


@mcp.tool()
def import_recipe_to_mealie(url: str) -> dict[str, Any]:
    """Importa una receta en Mealie directamente desde una URL (usa el scraper interno de Mealie)."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/recipes/create/url",
            headers=mealie._mealie_headers(),
            json={"url": url, "includeCategories": True, "includeTags": True},
            timeout=60,
        )
        result = {
            "ok": 200 <= r.status_code < 300,
            "status": r.status_code,
            "body": r.text[:4000],
        }
        return mealie._normalization_result(result, mealie._response_slug(r)) if result["ok"] else result
    except Exception as e:
        return {"ok": False, "error": f"import_failed: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
