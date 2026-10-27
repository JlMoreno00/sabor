"""Forward-only Postgres schema migrations, serialized per schema."""
from datetime import datetime, timezone
from hashlib import sha256
from typing import LiteralString


_TABLES: tuple[tuple[str, LiteralString], ...] = (
    ('serial', 'id bigserial PRIMARY KEY'),
    ('recipes', 'slug text PRIMARY KEY, id text NOT NULL, payload jsonb NOT NULL, seq bigserial, '
     'search_text text NOT NULL DEFAULT \'\', created_at text, updated_at text'),
    ('ingredients', 'recipe_slug text REFERENCES recipes(slug) ON DELETE CASCADE, position integer, '
     'originalText text, payload jsonb NOT NULL, PRIMARY KEY(recipe_slug, position)'),
    ('instructions', 'recipe_slug text REFERENCES recipes(slug) ON DELETE CASCADE, position integer, '
     'payload jsonb NOT NULL, PRIMARY KEY(recipe_slug, position)'),
    ('tags', 'id text PRIMARY KEY, name text UNIQUE NOT NULL, payload jsonb NOT NULL'),
    ('comments', 'id text PRIMARY KEY, recipe_slug text NOT NULL REFERENCES recipes(slug), text text NOT NULL'),
    ('cooking_events', 'id text PRIMARY KEY, recipe_slug text NOT NULL REFERENCES recipes(slug), '
     'subject text NOT NULL, message text, created_at text'),
    ('meal_entries', 'id text PRIMARY KEY, recipe_slug text REFERENCES recipes(slug), payload jsonb NOT NULL'),
    ('shopping_lists', 'id text PRIMARY KEY, name text NOT NULL'),
    ('shopping_items', 'id text PRIMARY KEY, list_id text NOT NULL REFERENCES shopping_lists(id) '
     'ON DELETE CASCADE, payload jsonb NOT NULL'),
)


def _base_schema(connection, schema):
    from psycopg import sql
    for table, columns in _TABLES:
        connection.execute(sql.SQL('CREATE TABLE {} ({})').format(sql.Identifier(table), sql.SQL(columns)))
    for table in ('schema_version', *(name for name, _ in _TABLES)):
        connection.execute(sql.SQL('ALTER TABLE {} ENABLE ROW LEVEL SECURITY').format(sql.Identifier(table)))
    connection.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(schema)))
    for role in ('anon', 'authenticated'):
        if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
            connection.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(
                sql.Identifier(schema), sql.Identifier(role)))
            connection.execute(sql.SQL('REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}').format(
                sql.Identifier(schema), sql.Identifier(role)))
            connection.execute(sql.SQL('REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}').format(
                sql.Identifier(schema), sql.Identifier(role)))


def _preferences_schema(connection, schema):
    from psycopg import sql
    connection.execute('CREATE TABLE preferences (profile text PRIMARY KEY, payload jsonb NOT NULL, '
                       'updated_at text NOT NULL)')
    connection.execute('ALTER TABLE preferences ENABLE ROW LEVEL SECURITY')
    connection.execute('REVOKE ALL ON preferences FROM PUBLIC')
    for role in ('anon', 'authenticated'):
        if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
            connection.execute(sql.SQL('REVOKE ALL ON {} FROM {}').format(
                sql.Identifier(schema, 'preferences'), sql.Identifier(role)))


MIGRATIONS = ((1, _base_schema), (2, _preferences_schema))


def migrate(connection, schema):
    from psycopg import sql
    lock = int.from_bytes(sha256(('sabor:' + schema).encode()).digest()[:8], 'big', signed=True)
    for version, apply in MIGRATIONS:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (lock,))
            # CREATE SCHEMA checks the database CREATE privilege even with IF NOT EXISTS, and
            # sabor_app loses it once its schema exists; the advisory lock serializes this check.
            if not connection.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (schema,)).fetchone():
                connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            connection.execute('CREATE TABLE IF NOT EXISTS schema_version '
                               '(version integer PRIMARY KEY, applied_at text NOT NULL)')
            if connection.execute('SELECT 1 FROM schema_version WHERE version=%s', (version,)).fetchone():
                continue
            apply(connection, schema)
            connection.execute('INSERT INTO schema_version VALUES (%s, %s)',
                               (version, datetime.now(timezone.utc).isoformat()))
