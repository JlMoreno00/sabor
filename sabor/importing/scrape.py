"""Offline recipe extraction from already safely fetched, untrusted HTML."""
import json
from html.parser import HTMLParser

import recipe_scrapers


def recipes(value):
    if isinstance(value, list):
        for item in value:
            yield from recipes(item)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if types == 'Recipe' or isinstance(types, list) and 'Recipe' in types:
            yield value
        yield from recipes(value.get('@graph', []))


class LinkedData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = (dict(attrs).get('type') or '').lower() == 'application/ld+json'
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.documents.append(''.join(self.parts))
            self.active = False


def valid(data):
    from .tools import plain_steps
    try:
        return (isinstance(data.get('name'), str) and bool(data['name'].strip())
            and isinstance(data.get('recipeIngredient'), list) and bool(data['recipeIngredient'])
            and all(isinstance(x, str) for x in data['recipeIngredient'])
            and bool(plain_steps(data.get('recipeInstructions', []))))
    except (ValueError, TypeError):
        return False


def clean_steps(value):
    """Remove blank strings and step text recursively, preserving section metadata."""
    if isinstance(value, list):
        return [clean_steps(item) for item in value
                if not (isinstance(item, str) and not item.strip())
                and not (isinstance(item, dict) and item.get('@type') != 'HowToSection'
                         and isinstance(item.get('text'), str) and not item['text'].strip())]
    if isinstance(value, dict) and value.get('@type') == 'HowToSection':
        return {**value, 'itemListElement': clean_steps(value.get('itemListElement', []))}
    return value


def clean(data):
    """Drop blank ingredient/step lines and keep a textual recipeYield (or None)."""
    data = dict(data)
    if isinstance(data.get('recipeIngredient'), list):
        # Non-text values are kept so valid() rejects the recipe and the next one is tried.
        data['recipeIngredient'] = [x.strip() if isinstance(x, str) else x for x in data['recipeIngredient']
                                    if not isinstance(x, str) or x.strip()]
    if isinstance(data.get('recipeInstructions'), list):
        data['recipeInstructions'] = clean_steps(data['recipeInstructions'])
    value = data.get('recipeYield')
    if isinstance(value, list):
        value = next((x for x in value if isinstance(x, (str, int, float))
                      and not isinstance(x, bool) and str(x).strip()), None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f'{value:g}'
    data['recipeYield'] = value.strip() if isinstance(value, str) and value.strip() else None
    return data


def recipe_from_html(html, url):
    """Try recipe-scrapers without network access, then schema.org JSON-LD."""
    text = html.decode('utf-8', errors='replace')
    try:
        scraper = recipe_scrapers.scrape_html(text, org_url=url, supported_only=False)
        data = {}
        for field, method in [('name', 'title'), ('recipeIngredient', 'ingredients'),
                ('recipeInstructions', 'instructions_list'), ('recipeYield', 'yields'),
                ('totalTime', 'total_time'), ('prepTime', 'prep_time'),
                ('cookTime', 'cook_time'), ('image', 'image')]:
            try:
                data[field] = getattr(scraper, method)()
            except Exception:
                data[field] = None
        data = clean(data)
        if valid(data):
            return data
    except Exception:
        pass
    parser = LinkedData()
    parser.feed(text)
    for document in parser.documents:
        try:
            for data in recipes(json.loads(document)):
                data = clean(data)
                if valid(data):
                    return data
        except (ValueError, TypeError):
            continue
    return None
