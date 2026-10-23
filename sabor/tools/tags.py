"""Mealie tag tools."""

from typing import Any
from sabor.core.toolsets import current_context, tool, store_ready
from sabor.core.envelope import fail
from sabor.store import StoreError, store_error_envelope
def _taxonomy_call(operation, *args):
    store = current_context().store
    if store is None:
        return fail(
            "provider_unconfigured", "Recipe store is not configured.", "mealie"
        )
    try:
        value = getattr(store, operation)(*args)
        legacy = getattr(value, "result", None)
        if legacy is not None:
            return legacy
        if operation == "get_or_create_tag":
            return {
                "ok": True,
                "id": value.id,
                "name": value.name,
                "slug": getattr(value, "slug", None),
                "created": getattr(value, "created", False),
            }
        if operation == "tag_recipe":
            return {
                "ok": True,
                "slug": args[0],
                "tags_added": args[1],
                "total_tags": len(store.get_recipe(args[0]).tags or ()),
            }
        if operation == "add_recipe_comment":
            return {"ok": True, "comment_id": value, "slug": args[0], "text": args[1]}
        return {"ok": True, "event_id": value, "slug": args[0], "subject": args[1]}
    except StoreError as exc:
        return getattr(exc, "result", None) or store_error_envelope(exc)

# MEALIE — Tags
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@tool("core", requires=store_ready, uses_context=True)
def get_or_create_tag(name: str) -> dict[str, Any]:
    """Busca un tag por nombre (case-insensitive); si no existe, lo crea."""
    return _taxonomy_call("get_or_create_tag", name)


@tool("core", requires=store_ready, uses_context=True)
def tag_recipe(slug: str, tags: list[str]) -> dict[str, Any]:
    """Asigna etiquetas a una receta. Conserva tags existentes y anade los nuevos."""
    return _taxonomy_call("tag_recipe", slug, tags)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
