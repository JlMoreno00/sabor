"""Structured household preference tools."""
from sabor.core.envelope import fail
from sabor.core.toolsets import current_context, tool
from sabor.preferences import preferences_call


@tool("preferences", uses_context=True)
def get_preferences() -> dict:
    """Read structured household preferences."""
    return preferences_call(current_context())


@tool("preferences", uses_context=True)
def update_preferences(changes: dict, confirm: bool = False) -> dict:
    """Preview preference changes; write only when confirmed."""
    if changes is None:
        return fail("invalid_argument", "changes must be an object.")
    return preferences_call(current_context(), changes, confirm)
