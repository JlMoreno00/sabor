"""SQLite durability, migrations and conservative ingredient aggregation."""
import sqlite3
from unittest.mock import patch

import pytest

from sabor.store import (Recipe, Ingredient, RecipeOccurrence, RecipeSearch,
                         RecipePatch, SuggestionQuery, NamedRef, StoreError,
                         MealEntryDraft, DateRange, ItemDraft, Instruction)
from sabor.store.sqlite import SqliteRecipeStore
from sabor.store.sqlite_schema import MIGRATIONS


def test_migrations_and_persistence(tmp_path):
    path = tmp_path / "nested" / "store.db"
    store = SqliteRecipeStore(path)
    ref = store.create_recipe(Recipe(name="Soup", recipeIngredient=(
        Ingredient(originalText="salt"),), recipeInstructions=(Instruction(text="Cook"),)))
    tag = store.get_or_create_tag("Dinner")
    store.tag_recipe(ref.slug, ["Dinner"])
    comment = store.add_recipe_comment(ref.slug, "Good")
    event = store.log_cooking_event(ref.slug, "Cooked", None)
    store.set_mealplan_entry(MealEntryDraft("2026-01-01", "dinner", "Soup", ref.slug))
    target = store.create_shopping_list("Week").id
    item = store.add_shopping_items(target, [ItemDraft(note="salt")]).changed_ids[0]
    store.check_shopping_items(target, [item], True)
    reopened = SqliteRecipeStore(path)
    recipe = reopened.get_recipe(ref.slug)
    assert recipe.name == "Soup"
    assert recipe.tags is not None
    assert recipe.recipeIngredient is not None
    assert recipe.recipeInstructions is not None
    assert recipe.tags[0].id == tag.id
    assert recipe.recipeIngredient[0].originalText == "salt"
    assert recipe.recipeInstructions[0].text == "Cook"
    assert reopened.get_shopping_items(target).items[0].checked is True
    assert len(reopened.get_mealplan(DateRange("2026-01-01", "2026-01-01"))) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT text FROM comments WHERE id=?", (comment,)).fetchone() == ("Good",)
        assert db.execute("SELECT message FROM cooking_events WHERE id=?", (event,)).fetchone() == (None,)
        assert db.execute("SELECT version FROM schema_version ORDER BY version").fetchall() == [
            (version,) for version, _ in MIGRATIONS]
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    other = SqliteRecipeStore(tmp_path / "other.db")
    assert other.search_recipes(RecipeSearch()).total == 0
    assert other.get_shopping_lists() == []


def test_interrupted_migration(tmp_path):
    path = tmp_path / "store.db"
    with patch("sabor.store.sqlite_schema.MIGRATIONS", MIGRATIONS[:1]):
        store = SqliteRecipeStore(path)
        ref = store.create_recipe(Recipe(name="Keep"))
    def interrupted(db):
        MIGRATIONS[1][1](db)
        raise sqlite3.OperationalError("Simulated interruption after DDL")

    with patch("sabor.store.sqlite_schema.MIGRATIONS", (MIGRATIONS[0], (2, interrupted))):
        with pytest.raises(sqlite3.OperationalError):
            SqliteRecipeStore(path).ensure_ready()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='recipe_search'").fetchall()
    reopened = SqliteRecipeStore(path)
    assert reopened.get_recipe(ref.slug).name == "Keep"
    assert reopened.search_recipes(RecipeSearch(query="Keep")).total == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT max(version) FROM schema_version").fetchone()[0] == MIGRATIONS[-1][0]


@pytest.mark.parametrize("lines,count,amount", [
    (["200 g harina", "0,5 kg harina"], 1, 700),
    (["2 huevos", "100 g huevo"], 2, None),
    (["100 g leche", "100 ml leche"], 2, None),
    (["sal al gusto", "sal al gusto"], 2, None),
    (["1-2 kg harina", "1-2 kg harina"], 2, None),
])
def test_aggregation(tmp_path, lines, count, amount):
    store = SqliteRecipeStore(tmp_path / "store.db")
    target = store.create_shopping_list("Week").id
    refs = [store.create_recipe(Recipe(name=str(n), recipeIngredient=(
        Ingredient(originalText=line),))) for n, line in enumerate(lines)]
    for ref in refs:
        assert ref.slug is not None
        store.add_recipe_occurrences(target, [RecipeOccurrence(ref.slug)])
    items = SqliteRecipeStore(store.path).get_shopping_items(target).items
    assert len(items) == count
    if amount:
        assert items[0].quantity == amount
        assert items[0].display == "700 g harina"
        assert items[0].unit is not None
        assert items[0].recipe_references is not None
        assert items[0].note is not None
        assert items[0].unit.name == "g"
        assert len(items[0].recipe_references) == 2
        assert all(line in items[0].note for line in lines)


