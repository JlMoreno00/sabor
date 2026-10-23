"""Synchronous store-neutral port; DTO field spelling preserves the public contract.

Frozen DTOs use tuples for collections. Adapters retain private transport/update
fields themselves; no raw provider response belongs in this port.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeVar
from sabor.core.envelope import ErrorCode, fail
from sabor.core import errors

T = TypeVar("T")


@dataclass(frozen=True)
class NamedRef:
    id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class RecipeSummary(NamedRef):
    slug: str | None = None


RecipeRef = RecipeSummary
Tag = NamedRef
ListRef = NamedRef


@dataclass(frozen=True)
class Ingredient:
    quantity: float | None = None
    unit: NamedRef | None = None
    food: NamedRef | None = None
    note: str | None = None
    display: str | None = None
    originalText: str | None = None
    referenceId: str | None = None
    title: str | None = None
    heading: str | None = None


@dataclass(frozen=True)
class Instruction:
    id: str | None = None
    title: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class Recipe(RecipeSummary):
    description: str | None = None
    recipeYield: str | None = None
    recipeServings: float | None = None
    totalTime: str | None = None
    prepTime: str | None = None
    cookTime: str | None = None
    tags: tuple[NamedRef, ...] | None = None
    recipeCategory: tuple[NamedRef, ...] | None = None
    recipeIngredient: tuple[Ingredient, ...] | None = None
    recipeInstructions: tuple[Instruction, ...] | None = None


RecipeDraft = Recipe


@dataclass(frozen=True)
class RecipePatch:
    # Presence distinguishes omission from explicit null; arrays replace entirely.
    changes: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "changes", MappingProxyType(dict(self.changes)))


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass(frozen=True)
class RecipeSearch:
    query: str = ""
    page: int = 1
    per_page: int = 25
    tag_ids: tuple[str, ...] | None = None
    category_ids: tuple[str, ...] | None = None
    food_ids: tuple[str, ...] | None = None
    tool_ids: tuple[str, ...] | None = None
    cookbook_id: str | None = None
    require_all: bool = False
    sort: str = "name"
    direction: str = "asc"


@dataclass(frozen=True)
class SuggestionQuery:
    foods: tuple[str, ...]
    tools: tuple[str, ...] | None = None
    max_missing_foods: int = 2
    max_missing_tools: int = 0
    limit: int = 10
    include_foods_on_hand: bool = True


@dataclass(frozen=True)
class Suggestion(RecipeSummary):
    missingFoods: tuple[NamedRef, ...] | None = None
    missingTools: tuple[NamedRef, ...] | None = None


@dataclass(frozen=True)
class DateRange:
    start_date: str
    end_date: str


@dataclass(frozen=True)
class MealEntryDraft:
    date: str
    entry_type: str
    title: str
    recipe_slug: str | None = None


@dataclass(frozen=True)
class MealEntry(MealEntryDraft):
    id: str = ""
    recipe_id: str | None = None


@dataclass(frozen=True)
class ListSummary(NamedRef):
    items_count: int = 0


@dataclass(frozen=True)
class ItemDraft:
    note: str | None = None
    display: str | None = None
    quantity: float | None = None
    food: NamedRef | None = None
    unit: NamedRef | None = None


@dataclass(frozen=True)
class ShoppingItem(ItemDraft):
    id: str = ""
    list_id: str = ""
    checked: bool | None = False
    recipe_references: tuple["RecipeOccurrence", ...] | None = None


@dataclass(frozen=True)
class ShoppingSnapshot:
    list_id: str
    items: tuple[ShoppingItem, ...]


@dataclass(frozen=True)
class RecipeOccurrence:
    slug: str
    multiplier: float = 1.0


@dataclass(frozen=True)
class WriteResult:
    changed_ids: tuple[str, ...] = ()


class StoreError(Exception):
    """Closed core error vocabulary; never accepts upstream exception messages."""
    def __init__(self, code: str, provider: str):
        self.code = ErrorCode(code).value
        self.provider = provider
        super().__init__(self.code)


def store_error_envelope(error: StoreError) -> dict:
    return fail(error.code, errors._MESSAGES.get(error.code,
                "Recipe store operation failed."), error.provider)


class RecipeStore(Protocol):
    id: str
    operations: frozenset[str]
    def search_recipes(self, filters: RecipeSearch) -> Page[RecipeSummary]: ...
    def get_recipe(self, slug: str) -> Recipe: ...
    def create_recipe(self, draft: RecipeDraft) -> RecipeRef: ...
    def update_recipe(self, slug: str, patch: RecipePatch) -> RecipeRef: ...
    def suggest_recipes(self, query: SuggestionQuery) -> list[Suggestion]: ...
    def get_mealplan(self, dates: DateRange) -> list[MealEntry]: ...
    def set_mealplan_entry(self, entry: MealEntryDraft) -> MealEntry: ...
    def delete_mealplan_entry(self, entry_id: str) -> None: ...
    def get_shopping_lists(self) -> list[ListSummary]: ...
    def create_shopping_list(self, name: str) -> ListRef: ...
    def delete_shopping_list(self, list_id: str) -> None: ...
    def shopping_list_preview(self, list_id: str) -> dict: ...
    def clear_shopping_list_items(self, list_id: str) -> dict: ...
    def validate_shopping_list_id(
        self, list_id: str, *, adding: bool = False
    ) -> str: ...
    def validate_recipe_id(self, recipe_id: str) -> str: ...
    def get_shopping_items(self, list_id: str) -> ShoppingSnapshot: ...
    def add_shopping_items(self, list_id: str, items: list[ItemDraft]) -> WriteResult: ...
    def add_recipe_occurrences(self, list_id: str, occurrences: list[RecipeOccurrence]) -> WriteResult: ...
    def check_shopping_items(self, list_id: str, ids: list[str], checked: bool) -> WriteResult: ...
    def remove_shopping_items(self, list_id: str, ids: list[str]) -> WriteResult: ...
    def get_or_create_tag(self, name: str) -> Tag: ...
    def tag_recipe(self, slug: str, tags: list[str]) -> WriteResult: ...
    def add_recipe_comment(self, slug: str, text: str) -> str: ...
    def log_cooking_event(self, slug: str, subject: str, message: str | None) -> str: ...
