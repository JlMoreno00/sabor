"""Preference memory and pantry tools."""

import re
from datetime import date
from typing import Any
from sabor.core.runtime import config
from sabor.core.toolsets import tool

# MEMORY — Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _read_memory() -> str:
    if config.MEMORY_FILE.exists():
        return config.MEMORY_FILE.read_text(encoding="utf-8")
    return ""


def _write_memory(content: str) -> None:
    config.MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.MEMORY_FILE.write_text(content, encoding="utf-8")


def _ensure_section(content: str, header: str, default_body: str = "") -> str:
    if re.search(rf"^{re.escape(header)}", content, re.MULTILINE):
        return content
    return content.rstrip("\n") + f"\n\n{header}\n{default_body}"


def _get_section_content(content: str, header: str) -> str:
    pattern = rf"(?:^|\n){re.escape(header)}\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, content, re.DOTALL)
    return m.group(1).strip() if m else ""


def _replace_section(content: str, header: str, new_body: str) -> str:
    pattern = rf"({re.escape(header)}\n)(.*?)(?=\n## |\Z)"
    result, count = re.subn(pattern, rf"\g<1>{new_body}\n", content, flags=re.DOTALL)
    if count == 0:
        result = content.rstrip("\n") + f"\n\n{header}\n{new_body}\n"
    return result


def _rating_to_stars(rating: int) -> str:
    rating = max(1, min(5, int(rating)))
    return "\u2605" * rating + "\u2606" * (5 - rating)


_MEMORY_TEMPLATE = """# Memoria del Agente de Cocina

## Feedback Historial

## Top Recetas Favoritas

## Blacklist Recetas

## Preferencias Aprendidas

## Despensa Conversacional
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MEMORY — Tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@tool("legacy_memory")
def save_feedback(
    recipe_name: str,
    rating: int | None = None,
    comment: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Persist recipe feedback in MEMORY.md (history, favorites, blacklist)."""
    if not recipe_name:
        return {"ok": False, "error": "recipe_name is required"}
    tags = tags or []
    today = date.today().isoformat()
    stars = _rating_to_stars(rating) if rating else "sin-rating"
    comment_str = comment.strip() if comment else "[sin-comentario]"
    tags_str = " ".join(t if t.startswith("#") else f"#{t}" for t in tags)
    content = _read_memory() or _MEMORY_TEMPLATE
    # Historial
    content = _ensure_section(content, "## Feedback Historial")
    new_entry = f"- {today} | {recipe_name} | {stars} | {comment_str}"
    if tags_str:
        new_entry += f" | {tags_str}"
    hist_content = _get_section_content(content, "## Feedback Historial")
    lines = [l for l in hist_content.split("\n") if l.startswith("- ")]
    policy_lines = [l for l in hist_content.split("\n") if l.startswith("**Politica")]
    lines.append(new_entry)
    new_hist = "\n".join(lines)
    if policy_lines:
        new_hist += "\n\n" + "\n".join(policy_lines)
    content = _replace_section(content, "## Feedback Historial", new_hist)
    # Blacklist
    if "#nunca-mas" in tags or "nunca-mas" in tags:
        content = _ensure_section(content, "## Blacklist Recetas")
        bl_content = _get_section_content(content, "## Blacklist Recetas")
        bl_lines = [l for l in bl_content.split("\n") if l.strip()]
        if not any(recipe_name.lower() in l.lower() for l in bl_lines):
            bl_lines.append(f"- {recipe_name} | motivo: nunca-mas | desde: {today}")
        content = _replace_section(content, "## Blacklist Recetas", "\n".join(bl_lines))
    # Top Favoritas
    is_fav = rating and rating >= 4
    if is_fav or "#favorita" in tags or "favorita" in tags:
        content = _ensure_section(content, "## Top Recetas Favoritas")
        fav_content = _get_section_content(content, "## Top Recetas Favoritas")
        fav_lines = [
            l
            for l in fav_content.split("\n")
            if l.strip() and not l.startswith("**") and not l.startswith("(")
        ]
        idx = next(
            (i for i, l in enumerate(fav_lines) if recipe_name.lower() in l.lower()),
            None,
        )
        entry_line = f"- {recipe_name} | rating: {rating or '?'}/5 | ult: {today}"
        if idx is not None:
            fav_lines[idx] = entry_line
        else:
            fav_lines.append(entry_line)
        fav_lines = fav_lines[-10:]
        content = _replace_section(
            content, "## Top Recetas Favoritas", "\n".join(fav_lines)
        )
    _write_memory(content)
    verify = _read_memory()
    return {
        "ok": True,
        "persisted": recipe_name in verify and new_entry in verify,
        "date": today,
        "recipe": recipe_name,
        "rating": stars,
        "comment": comment_str,
        "tags": tags_str,
        "added_to_blacklist": "#nunca-mas" in tags or "nunca-mas" in tags,
        "added_to_favorites": bool(is_fav or "#favorita" in tags or "favorita" in tags),
    }


@tool("legacy_memory")
def read_memory(section: str | None = None) -> dict[str, Any]:
    """Read all of MEMORY.md or a specific section."""
    content = _read_memory()
    if not content:
        return {"ok": False, "error": "MEMORY.md no existe o esta vacio"}
    if section:
        header = f"## {section}"
        body = _get_section_content(content, header)
        if not body and section not in content:
            return {"ok": False, "error": f"Seccion '{section}' no encontrada"}
        return {"ok": True, "section": section, "content": body}
    return {"ok": True, "content": content}


@tool("legacy_memory")
def update_pantry(items: list[str]) -> dict[str, Any]:
    """Update the 'Despensa Conversacional' section with available ingredients."""
    if not items:
        return {"ok": False, "error": "items list is empty"}
    content = _read_memory() or _MEMORY_TEMPLATE
    today = date.today().isoformat()
    new_body = f"(Actualizado: {today})\n" + "\n".join(f"- {it}" for it in items)
    content = _ensure_section(content, "## Despensa Conversacional")
    content = _replace_section(content, "## Despensa Conversacional", new_body)
    _write_memory(content)
    return {
        "ok": True,
        "persisted": items[0] in _read_memory(),
        "items_saved": len(items),
        "updated": today,
    }


@tool("legacy_memory")
def update_learned_preference(preference: str) -> dict[str, Any]:
    """Add a learned user preference to MEMORY.md."""
    if not preference:
        return {"ok": False, "error": "preference text is required"}
    content = _read_memory() or _MEMORY_TEMPLATE
    content = _ensure_section(content, "## Preferencias Aprendidas")
    pref_content = _get_section_content(content, "## Preferencias Aprendidas")
    lines = [l for l in pref_content.split("\n") if l.strip() and not l.startswith("(")]
    already = any(preference.lower()[:40] in l.lower() for l in lines)
    if not already:
        lines.append(f"- {preference} (desde {date.today().isoformat()})")
    content = _replace_section(content, "## Preferencias Aprendidas", "\n".join(lines))
    _write_memory(content)
    return {"ok": True, "added": not already, "preference": preference}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
