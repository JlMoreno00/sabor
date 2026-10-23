"""Bring catalog discovery, authentication and list synchronization."""

import json
import os
import re
import sysconfig
import time
from pathlib import Path
import requests

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


def _load_bring_catalog(lang: str, catalog_dir=None) -> dict[str, str]:
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
    env_catalog = os.getenv("BRING_CATALOG_DIR") if catalog_dir is None else catalog_dir
    if env_catalog:
        search_paths.insert(0, Path(env_catalog))

    for base in search_paths:
        if not base.exists():
            continue
        for p in base.rglob(f"articles.{lang}.json"):
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                continue  # An unreadable catalog must not prevent server startup.
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


def _catalog_match(food_name: str, catalogs=None) -> str | None:
    catalogs = catalogs if catalogs is not None else (_load_bring_catalog('es-ES'), _load_bring_catalog('en-US'))
    norm = food_name.lower().strip()
    norm_no_acc = _remove_accents(norm)
    if norm in _MANUAL_ALIASES:
        return _MANUAL_ALIASES[norm]
    if norm_no_acc in _MANUAL_ALIASES:
        return _MANUAL_ALIASES[norm_no_acc]
    for cat in catalogs:
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
    for cat in catalogs:
        for k, v in cat.items():
            cat_words = set(re.findall(r"\b\w{3,}\b", k))
            if not cat_words:
                continue
            overlap = len(food_words & cat_words) / len(cat_words)
            if overlap >= 0.8 and len(k) >= 3 and overlap > best_score:
                best_score, best_name = overlap, v
    return best_name


class BringClient:
    """Credentials, session, tokens and catalogs belong to one provider."""
    def __init__(self, settings, session=None):
        self.email, self.password = settings.bring_email, settings.bring_password
        self.client_id = "cof4Nc6D8saplXjE3h3HXqHH8m7VU2i1Gs0g85Sp"  # gitleaks:allow
        self.session = session if session is not None else requests.Session()
        self.auth = {}
        directory = settings.legacy_config.BRING_CATALOG_DIR
        self.catalogs = (_load_bring_catalog("es-ES", directory), _load_bring_catalog("en-US", directory))

    def _bring_login_headers(self) -> dict[str, str]:
        return {
            "X-BRING-API-KEY": self.client_id,
            "X-BRING-CLIENT": "webApp",
            "X-BRING-APPLICATION": "bring",
            "Content-Type": "application/x-www-form-urlencoded",
        }


    def _bring_authed_headers(self) -> dict[str, str]:
        return {
            "X-BRING-API-KEY": self.client_id,
            "X-BRING-CLIENT": "webApp",
            "X-BRING-APPLICATION": "bring",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.auth['access_token']}",
            "X-BRING-USER-UUID": self.auth["uuid"],
        }


    def _bring_login(self) -> None:
        r = self.session.post(
            "https://api.getbring.com/rest/v2/bringauth",
            headers=self._bring_login_headers(),
            data={"email": self.email, "password": self.password},
            timeout=30,
        )
        r.raise_for_status()
        d = r.json()
        self.auth["uuid"] = d["uuid"]
        self.auth["access_token"] = d["access_token"]
        self.auth["expires_at"] = time.time() + d.get("expires_in", 3600) - 60


    def _bring_ensure_auth(self) -> None:
        if not self.auth or time.time() > self.auth.get("expires_at", 0):
            self._bring_login()


    def _bring_req(self, method: str, path: str, **kwargs) -> requests.Response:
        self._bring_ensure_auth()
        r = self.session.request(
            method,
            f"https://api.getbring.com/rest{path}",
            headers=self._bring_authed_headers(),
            timeout=30,
            **kwargs,
        )
        if r.status_code == 401:
            self._bring_login()
            r = self.session.request(
                method,
                f"https://api.getbring.com/rest{path}",
                headers=self._bring_authed_headers(),
                timeout=30,
                **kwargs,
            )
        r.raise_for_status()
        return r

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


