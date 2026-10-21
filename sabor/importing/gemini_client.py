"""Gemini Files API and extraction transport for video imports."""

import json
import re
import time
from pathlib import Path
from typing import Any
import requests
from sabor.core.runtime import config

def _upload_to_gemini(filepath: Path, *, cfg=None) -> dict[str, Any]:
    runtime = cfg.legacy_config if cfg is not None else config
    init_url = f"{runtime._GEMINI_BASE}/upload/v1beta/files?key={runtime.GEMINI_API_KEY}"
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


def _wait_for_gemini_processing(file_name: str, max_wait: int = 120, *, cfg=None) -> bool:
    runtime = cfg.legacy_config if cfg is not None else config
    url = f"{runtime._GEMINI_BASE}/v1beta/{file_name}?key={runtime.GEMINI_API_KEY}"
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


def _cleanup_gemini_file(file_name: str, *, cfg=None) -> None:
    runtime = cfg.legacy_config if cfg is not None else config
    try:
        requests.delete(
            f"{runtime._GEMINI_BASE}/v1beta/{file_name}?key={runtime.GEMINI_API_KEY}", timeout=15
        )
    except Exception:
        pass


def _extract_recipes_with_gemini(
    file_uri: str, language: str, max_recipes: int, *, cfg=None
) -> dict[str, Any]:
    runtime = cfg.legacy_config if cfg is not None else config
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
    for m in [runtime.GEMINI_MODEL] + runtime._GEMINI_FALLBACK_MODELS:
        if m and m not in models:
            models.append(m)
    for model_name in models:
        try:
            r = requests.post(
                f"{runtime._GEMINI_BASE}/v1beta/models/{model_name}:generateContent",
                params={"key": runtime.GEMINI_API_KEY},
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


