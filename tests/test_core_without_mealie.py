"""Core acceptance through a real server and an instance-owned non-Mealie store."""

import ast
from unittest.mock import Mock, patch
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from sabor.server import register_tools
from sabor.store.fake import FakeRecipeStore


def test_all_core_tools_work_without_mealie():
    store = FakeRecipeStore()
    server = FastMCP("store-only")
    register_tools(
        server,
        {"SABOR_TOOLSETS": "core", "SABOR_NUTRITION_ENABLED": "true"},
        store_factory=lambda settings: store,
    )
    tools = {tool.name: tool.fn for tool in server._tool_manager.list_tools()}
    called = set()

    def call(tool_name, **kwargs):
        called.add(tool_name)
        result = tools[tool_name](**kwargs)
        assert result.get("ok", True), (tool_name, result)
        return result

    capabilities = tools["sabor_capabilities"]()["data"]
    assert capabilities["mealie"]["configured"] is False
    assert capabilities["capabilities"]["recipe_store"]["configured"] is True
    assert len(tools) == 23
    call("create_recipe", name="Soup", ingredients=["salt"], instructions=["Cook"])
    slug = next(iter(store.recipes))
    assert store.get_recipe(slug).recipeIngredient[0].originalText == "salt"
    assert store.get_recipe(slug).recipeInstructions[0].text == "Cook"
    call("get_recipe", slug=slug)
    call("search_recipes", query="Soup")
    call("update_recipe", slug=slug, patch={"description": "Good"}, confirm=True)
    call("suggest_recipes", foods=[])
    call("get_or_create_tag", name="Dinner")
    call("tag_recipe", slug=slug, tags=["Dinner"])
    call("add_recipe_comment", slug=slug, text="Good")
    call("log_cooking_event", slug=slug, subject="Cooked", message="Today")
    entry = call(
        "set_mealplan_entry",
        date_str="2026-01-01",
        entry_type="dinner",
        title="Soup",
        recipe_slug=slug,
    )
    call("get_mealplan_week", start_date="2026-01-01", end_date="2026-01-07")
    call("delete_mealplan_entry", entry_id=entry["id"])
    call(
        "clear_mealplan_week",
        start_date="2026-01-01",
        end_date="2026-01-07",
        confirm=True,
    )
    target = call("create_shopping_list", name="Week")["id"]
    call("get_shopping_lists")
    call("get_or_create_shopping_list", name="Week")
    call("add_to_shopping_list", list_id=target, items=["salt"])
    call("add_to_shopping_list", list_id=target, recipe_slugs=[slug])
    call("get_shopping_list_items", list_id=target)
    item = store.get_shopping_items(target).items[0].id
    call("check_shopping_list_items", list_id=target, item_ids=[item])
    call("remove_shopping_list_items", list_id=target, item_ids=[item], confirm=True)
    call("clear_shopping_list", list_id=target)
    call("clear_shopping_list", list_id=target, confirm=True)
    call("delete_shopping_list", list_id=target, confirm=True)
    assert called == set(tools) - {"sabor_capabilities"}
    assert store.comments and store.cooking_events
    assert store.get_recipe(slug).tags


@pytest.mark.parametrize(
    "module", ["recipes", "mealplan", "shopping", "shopping_items", "tags", "comments"]
)
def test_core_does_not_import_requests(module):
    tree = ast.parse(
        (Path(__file__).parents[1] / "sabor" / "tools" / f"{module}.py").read_text()
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(
                alias.name != "requests" and not alias.name.startswith("requests.")
                for alias in node.names
            )
        if isinstance(node, ast.ImportFrom):
            assert node.module != "requests" and not (node.module or "").startswith(
                "requests."
            )


def test_injected_store_does_not_enable_mealie_catalog():
    server = FastMCP("no-catalog")
    register_tools(
        server,
        {"SABOR_TOOLSETS": "core,legacy_catalog,import"},
        store_factory=lambda settings: FakeRecipeStore(),
    )
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert "import_recipe" in names
    assert "spanish_catalog_status" not in names
    assert "apply_spanish_catalog" not in names


@pytest.mark.parametrize(
    "recipe_id", ["ABCDEFAB-1234-1234-1234-123456789ABC", "invalid"]
)
def test_mealie_shopping_recipe_id_policy_is_preserved(recipe_id):
    server = FastMCP("mealie-id-policy")
    register_tools(
        server,
        {
            "SABOR_TOOLSETS": "core",
            "MEALIE_BASE_URL": "https://mealie.example",
            "MEALIE_API_TOKEN": "test",
        },
    )
    tool = server._tool_manager.get_tool("add_to_shopping_list")
    assert tool is not None
    response = Mock(status_code=200, json=lambda: {"id": recipe_id, "slug": "soup"})
    with (
        patch("requests.Session.get", return_value=response),
        patch("requests.Session.post", return_value=Mock(status_code=200)) as post,
    ):
        result = tool.fn("12345678-1234-1234-1234-123456789abc", recipe_slugs=["soup"])
    if recipe_id == "invalid":
        assert result["error"]["code"] == "invalid_response"
        post.assert_not_called()
    else:
        assert result["ok"]
        assert result["data"]["recipes"][0]["id"] == recipe_id.lower()


def test_missing_store_still_disables_core():
    server = FastMCP("no-store")
    register_tools(server, {"SABOR_TOOLSETS": "core"})
    assert {tool.name for tool in server._tool_manager.list_tools()} == {
        "sabor_capabilities"
    }
