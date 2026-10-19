"""Bring catalog discovery, authentication and list synchronization."""

import json
import os
import re
import sysconfig
import time
from pathlib import Path
from typing import Any
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.server import mcp

# BRING! — Catalog & Auth Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_QUANTITY_NOUNS = {
    "diente",
    "dientes",
    "lata",
    "latas",
    "taza",
    "tazas",
    "ramita",
    "ramitas",
    "pizca",
    "pizcas",
    "rodaja",
    "rodajas",
    "filete",
    "filetes",
    "trozo",
    "trozos",
    "punado",
    "punados",
    "manojo",
    "manojos",
    "chorrito",
    "chorritos",
    "hoja",
    "hojas",
    "rama",
    "ramas",
    "clove",
    "cloves",
    "can",
    "cans",
    "cup",
    "cups",
    "sprig",
    "sprigs",
    "slice",
    "slices",
    "bunch",
    "bunches",
    "pinch",
    "pinches",
    "piece",
    "pieces",
    "fillet",
    "fillets",
    "leaf",
    "cucharadita",
    "cucharaditas",
    "cucharada",
    "cucharadas",
    "tablespoon",
    "tablespoons",
    "teaspoon",
    "teaspoons",
}

_PREP_WORDS = {
    "picada",
    "picado",
    "picadas",
    "picados",
    "troceada",
    "troceado",
    "troceadas",
    "troceados",
    "en tiras",
    "en rodajas",
    "en brunoise",
    "en cubos",
    "rallado",
    "rallada",
    "rallados",
    "ralladas",
    "fresco",
    "fresca",
    "frescos",
    "frescas",
    "cocido",
    "cocida",
    "cocidos",
    "cocidas",
    "congelado",
    "congelada",
    "congelados",
    "congeladas",
    "enlatado",
    "enlatada",
    "seco",
    "seca",
    "pelado",
    "pelada",
    "pelados",
    "peladas",
    "deshuesado",
    "deshuesada",
    "sin hueso",
    "triturado",
    "triturada",
    "triturados",
    "trituradas",
    "machacado",
    "machacada",
    "tostado",
    "tostada",
}

_MANUAL_ALIASES: dict[str, str] = {
    "bay leaf": "Laurel",
    "bay leaves": "Laurel",
    "laurel": "Laurel",
    "hoja de laurel": "Laurel",
    "hojas de laurel": "Laurel",
    "boniato": "Boniato",
    "batata": "Boniato",
    "camote": "Boniato",
    "sweet potato": "Boniato",
    "sweet potatoes": "Boniato",
    "judias verdes": "Bohnen",
    "judia verde": "Bohnen",
    "green bean": "Bohnen",
    "green beans": "Bohnen",
    "comino": "Comino",
    "cumin": "Comino",
    "cayena": "Cayena",
    "cayenne": "Cayena",
    "red pepper flakes": "Cayena",
    "chili flakes": "Cayena",
    "chile flakes": "Cayena",
    "kale": "Col rizada",
    "col rizada": "Col rizada",
    "pimenton": "Paprikapulver",
    "pimenton ahumado": "Paprikapulver",
    "smoked paprika": "Paprikapulver",
}


def _load_bring_catalog(lang: str) -> dict[str, str]:
    """Load Bring! catalog from site-packages or bundled data."""
    search_paths = []
    # Site-packages (Docker / pip install)
    try:
        search_paths.append(Path(sysconfig.get_paths()["purelib"]))
    except Exception:
        pass
    # Script directory and parent (local dev)
    script_dir = Path(__file__).resolve().parents[2]
    search_paths.append(script_dir)
    search_paths.append(script_dir.parent / ".venv" / "lib")
    # Custom env var
    env_catalog = os.getenv("BRING_CATALOG_DIR")
    if env_catalog:
        search_paths.insert(0, Path(env_catalog))

    for base in search_paths:
        if not base.exists():
            continue
        for p in base.rglob(f"articles.{lang}.json"):
            data = json.loads(p.read_text())
            result: dict[str, str] = {}
            for german_key, localized_name in data.items():
                key = localized_name.lower()
                result[key] = german_key
                words = key.split()
                if words and words[0].endswith("s") and len(words[0]) > 4:
                    singular_key = " ".join([words[0][:-1]] + words[1:])
                    if singular_key not in result:
                        result[singular_key] = german_key
                if len(words) > 1 and words[-1].endswith("s") and len(words[-1]) > 4:
                    singular_last = " ".join(words[:-1] + [words[-1][:-1]])
                    if singular_last not in result:
                        result[singular_last] = german_key
            return result
    return {}


_CAT_ES = _load_bring_catalog("es-ES")
_CAT_EN = _load_bring_catalog("en-US")


