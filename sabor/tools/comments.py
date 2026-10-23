"""Recipe comments and cooking logs."""

from typing import Any
from sabor.core.toolsets import tool, store_ready
from sabor.tools.tags import _taxonomy_call


@tool("core", requires=store_ready, uses_context=True)
def add_recipe_comment(slug: str, text: str) -> dict[str, Any]:
    """Anade un comentario a una receta en Mealie (rating, notas, feedback)."""
    return _taxonomy_call("add_recipe_comment", slug, text)


@tool("core", requires=store_ready, uses_context=True)
def log_cooking_event(
    slug: str, subject: str, message: str | None = None
) -> dict[str, Any]:
    """Registra un evento en el timeline de una receta (cooking log / historial)."""
    return _taxonomy_call("log_cooking_event", slug, subject, message)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
