"""Mealie tag tools."""

import unicodedata
from typing import Any
import requests
import sabor.config as config
import sabor.integrations.mealie as mealie
from sabor.core.toolsets import tool

# MEALIE — Tags
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@tool("core")
def get_or_create_tag(name: str) -> dict[str, Any]:
    """Busca un tag por nombre (case-insensitive); si no existe, lo crea."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/organizers/tags",
            headers=mealie._mealie_headers(),
            params={"search": name, "page": 1, "perPage": 100},
            timeout=30,
        )
        if 200 <= r.status_code < 300:
            requested_tag = "".join(
                char
                for char in unicodedata.normalize("NFKD", name.casefold())
                if not unicodedata.combining(char)
            )
            for t in r.json().get("items") or []:
                tag_names = (t.get("name"), t.get("slug"))
                if any(
                    isinstance(value, str)
                    and "".join(
                        char
                        for char in unicodedata.normalize("NFKD", value.casefold())
                        if not unicodedata.combining(char)
                    )
                    == requested_tag
                    for value in tag_names
                ):
                    return {
                        "ok": True,
                        "id": t["id"],
                        "name": t["name"],
                        "slug": t.get("slug"),
                        "created": False,
                    }
        r2 = requests.post(
            f"{config.MEALIE_BASE_URL}/api/organizers/tags",
            headers=mealie._mealie_headers(),
            json={"name": name},
            timeout=30,
        )
        if not (200 <= r2.status_code < 300):
            return {"ok": False, "status": r2.status_code, "body": r2.text[:500]}
        data = r2.json()
        return {
            "ok": True,
            "id": data.get("id"),
            "name": data.get("name"),
            "slug": data.get("slug"),
            "created": True,
        }
    except Exception as e:
        return {"ok": False, "error": f"get_or_create_tag_failed: {e}"}


@tool("core")
def tag_recipe(slug: str, tags: list[str]) -> dict[str, Any]:
    """Asigna etiquetas a una receta. Conserva tags existentes y anade los nuevos."""
    if err := mealie._check_mealie():
        return err
    try:
        r = requests.get(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=mealie._mealie_headers(),
            timeout=30,
        )
        if not (200 <= r.status_code < 300):
            return {
                "ok": False,
                "status": r.status_code,
                "error": f"recipe_not_found: {slug}",
            }
        existing_tags = {
            t["id"]: {"id": t.get("id"), "name": t.get("name"), "slug": t.get("slug")}
            for t in (r.json().get("tags") or [])
            if t.get("id")
        }
        added = []
        for tag_name in tags:
            tag_result = get_or_create_tag(tag_name)
            if not tag_result.get("ok"):
                return {
                    "ok": False,
                    "error": f"tag_resolution_failed: {tag_name}",
                    "tag_result": tag_result,
                }
            if not all(tag_result.get(field) for field in ("id", "name", "slug")):
                return {
                    "ok": False,
                    "error": f"tag_resolution_incomplete: {tag_name}",
                    "tag_result": tag_result,
                }
            tid = tag_result["id"]
            if tid not in existing_tags:
                existing_tags[tid] = {
                    "id": tid,
                    "name": tag_result["name"],
                    "slug": tag_result["slug"],
                }
                added.append(tag_result["name"])
        all_tags = list(existing_tags.values())
        rp = requests.patch(
            f"{config.MEALIE_BASE_URL}/api/recipes/{slug}",
            headers=mealie._mealie_headers(),
            json={"tags": all_tags},
            timeout=30,
        )
        if not (200 <= rp.status_code < 300):
            return {"ok": False, "status": rp.status_code, "body": rp.text[:500]}
        return {
            "ok": True,
            "slug": slug,
            "tags_added": added,
            "total_tags": len(all_tags),
        }
    except Exception as e:
        return {"ok": False, "error": f"tag_recipe_failed: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
