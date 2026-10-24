"""Forward-only store schema. Each version and its marker commit together."""


def _base_schema(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS serial (id INTEGER PRIMARY KEY AUTOINCREMENT)")
    connection.execute("CREATE TABLE IF NOT EXISTS recipes (slug TEXT PRIMARY KEY, id TEXT NOT NULL, payload TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS ingredients (recipe_slug TEXT REFERENCES recipes(slug) ON DELETE CASCADE, position INTEGER, originalText TEXT, payload TEXT NOT NULL, PRIMARY KEY(recipe_slug, position))")
    connection.execute("CREATE TABLE IF NOT EXISTS instructions (recipe_slug TEXT REFERENCES recipes(slug) ON DELETE CASCADE, position INTEGER, payload TEXT NOT NULL, PRIMARY KEY(recipe_slug, position))")
    connection.execute("CREATE TABLE IF NOT EXISTS tags (id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS comments (id TEXT PRIMARY KEY, recipe_slug TEXT NOT NULL REFERENCES recipes(slug), text TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS cooking_events (id TEXT PRIMARY KEY, recipe_slug TEXT NOT NULL REFERENCES recipes(slug), subject TEXT NOT NULL, message TEXT)")
    connection.execute("CREATE TABLE IF NOT EXISTS meal_entries (id TEXT PRIMARY KEY, recipe_slug TEXT REFERENCES recipes(slug), payload TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS shopping_lists (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS shopping_items (id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES shopping_lists(id) ON DELETE CASCADE, payload TEXT NOT NULL)")


def _search_schema(connection):
    connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS recipe_search USING fts5(slug UNINDEXED, name, description, ingredients, tokenize='unicode61 remove_diacritics 2')")
    connection.execute("INSERT INTO recipe_search(slug, name, description, ingredients) SELECT slug, json_extract(payload, '$.name'), json_extract(payload, '$.description'), COALESCE((SELECT group_concat(originalText, ' ') FROM ingredients WHERE recipe_slug=recipes.slug), '') FROM recipes WHERE slug NOT IN (SELECT slug FROM recipe_search)")


def _sort_schema(connection):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for table, column in (("recipes", "created_at"), ("recipes", "updated_at"),
                          ("cooking_events", "created_at")):
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
        connection.execute(f"UPDATE {table} SET {column}=?", (now,))


MIGRATIONS = ((1, _base_schema), (2, _search_schema), (3, _sort_schema))


def migrate(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, apply in MIGRATIONS:
        # Serialize constructors too: recheck the authoritative marker under lock.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM schema_version WHERE version=?", (version,)).fetchone():
                continue
            apply(connection)
            connection.execute("INSERT INTO schema_version VALUES (?, datetime('now'))", (version,))