def _remove_accents(text: str) -> str:
    for a, b in [
        ("a\u0301", "a"),
        ("e\u0301", "e"),
        ("i\u0301", "i"),
        ("o\u0301", "o"),
        ("u\u0301", "u"),
        ("\u00e1", "a"),
        ("\u00e9", "e"),
        ("\u00ed", "i"),
        ("\u00f3", "o"),
        ("\u00fa", "u"),
        ("\u00e0", "a"),
        ("\u00e8", "e"),
        ("\u00ec", "i"),
        ("\u00f2", "o"),
        ("\u00f9", "u"),
        ("\u00f1", "n"),
        ("\u00fc", "u"),
        ("\u00f6", "o"),
        ("\u00e4", "a"),
    ]:
        text = text.replace(a, b)
    return text


def _catalog_match(food_name: str) -> str | None:
    norm = food_name.lower().strip()
    norm_no_acc = _remove_accents(norm)
    if norm in _MANUAL_ALIASES:
        return _MANUAL_ALIASES[norm]
    if norm_no_acc in _MANUAL_ALIASES:
        return _MANUAL_ALIASES[norm_no_acc]
    for cat in (_CAT_ES, _CAT_EN):
        if norm in cat:
            return cat[norm]
        if norm_no_acc in cat:
            return cat[norm_no_acc]
        candidates = [
            (len(k), v)
            for k, v in cat.items()
            if norm.startswith(k)
            and len(k) >= 3
            and (len(norm) == len(k) or norm[len(k)] in (" ", ",", ";"))
        ]
        if candidates:
            return max(candidates)[1]
        candidates = [
            (len(k), v)
            for k, v in cat.items()
            if len(k) >= 3 and re.search(rf"\b{re.escape(k)}\b", norm)
        ]
        if candidates:
            return max(candidates)[1]
    food_words = set(re.findall(r"\b\w{3,}\b", norm))
    best_score, best_name = 0, None
    for cat in (_CAT_ES, _CAT_EN):
        for k, v in cat.items():
            cat_words = set(re.findall(r"\b\w{3,}\b", k))
            if not cat_words:
                continue
            overlap = len(food_words & cat_words) / len(cat_words)
            if overlap >= 0.8 and len(k) >= 3 and overlap > best_score:
                best_score, best_name = overlap, v
    return best_name


# Bring! auth cache
_bring_auth: dict[str, Any] = {}


def _bring_login_headers() -> dict[str, str]:
    return {
        "X-BRING-API-KEY": config.BRING_PUBLIC_CLIENT_ID,
        "X-BRING-CLIENT": "webApp",
        "X-BRING-APPLICATION": "bring",
        "Content-Type": "application/x-www-form-urlencoded",
    }


def _bring_authed_headers() -> dict[str, str]:
    return {
        "X-BRING-API-KEY": config.BRING_PUBLIC_CLIENT_ID,
        "X-BRING-CLIENT": "webApp",
        "X-BRING-APPLICATION": "bring",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_bring_auth['access_token']}",
        "X-BRING-USER-UUID": _bring_auth["uuid"],
    }


