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
        if valid(data):
            return data
    except Exception:
        pass
    parser = LinkedData()
    parser.feed(text)
    for document in parser.documents:
        try:
            for data in recipes(json.loads(document)):
                if valid(data):
                    return data
        except (ValueError, TypeError):
            continue
    return None
