"""Validated, instance-owned household preferences persistence."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from sabor.core.envelope import fail, ok

LIST_FIELDS = ('diet', 'dislikes', 'pantry', 'notes')
FIELDS = (*LIST_FIELDS, 'household_size')


def empty_preferences() -> dict[str, Any]:
    return {**{key: [] for key in LIST_FIELDS}, 'household_size': None}


def validate(field, value) -> Any:
    if field == 'household_size':
        if value is None or type(value) is int and 1 <= value <= 20:
            return value
        raise ValueError
    if field not in LIST_FIELDS or not isinstance(value, list) or len(value) > 200:
        raise ValueError
    result, seen = [], set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item.strip()) > 200:
            raise ValueError
        item = item.strip()
        if item.casefold() not in seen:
            result.append(item)
            seen.add(item.casefold())
    return result


def _read(path):
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return empty_preferences(), None, 'defaults'
    try:
        document = json.loads(raw)
    except ValueError:
        raise ValueError('Invalid preferences JSON') from None
    if (type(document.get('schema_version')) is not int or document['schema_version'] != 1
            or not isinstance(document.get('profiles'), dict)):
        raise ValueError
    profile = document['profiles']['default']
    if set(profile) != set(FIELDS):
        raise ValueError
    preferences = {key: validate(key, profile[key]) for key in FIELDS}
    updated = document['updated_at']
    if not isinstance(updated, str):
        raise ValueError
    return preferences, updated, 'file'


def _write(path, preferences):
    updated = datetime.now(timezone.utc).isoformat()
    document = {'schema_version': 1, 'profiles': {'default': preferences}, 'updated_at': updated}
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.preferences-', delete=False) as stream:
        temporary = stream.name
        json.dump(document, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return updated


def preferences_call(context, changes=None, confirm=False):
    path = Path(context.settings.legacy_config.SABOR_DATA_DIR) / 'preferences.json'
    if type(confirm) is not bool:
        return fail('invalid_argument', 'confirm must be a boolean.')
    with context.state.preferences_lock:
        try:
            before, updated, source = _read(path)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return fail('invalid_response', 'Unable to read preferences safely.')
        data = {'preferences': before, 'updated_at': updated, 'source': source}
        if changes is None:
            return ok(data)
        try:
            if not isinstance(changes, dict) or set(changes) - {'set', 'add', 'remove'}:
                raise ValueError
            after = deepcopy(before)
            for operation in ('set', 'add', 'remove'):
                values = changes.get(operation, {})
                if not isinstance(values, dict):
                    raise ValueError
                for field, value in values.items():
                    if field not in FIELDS or operation != 'set' and field not in LIST_FIELDS:
                        raise ValueError
                    value = validate(field, value)
                    if operation == 'set':
                        after[field] = value
                    elif operation == 'add':
                        seen = {item.casefold() for item in after[field]}
                        added = [item for item in value if item.casefold() not in seen]
                        after[field] = validate(field, after[field] + added)
                    else:
                        removed = {item.casefold() for item in value}
                        after[field] = [item for item in after[field] if item.casefold() not in removed]
        except (ValueError, TypeError):
            return fail('invalid_argument', 'Invalid preference changes.')
        diff = {key: {'before': before[key], 'after': after[key]} for key in FIELDS
                if before[key] != after[key]}
        if confirm:
            try:
                updated = _write(path, after)
            except (OSError, ValueError):
                return fail('upstream_error', 'Unable to write preferences safely.')
        return ok({'preferences': after, 'updated_at': updated,
                   'source': 'file' if confirm else source, 'diff': diff, 'preview': not confirm})
