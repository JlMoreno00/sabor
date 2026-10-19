"""Gemini video analysis and recipe import."""

import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
import sabor.tools.tags as tags
from sabor.server import mcp

# VIDEO RECIPE — Helpers (Gemini multimodal)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _download_video_from_url(url: str, workdir: Path) -> dict[str, Any]:
    try:
        import yt_dlp
    except ImportError:
        return {
            "ok": False,
            "error": "missing_dependency",
            "detail": "pip install yt-dlp",
        }
    if "tiktok.com" in url.lower():
        return {
            "ok": False,
            "error": "tiktok_url_not_supported",
            "detail": "TikTok bloquea descargas. Envia el video como archivo.",
        }
    workdir.mkdir(parents=True, exist_ok=True)
    opts: dict[str, Any] = {
        "format": "best[height<=720]/bestvideo[height<=720]+bestaudio/best",
        "outtmpl": str(workdir / "%(id)s.%(ext)s"),
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as e:
        return {"ok": False, "error": "download_failed", "detail": str(e)}
    if not isinstance(info, dict):
        return {"ok": False, "error": "video_metadata_missing"}
    video_id = str(info.get("id") or "video")
    skip_exts = {".vtt", ".json", ".description", ".jpg", ".jpeg", ".png", ".webp"}
    media = [
        p
        for p in Path(workdir).glob(f"{video_id}.*")
        if p.suffix.lower() not in skip_exts
    ]
    if not media:
        return {"ok": False, "error": "no_video_file_after_download"}
    return {
        "ok": True,
        "path": str(media[0]),
        "title": str(info.get("title") or ""),
        "duration": int(info.get("duration") or 0),
    }


def _resolve_video_source(source: str) -> dict[str, Any]:
    if not _is_url(source):
        path = Path(source)
        if not path.exists():
            return {"ok": False, "error": "file_not_found", "detail": source}
        if not path.is_file():
            return {"ok": False, "error": "not_a_file", "detail": source}
        return {
            "ok": True,
            "path": str(path),
            "source_type": "file",
            "label": path.name,
        }
    config.VIDEO_WORKDIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(config.VIDEO_WORKDIR)) as tmp:
        result = _download_video_from_url(source, Path(tmp))
        if not result.get("ok"):
            return result
        src = Path(result["path"])
        dest = config.VIDEO_WORKDIR / src.name
        src.rename(dest)
        return {"ok": True, "path": str(dest), "source_type": "url", "label": source}


def _upload_to_gemini(filepath: Path) -> dict[str, Any]:
    init_url = f"{config._GEMINI_BASE}/upload/v1beta/files?key={config.GEMINI_API_KEY}"
    meta = {"file": {"display_name": filepath.stem}}
    r = requests.post(
        init_url,
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(filepath.stat().st_size),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json",
        },
        json=meta,
        timeout=30,
    )
    if r.status_code != 200:
        return {
            "ok": False,
            "error": f"upload_init_failed ({r.status_code})",
            "detail": r.text[:300],
        }
    upload_url = r.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        return {"ok": False, "error": "no_upload_url"}
    data = filepath.read_bytes()
    r2 = requests.post(
        upload_url,
        headers={
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
            "Content-Length": str(len(data)),
        },
        data=data,
        timeout=180,
    )
    if r2.status_code != 200:
        return {
            "ok": False,
            "error": f"upload_failed ({r2.status_code})",
            "detail": r2.text[:300],
        }
    file_info = r2.json().get("file", {})
    return {
        "ok": True,
        "name": file_info.get("name"),
        "uri": file_info.get("uri"),
        "state": file_info.get("state"),
    }