def test_search_and_suggestions(tmp_path):
    store = SqliteRecipeStore(tmp_path / "store.db")
    ref = store.create_recipe(Recipe(name='Sopa "especial"', description="Caliente",
        recipeIngredient=(Ingredient(originalText="200 g maíz", food=NamedRef(name="Maíz")),)))
    for query in ('Sopa "especial"', "caliente", "maíz"):
        assert store.search_recipes(RecipeSearch(query=query)).items[0].slug == ref.slug
    assert len(store.suggest_recipes(SuggestionQuery(("MAIZ",), max_missing_foods=0))) == 1
    assert store.suggest_recipes(SuggestionQuery((), max_missing_foods=0)) == []
    store.update_recipe(ref.slug, RecipePatch({"name": "Nuevo", "description": None,
                                              "recipeIngredient": ()}))
    assert store.search_recipes(RecipeSearch(query="caliente")).total == 0
    assert store.search_recipes(RecipeSearch(query="maíz")).total == 0
    assert store.search_recipes(RecipeSearch(query="Nuevo")).total == 1


def test_atomic_occurrences_and_multiplier(tmp_path):
    store = SqliteRecipeStore(tmp_path / "store.db")
    ref = store.create_recipe(Recipe(name="Flour", recipeIngredient=(Ingredient(originalText="200 g harina"),)))
    target = store.create_shopping_list("Week").id
    assert ref.slug is not None
    for invalid in (0, 21, float("nan"), float("inf")):
        with pytest.raises(StoreError) as caught:
            store.add_recipe_occurrences(target, [RecipeOccurrence(ref.slug), RecipeOccurrence(ref.slug, invalid)])
        assert caught.value.code == "invalid_argument"
        assert store.get_shopping_items(target).items == ()
    store.add_recipe_occurrences(target, [RecipeOccurrence(ref.slug, 2)])
    assert store.get_shopping_items(target).items[0].quantity == 400


def test_duplicate_search_across_pages(tmp_path):
    from sabor.importing.tools import store_duplicate
    store = SqliteRecipeStore(tmp_path / "store.db")
    for n in range(101):
        store.create_recipe(Recipe(name=f"A {n:03}", description="Soup"))
    exact = store.create_recipe(Recipe(name="Soup"))
    assert store_duplicate("SOUP", store) == exact.slug
    assert store.search_recipes(RecipeSearch(query="Soup", page=2, per_page=100)).total == 102


def test_normalize_imported(tmp_path):
    store = SqliteRecipeStore(tmp_path / "normalize.db")
    original = (
        Ingredient(originalText=" 2 dientes de ajo ", note="picado", referenceId="keep"),
        Ingredient(originalText="sal al gusto", note="sal al gusto"),
        Ingredient(originalText="2 huevos", quantity=7, food=NamedRef(name="custom")),
        Ingredient(originalText="200 g de harina de trigo"),
    )
    ref = store.create_recipe(Recipe(name="Import", recipeIngredient=original))
    result = store.normalize_imported(ref.slug)
    assert result == {"ok": True, "slug": ref.slug, "status": "normalized", "accepted": 2}
    rows = store.get_recipe(ref.slug).recipeIngredient
    assert [row.originalText for row in rows] == [row.originalText for row in original]
    assert rows[0].quantity == 2
    assert rows[0].unit == NamedRef(name="diente")
    assert rows[0].food == NamedRef(name="ajo")
    assert rows[0].note == "picado"
    assert rows[0].referenceId == "keep"
    assert rows[1:3] == original[1:3]
    assert rows[3].food == NamedRef(name="harina de trigo")
    assert store.normalize_imported(ref.slug) == {
        "ok": True, "slug": ref.slug, "status": "retained_originals", "accepted": 0}
    assert SqliteRecipeStore(store.path).get_recipe(ref.slug).recipeIngredient == rows


def test_normalize_empty_and_missing(tmp_path):
    store = SqliteRecipeStore(tmp_path / "normalize.db")
    ref = store.create_recipe(Recipe(name="Empty"))
    assert store.normalize_imported(ref.slug) == {
        "ok": True, "slug": ref.slug, "status": "skipped_no_ingredients"}
    with pytest.raises(StoreError) as caught:
        store.normalize_imported("missing")
    assert caught.value.code == "not_found"