def _bring_login() -> None:
    r = requests.post(
        "https://api.getbring.com/rest/v2/bringauth",
        headers=_bring_login_headers(),
        data={"email": config.BRING_EMAIL, "password": config.BRING_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    _bring_auth["uuid"] = d["uuid"]
    _bring_auth["access_token"] = d["access_token"]
    _bring_auth["expires_at"] = time.time() + d.get("expires_in", 3600) - 60


def _bring_ensure_auth() -> None:
    if not _bring_auth or time.time() > _bring_auth.get("expires_at", 0):
        _bring_login()


def _bring_req(method: str, path: str, **kwargs) -> requests.Response:
    _bring_ensure_auth()
    r = requests.request(
        method,
        f"https://api.getbring.com/rest{path}",
        headers=_bring_authed_headers(),
        timeout=30,
        **kwargs,
    )
    if r.status_code == 401:
        _bring_login()
        r = requests.request(
            method,
            f"https://api.getbring.com/rest{path}",
            headers=_bring_authed_headers(),
            timeout=30,
            **kwargs,
        )
    r.raise_for_status()
    return r


def _normalize_ingredient_text(text: str) -> str:
    for pattern, repl in [
        (r"\bcdtas?\b", "cucharadita"),
        (r"\bcdas\b", "cucharadas"),
        (r"\bcda\b", "cucharada"),
        (r"\bkg\b", "kilogramo"),
        (r"\bml\b", "mililitro"),
    ]:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def _clean_food_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^de\s+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^d'", "", name, flags=re.IGNORECASE)
    m = re.match(r"^(\w+)\s+de\s+(.+)$", name, re.IGNORECASE)
    if m and m.group(1).lower() in _QUANTITY_NOUNS:
        name = m.group(2).strip()
    changed = True
    while changed:
        changed = False
        for prep in _PREP_WORDS:
            new = re.sub(
                rf"\s+{re.escape(prep)}$", "", name, flags=re.IGNORECASE
            ).strip()
            if new != name:
                name, changed = new, True
    if re.search(r"\b(o|or)\b", name, re.IGNORECASE):
        parts = re.split(r"\s+(?:o|or)\s+", name, flags=re.IGNORECASE)
        name = parts[0].strip()
    return name.capitalize() if name else name


def _parse_ingredient(text: str) -> tuple[str, str]:
    text = text.split(",")[0].strip() if re.search(r",\s*\d", text) else text
    text_norm = _normalize_ingredient_text(text)
    food_name, unit, qty = "", "", 0.0
    try:
        r = requests.post(
            f"{config.MEALIE_BASE_URL}/api/parser/ingredient",
            headers=mealie._mealie_headers(),
            json={"ingredient": text_norm},
            timeout=5,
        )
        if r.ok:
            ing = r.json().get("ingredient", {})
            food_name = _clean_food_name((ing.get("food") or {}).get("name", ""))
            unit = (ing.get("unit") or {}).get("name", "")
            qty = ing.get("quantity") or 0.0
    except Exception:
        pass
    if not food_name:
        food_name = text.split("(")[0].strip()
    catalog_name = _catalog_match(food_name)
    item_id = catalog_name if catalog_name else food_name
    qty_str = (
        str(int(qty))
        if qty and qty == int(qty)
        else (str(round(qty, 3)) if qty else "")
    )
    spec = " ".join(filter(None, [qty_str, unit])).strip() or text
    return item_id, spec


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BRING! — Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def bring_get_lists() -> dict[str, Any]:
    """Devuelve todas las listas de compra de Bring! del usuario con sus UUIDs."""
    try:
        _bring_ensure_auth()
        r = _bring_req("GET", f"/bringusers/{_bring_auth['uuid']}/lists")
        lists = [
            {"uuid": l.get("listUuid"), "name": l.get("name"), "theme": l.get("theme")}
            for l in r.json().get("lists", [])
        ]
        return {"ok": True, "total": len(lists), "lists": lists}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def bring_sync_list(list_name: str, items: list[str]) -> dict[str, Any]:
    """Vacia una lista de Bring! y anade los ingredientes semanales con iconos automaticos."""
    try:
        _bring_ensure_auth()
        r = _bring_req("GET", f"/bringusers/{_bring_auth['uuid']}/lists")
        all_lists = r.json().get("lists", [])
        if not all_lists:
            return {"ok": False, "error": "No hay listas en tu cuenta de Bring!"}
        match = next(
            (l for l in all_lists if l.get("name", "").lower() == list_name.lower()),
            None,
        )
        target = match or all_lists[0]
        list_uuid = target["listUuid"]
        used_name = target.get("name", list_uuid)
        # Clear current list
        current = _bring_req("GET", f"/v2/bringlists/{list_uuid}").json()
        purchase = current.get("purchase", [])
        if purchase:
            changes = [
                {
                    "itemId": i.get("itemId") or i.get("name", ""),
                    "spec": "",
                    "operation": "TO_RECENTLY",
                }
                for i in purchase
                if i.get("itemId") or i.get("name")
            ]
            if changes:
                _bring_req(
                    "PUT",
                    f"/v2/bringlists/{list_uuid}/items",
                    json={"changes": changes, "sender": "Sabor"},
                )
        # Parse + catalog match + add (deduplicated)
        seen, changes, parsed_log = set(), [], []
        for raw in items:
            item_id, spec = _parse_ingredient(raw)
            key = item_id.lower()
            if key in seen:
                continue
            seen.add(key)
            changes.append(
                {
                    "itemId": item_id,
                    "spec": spec,
                    "operation": "TO_PURCHASE",
                    "accuracy": "0.0",
                    "altitude": "0.0",
                    "latitude": "0.0",
                    "longitude": "0.0",
                }
            )
            parsed_log.append({"raw": raw, "itemId": item_id, "spec": spec})
        if changes:
            _bring_req(
                "PUT",
                f"/v2/bringlists/{list_uuid}/items",
                json={"changes": changes, "sender": "Sabor"},
            )
        return {
            "ok": True,
            "list_name": used_name,
            "items_added": len(changes),
            "items_cleared": len(purchase),
            "parsed": parsed_log,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@mcp.tool()
def bring_add_items(list_uuid: str, items: list[str]) -> dict[str, Any]:
    """Anade items a una lista de Bring! sin borrar el contenido actual."""
    try:
        changes = []
        for raw in items:
            item_id, spec = _parse_ingredient(raw)
            changes.append(
                {
                    "itemId": item_id,
                    "spec": spec,
                    "operation": "TO_PURCHASE",
                    "accuracy": "0.0",
                    "altitude": "0.0",
                    "latitude": "0.0",
                    "longitude": "0.0",
                }
            )
        _bring_req(
            "PUT",
            f"/v2/bringlists/{list_uuid}/items",
            json={"changes": changes, "sender": "Sabor"},
        )
        return {"ok": True, "list_uuid": list_uuid, "items_added": len(items)}
    except Exception as e:
        return {"ok": False, "error": str(e)}



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
