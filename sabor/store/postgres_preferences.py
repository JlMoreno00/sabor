"""Private transactional preferences adapter using the recipe store connection."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from sabor.preferences import FIELDS, LIST_FIELDS, _read, empty_preferences, validate


class PostgresPreferences:
    source = "postgres"

    def __init__(self, connection, schema):
        self.connection = connection
        self.schema = schema

    def lock(self):
        # Also serializes first writes, when no row yet exists to lock.
        key = int.from_bytes(
            sha256(("sabor:preferences:" + self.schema).encode()).digest()[:8],
            "big",
            signed=True,
        )
        self.connection.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

    def read(self):
        from psycopg import sql
        from psycopg import Error

        try:
            row = self.connection.execute(
                sql.SQL("SELECT payload, updated_at FROM {} WHERE profile=%s").format(
                    sql.Identifier(self.schema, "preferences")
                ),
                ("default",),
            ).fetchone()
        except Error:
            raise ValueError("Unable to read preferences safely.") from None
        if row is None:
            return empty_preferences(), None, "defaults"
        payload, updated = row
        if isinstance(payload, str):
            payload = json.loads(payload)
        if (
            not isinstance(payload, dict)
            or set(payload) != set(FIELDS)
            or not isinstance(updated, str)
        ):
            raise ValueError
        return (
            {key: validate(key, payload[key]) for key in FIELDS},
            updated,
            self.source,
        )

    def write(self, preferences):
        from psycopg import sql

        updated = datetime.now(timezone.utc).isoformat()
        from psycopg import Error

        try:
            self.connection.execute(
                sql.SQL(
                    "INSERT INTO {} (profile, payload, updated_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (profile) DO UPDATE SET payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at"
                ).format(sql.Identifier(self.schema, "preferences")),
                ("default", json.dumps(preferences), updated),
            )
        except Error:
            raise OSError("Unable to write preferences safely.") from None
        return updated


def import_changes(context, backend):
    path = Path(context.settings.legacy_config.SABOR_DATA_DIR) / "preferences.json"
    local, _, source = _read(path)
    if source != "file":
        raise ValueError
    current, _, _ = backend.read()
    changes = {"add": {key: local[key] for key in LIST_FIELDS}}
    if current["household_size"] is None:
        changes["set"] = {"household_size": local["household_size"]}
    return changes