def _wait_for_gemini_processing(file_name: str, max_wait: int = 120) -> bool:
    url = f"{config._GEMINI_BASE}/v1beta/{file_name}?key={config.GEMINI_API_KEY}"
    for _ in range(max_wait // 5):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                state = r.json().get("state", "")
                if state == "ACTIVE":
                    return True
                if state == "FAILED":
                    return False
        except Exception:
            pass
        time.sleep(5)
    return False


def _cleanup_gemini_file(file_name: str) -> None:
    try:
        requests.delete(
            f"{config._GEMINI_BASE}/v1beta/{file_name}?key={config.GEMINI_API_KEY}", timeout=15
        )
    except Exception:
        pass


def _extract_recipes_with_gemini(
    file_uri: str, language: str, max_recipes: int
) -> dict[str, Any]:
    lang_label = {"es": "espanol", "en": "English"}.get(language, language)
    prompt = f"""Analiza este video de cocina en DOS PASOS.

PASO 1 - TRANSCRIPCION DE CANTIDADES:
Escucha el audio con atencion y transcribe LITERALMENTE todas las cantidades,
medidas y numeros mencionados. No resumas.

PASO 2 - EXTRACCION DE RECETAS:
Usando las cantidades del paso anterior, extrae TODAS las recetas (maximo {max_recipes}).

Devuelve un JSON con esta estructura:
{{
  "recipes": [
    {{
      "name": "Nombre del plato en {lang_label}",
      "ingredients": ["200 gramos de harina", "sal (cantidad no especificada)"],
      "steps": ["paso 1 detallado con tiempos y temperaturas"],
      "servings": "numero de raciones",
      "notes": "notas, trucos o variaciones mencionadas",
      "confidence": 0.85
    }}
  ]
}}

Reglas: Nombres en {lang_label}. NO inventes cantidades. Pasos detallados.
Devuelve SOLO el JSON valido."""

    models = []
    for m in [config.GEMINI_MODEL] + config._GEMINI_FALLBACK_MODELS:
        if m and m not in models:
            models.append(m)
    for model_name in models:
        try:
            r = requests.post(
                f"{config._GEMINI_BASE}/v1beta/models/{model_name}:generateContent",
                params={"key": config.GEMINI_API_KEY},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [
                                {
                                    "file_data": {
                                        "mime_type": "video/mp4",
                                        "file_uri": file_uri,
                                    }
                                },
                                {"text": prompt},
                            ],
                        }
                    ],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=120,
            )
            if not (200 <= r.status_code < 300):
                continue
            candidates = r.json().get("candidates") or []
            if not candidates:
                continue
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "\n".join(str(p.get("text", "")) for p in parts if p.get("text"))
            parsed = _parse_json_response(text)
            if not parsed:
                continue
            if "recipes" in parsed and isinstance(parsed["recipes"], list):
                recipes = [_normalize_video_recipe(r) for r in parsed["recipes"]]
                return {"ok": True, "model": model_name, "recipes": recipes}
            elif "name" in parsed:
                return {
                    "ok": True,
                    "model": model_name,
                    "recipes": [_normalize_video_recipe(parsed)],
                }
        except Exception:
            continue
    return {
        "ok": False,
        "error": "gemini_extraction_failed",
        "detail": "All model attempts failed",
    }


