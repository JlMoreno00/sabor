"""Name-derived recipe slugs shared by the built-in stores (#49)."""
import re
import unicodedata


def slugify(name):
    """Lowercase ASCII words joined by hyphens ('Tortilla de patatas' -> 'tortilla-de-patatas')."""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def next_slug(name, taken, new_id):
    """First free slug for a new recipe: the name slug, then -2, -3...

    Without a usable name the numeric identifier from `new_id()` is kept, skipping
    any value a numeric name already took. Existing recipes are never rewritten.
    """
    base = slugify(name)
    if not base:
        slug = new_id()
        while taken(slug):
            slug = new_id()
        return slug
    slug, suffix = base, 2
    while taken(slug):
        slug, suffix = f"{base}-{suffix}", suffix + 1
    return slug
