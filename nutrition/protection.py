"""Pure recipe publication-protection contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def nutrition_publication_protected(recipe: Mapping[str, Any] | None) -> bool:
    """Return whether publication must stop at the recipe boundary.

    Only the explicit boolean ``True`` enables a valid protection flag. A
    present, non-mapping extras or settings value blocks conservatively because
    its protection flag cannot be inspected safely.
    """

    if recipe is None:
        return False
    if not isinstance(recipe, Mapping):
        return True
    for field in ("extras", "settings"):
        container = recipe.get(field)
        if container is None:
            continue
        if not isinstance(container, Mapping):
            return True
        if container.get("saborNutritionProtected") is True:
            return True
    return False