def _parse_json_response(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _normalize_video_recipe(obj: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": (obj.get("name") or "").strip(),
        "ingredients": [
            str(x).strip() for x in (obj.get("ingredients") or []) if str(x).strip()
        ],
        "steps": [str(x).strip() for x in (obj.get("steps") or []) if str(x).strip()],
        "servings": str(obj.get("servings") or "").strip(),
        "notes": str(obj.get("notes") or "").strip(),
        "confidence": float(obj.get("confidence") or 0.0),
    }


def _analyze_video(source: str, language: str, max_recipes: int) -> dict[str, Any]:
    if not config.GEMINI_API_KEY:
        return {"ok": False, "error": "missing_GEMINI_API_KEY"}
    resolved = _resolve_video_source(source)
    if not resolved.get("ok"):
        return resolved
    filepath = Path(resolved["path"])
    source_label = resolved.get("label", filepath.name)
    upload = _upload_to_gemini(filepath)
    if resolved.get("source_type") == "url":
        try:
            filepath.unlink(missing_ok=True)
        except Exception:
            pass
    if not upload.get("ok"):
        return upload
    file_name, file_uri = upload["name"], upload["uri"]
    if upload.get("state") != "ACTIVE":
        if not _wait_for_gemini_processing(file_name):
            _cleanup_gemini_file(file_name)
            return {"ok": False, "error": "gemini_processing_failed"}
    extraction = _extract_recipes_with_gemini(file_uri, language, max_recipes)
    _cleanup_gemini_file(file_name)
    if not extraction.get("ok"):
        return extraction
    return {
        "ok": True,
        "source": source_label,
        "model": extraction.get("model"),
        "recipes": extraction["recipes"],
    }


def _create_recipe_in_mealie_from_video(
    recipe: dict[str, Any], source_label: str
) -> dict[str, Any]:
    name = recipe.get("name") or "Receta de video"
    jsonld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": name,
        "description": f"Importado desde video: {source_label}\nConfianza: {recipe.get('confidence', '?')}\n{recipe.get('notes', '')}".strip(),
        "recipeIngredient": recipe.get("ingredients") or ["Ver video original"],
        "recipeInstructions": recipe.get("steps") or ["Ver video original"],
        "recipeYield": recipe.get("servings") or "",
    }
    payload = {"data": json.dumps(jsonld, ensure_ascii=False)}
    r = requests.post(
        f"{config.MEALIE_BASE_URL}/api/recipes/create/html-or-json",
        headers=mealie._mealie_headers(),
        json=payload,
        timeout=60,
    )
    if not (200 <= r.status_code < 300):
        return {"ok": False, "status": r.status_code, "body": r.text[:1200]}
    return mealie._normalization_result(
        {"ok": True, "name": name}, mealie._response_slug(r),
        enabled=bool(recipe.get("ingredients")),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VIDEO RECIPE — Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@mcp.tool()
def analyze_video_recipes(
    source: str, language: str = "es", max_recipes: int = 8
) -> dict[str, Any]:
    """Analiza un video de cocina con Gemini multimodal y extrae recetas. source: ruta local o URL."""
    if max_recipes < 1:
        return {"ok": False, "error": "max_recipes_must_be_positive"}
    return _analyze_video(source, language=language, max_recipes=max_recipes)


@mcp.tool()
def import_video_recipes_to_mealie(
    source: str,
    language: str = "es",
    max_recipes: int = 8,
    min_confidence: float = 0.55,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Extrae recetas de un video y las importa a Mealie. source: ruta local o URL."""
    if err := mealie._check_mealie():
        return err
    artifact = _analyze_video(source, language=language, max_recipes=max_recipes)
    if not artifact.get("ok"):
        return artifact
    source_label = artifact.get("source", "video")
    recipes = artifact.get("recipes") or []
    created, skipped = [], []
    for recipe in recipes:
        confidence = float(recipe.get("confidence") or 0.0)
        name = str(recipe.get("name") or "").strip()
        if confidence < min_confidence:
            skipped.append(
                {"reason": "low_confidence", "name": name, "confidence": confidence}
            )
            continue
        if not name:
            skipped.append({"reason": "missing_name"})
            continue
        # Check for duplicates
        try:
            sr = requests.get(
                f"{config.MEALIE_BASE_URL}/api/recipes",
                headers=mealie._mealie_headers(),
                params={"search": name, "perPage": 100},
                timeout=30,
            )
            if 200 <= sr.status_code < 300:
                for item in sr.json().get("items") or []:
                    if (
                        str(item.get("name") or "").lower().strip()
                        == name.lower().strip()
                    ):
                        skipped.append(
                            {
                                "reason": "duplicate",
                                "name": name,
                                "slug": item.get("slug"),
                            }
                        )
                        name = ""  # flag to skip
                        break
        except Exception:
            pass
        if not name:
            continue
        if dry_run:
            created.append({"ok": True, "name": name, "status": "dry_run"})
            continue
        result = _create_recipe_in_mealie_from_video(recipe, source_label)
        if not result.get("ok"):
            skipped.append({"reason": "create_failed", "name": name, "detail": result})
            continue
        slug = result.get("slug")
        if slug:
            # Tag the created recipe
            try:
                tags.tag_recipe(slug, ["video-import", "tiktok"])
            except Exception:
                pass
        created.append(
            {
                "ok": True, "name": name, "slug": slug, "confidence": confidence,
                **({"nutrition": result["nutrition"]} if "nutrition" in result else {}),
                **({"nutrition_warning": result["nutrition_warning"]}
                   if "nutrition_warning" in result else {}),
            }
        )
    return {
        "ok": True,
        "source": source_label,
        "model": artifact.get("model"),
        "stats": {
            "detected": len(recipes),
            "created": len(created),
            "skipped": len(skipped),
            "min_confidence": min_confidence,
            "dry_run": dry_run,
        },
        "created": created,
        "skipped": skipped,
        "recipes": recipes,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
