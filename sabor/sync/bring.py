"""Bring transport and read-only mapping; no ingredient parser writes."""
from typing import Any, Mapping, Literal
from urllib.parse import quote

from sabor.integrations import bring
from sabor.core import errors
from sabor.core.envelope import ok


class BringSyncProvider:
    id = 'bring'

    def __init__(self, settings=None):
        pass  # Credentials and refresh remain owned by the existing transport.

    def targets(self) -> list[Mapping[str, Any]]:
        bring._bring_ensure_auth()
        rows = bring._bring_req('GET', f"/bringusers/{bring._bring_auth['uuid']}/lists").json()['lists']
        return [{'provider': self.id, 'list_id': row['listUuid'], 'name': row['name']} for row in rows]

    def plan(self, source: Mapping[str, Any], target: Mapping[str, Any],
             mode: Literal['append', 'replace']) -> Mapping[str, Any]:
        path = f"/v2/bringlists/{quote(target['list_id'], safe='')}"
        purchase = bring._bring_req('GET', path).json()['purchase']
        current = {(row.get('itemId') or row['name']).casefold(): row for row in purchase}
        mapped, skipped, warnings, conflicts = {}, [], [], []
        for row in source['items']:
            if row.get('checked'):
                skipped.append({'source_id': row['id'], 'reason': 'checked'})
                continue
            raw = row.get('display') or row.get('note') or ''
            food = (row.get('food') or {}).get('name')
            name = bring._clean_food_name(food or raw)
            if not name:
                raise ValueError('Missing source representation')
            item_id = bring._catalog_match(name) or name
            qty = row.get('quantity')
            qty_str = (str(int(qty)) if qty == int(qty) else str(round(qty, 3))) if qty else ''
            spec = ' '.join(filter(None, [qty_str, (row.get('unit') or {}).get('name')])).strip()
            if not food:
                spec = raw  # Unstructured notes retain quantities verbatim.
            elif row.get('note'):
                spec = '; '.join(filter(None, [spec, row['note']]))
            spec = spec or raw or name
            key = item_id.casefold()
            if key in mapped:
                warnings.append({'code': 'lossy_collision', 'source_ids': mapped[key]['source_ids'] + [row['id']]})
                mapped[key]['spec'] += ' + ' + spec
                mapped[key]['source_ids'].append(row['id'])
            else:
                mapped[key] = {'itemId': item_id, 'spec': spec, 'source_ids': [row['id']]}
        additions = []
        for key, item in mapped.items():
            existing = current.get(key) if mode == 'append' else None
            if existing is not None:
                if existing.get('spec', '') == item['spec']:
                    skipped.append({**item, 'reason': 'identical'})
                else:
                    conflicts.append({**item, 'existing_spec': existing.get('spec', '')})
            else:
                additions.append({**item, 'operation': 'TO_PURCHASE',
                                  **dict.fromkeys(('accuracy', 'altitude', 'latitude', 'longitude'), '0.0')})
        removals = [{'itemId': row.get('itemId') or row['name'], 'spec': '', 'operation': 'TO_RECENTLY'}
                    for row in purchase] if mode == 'replace' else []
        return {'source': source, 'target': target, 'mode': mode, 'add': additions, 'remove': removals,
                'skipped': skipped, 'conflicts': conflicts, 'warnings': warnings,
                'counts': {'add': len(additions), 'remove': len(removals), 'skipped': len(skipped)},
                'confirmation': 'Confirmation applies to the current snapshot; no cross-system transaction.'}

    def apply(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        completed = []
        actions = plan['remove'] + plan['add']
        for index, action in enumerate(actions):
            change = {k: v for k, v in action.items() if k != 'source_ids'}
            try:
                bring._bring_req('PUT', f"/v2/bringlists/{quote(plan['target']['list_id'], safe='')}/items",
                                 json={'changes': [change], 'sender': 'Sabor'})
                completed.append(change)
            except Exception:
                return errors.partial(completed, [{'action': x, 'state': 'uncertain' if i == 0 else 'not_attempted'}
                    for i, x in enumerate(actions[index:])], self.id)
        return ok({**plan, 'preview': False, 'completed': completed})
