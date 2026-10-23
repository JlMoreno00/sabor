"""HTTP-level baseline for #36: exact current responses, not a RED suite."""

from unittest.mock import Mock

import pytest

from sabor.server import register_tools

BASE = "https://mealie.example"
HEADERS = {"Authorization": "Bearer baseline"}
LIST = "12345678-1234-1234-1234-123456789abc"
TAG = {"id": "new", "name": "Vídeo", "slug": "video"}
OLD = {"id": "old", "name": "Old", "slug": "old"}


class Server:
    def __init__(self):
        self.tools = {}

    def add_tool(self, fn):
        self.tools[fn.__name__] = fn


@pytest.fixture
def server():
    result = Server()
    register_tools(
        result,
        {
            "MEALIE_BASE_URL": BASE,
            "MEALIE_API_TOKEN": "baseline",
            "SABOR_TOOLSETS": "core,import,shopping_sync",
            "GEMINI_API_KEY": "gemini",
            "SABOR_NORMALIZE_IMPORTS": "false",
            "BRING_EMAIL": "baseline@example.com",
            "BRING_PASSWORD": "test-password",
            "SABOR_NUTRITION_ENABLED": "false",
        },
    )
    return result


@pytest.fixture
def wire(monkeypatch):
    """Queue exact transport contracts; reject extra calls, including real network."""
    pending = []
    seen = []
    transport_headers = dict(HEADERS)

    def expect(method, path, body=None, status=200, **kwargs):
        pending.append(
            (
                method,
                BASE + path,
                {"headers": dict(transport_headers), "timeout": 30, **kwargs},
                Mock(status_code=status, json=lambda: body, text="upstream body"),
            )
        )

    def request(method, url, **kwargs):
        seen.append((method.upper(), url, kwargs))
        assert pending, f"Unexpected HTTP call: {seen[-1]}"
        expected_method, expected_url, expected_kwargs, response = pending.pop(0)
        assert seen[-1] == (expected_method, expected_url, expected_kwargs)
        return response

    for method in ("get", "post", "patch", "put", "delete"):
        monkeypatch.setattr(
            "requests." + method,
            lambda url, _method=method, **kw: request(_method, url, **kw),
        )
        monkeypatch.setattr(
            "requests.sessions.Session." + method,
            lambda self, url, _method=method, **kw: request(_method, url, **kw),
        )
    yield Mock(side_effect=expect, headers=transport_headers)
    assert not pending, f"Missing HTTP calls: {pending}"


@pytest.fixture(params=["registered", "direct"])
def legacy_call(request, server, monkeypatch, wire):
    wire.headers["Content-Type"] = "application/json"
    if request.param == "registered":
        return lambda name, *args: server.tools[name](*args)
    import sabor_mcp
    from sabor import config

    monkeypatch.setattr(config, "MEALIE_BASE_URL", BASE)
    monkeypatch.setattr(config, "MEALIE_API_TOKEN", "baseline")
    return lambda name, *args: getattr(sabor_mcp, name)(*args)


@pytest.mark.parametrize("mode", ["existing", "created", "error"])
def test_tag_resolution(legacy_call, wire, mode):
    wire(
        "GET",
        "/api/organizers/tags",
        {"items": [TAG] if mode == "existing" else []},
        params={"search": "video", "page": 1, "perPage": 100},
    )
    if mode != "existing":
        wire(
            "POST",
            "/api/organizers/tags",
            TAG,
            status=503 if mode == "error" else 201,
            json={"name": "video"},
        )
    expected = (
        {"ok": False, "status": 503, "body": "upstream body"}
        if mode == "error"
        else {"ok": True, **TAG, "created": mode == "created"}
    )
    assert legacy_call("get_or_create_tag", "video") == expected


@pytest.mark.parametrize("mode", ["success", "missing", "patch_error"])
def test_tag_recipe(legacy_call, wire, mode):
    wire(
        "GET",
        "/api/recipes/soup",
        {"tags": [OLD]},
        status=404 if mode == "missing" else 200,
    )
    if mode == "missing":
        expected = {"ok": False, "status": 404, "error": "recipe_not_found: soup"}
    else:
        wire(
            "GET",
            "/api/organizers/tags",
            {"items": [TAG]},
            params={"search": "video", "page": 1, "perPage": 100},
        )
        wire(
            "PATCH",
            "/api/recipes/soup",
            {},
            status=500 if mode == "patch_error" else 200,
            json={"tags": [OLD, TAG]},
        )
        expected = (
            {"ok": False, "status": 500, "body": "upstream body"}
            if mode == "patch_error"
            else {"ok": True, "slug": "soup", "tags_added": ["Vídeo"], "total_tags": 2}
        )
    assert legacy_call("tag_recipe", "soup", ["video"]) == expected


@pytest.mark.parametrize(
    "name,args,path,payload,success",
    [
        (
            "add_recipe_comment",
            ("soup", "Tasty"),
            "/api/comments",
            {"recipeId": "recipe", "text": "Tasty"},
            {"ok": True, "comment_id": "written", "slug": "soup", "text": "Tasty"},
        ),
        (
            "log_cooking_event",
            ("soup", "Cooked", "Tasty"),
            "/api/recipes/timeline/events",
            {
                "recipeId": "recipe",
                "subject": "Cooked",
                "eventType": "info",
                "eventMessage": "Tasty",
            },
            {"ok": True, "event_id": "written", "slug": "soup", "subject": "Cooked"},
        ),
        (
            "log_cooking_event",
            ("soup", "Cooked"),
            "/api/recipes/timeline/events",
            {"recipeId": "recipe", "subject": "Cooked", "eventType": "info"},
            {"ok": True, "event_id": "written", "slug": "soup", "subject": "Cooked"},
        ),
    ],
)
@pytest.mark.parametrize("mode", ["success", "missing", "no_id", "post_error"])
def test_comments(legacy_call, wire, name, args, path, payload, success, mode):
    wire(
        "GET",
        "/api/recipes/soup",
        {} if mode == "no_id" else {"id": "recipe"},
        status=404 if mode == "missing" else 200,
    )
    if mode == "missing":
        expected = {"ok": False, "status": 404, "error": "recipe_not_found: soup"}
    elif mode == "no_id":
        expected = {"ok": False, "error": "recipe_id_missing_for: soup"}
    else:
        wire(
            "POST",
            path,
            {"id": "written"},
            status=403 if mode == "post_error" else 201,
            json=payload,
        )
        expected = (
            {"ok": False, "status": 403, "body": "upstream body"}
            if mode == "post_error"
            else success
        )
    assert legacy_call(name, *args) == expected


@pytest.mark.parametrize("name", ["delete_shopping_list", "clear_shopping_list"])
@pytest.mark.parametrize("confirm", [False, True])
def test_shopping(server, wire, name, confirm):
    items = [{"id": "a", "note": "Rice"}, {"id": "b", "checked": True}]
    if not confirm or name == "clear_shopping_list":
        wire(
            "GET",
            f"/api/households/shopping/lists/{LIST}",
            {"name": "Weekly", "listItems": items},
        )
    if not confirm:
        expected = {
            "ok": True,
            "preview": True,
            "list_id": LIST,
            "items_to_remove": items,
            "count": 2,
        }
        if name == "delete_shopping_list":
            expected["name"] = "Weekly"
    elif name == "delete_shopping_list":
        wire("DELETE", f"/api/households/shopping/lists/{LIST}", status=204)
        expected = {"ok": True, "status": 204}
    else:
        wire(
            "DELETE",
            "/api/households/shopping/items",
            status=204,
            params=[("ids", "a"), ("ids", "b")],
        )
        expected = {"ok": True, "deleted": 2, "status": 204}
    assert server.tools[name](LIST, confirm=confirm) == expected


@pytest.mark.parametrize(
    "name,kwargs,message",
    [
        (
            "add_to_shopping_list",
            {"items": ["Rice"]},
            "Require a UUID list, exactly one nonempty source, valid ISO range, and multiplier > 0 and <= 20.",
        ),
        ("get_shopping_list_items", {}, "list_id must be a UUID."),
        ("check_shopping_list_items", {"item_ids": ["a"]}, "list_id must be a UUID."),
        ("remove_shopping_list_items", {"item_ids": ["a"]}, "list_id must be a UUID."),
    ],
)
def test_invalid_list_id(server, wire, name, kwargs, message):
    assert server.tools[name]("not-a-uuid", **kwargs) == {
        "ok": False,
        "error": {"code": "invalid_argument", "message": message, "provider": "sabor"},
    }


def test_valid_list_id(server, wire):
    wire("GET", f"/api/households/shopping/lists/{LIST}", {"listItems": []})
    assert server.tools["get_shopping_list_items"](LIST) == {
        "ok": True,
        "data": {
            "list_id": LIST,
            "items": [],
            "total": 0,
            "page": 1,
            "per_page": 100,
            "total_pages": 0,
        },
    }


@pytest.mark.parametrize("gate", [False, True])
def test_schedule(server, wire, monkeypatch, gate):
    ensure = Mock()
    if gate:
        # ensure_recipe_nutrition also resolves against the local nutrition DB,
        # so HTTP alone cannot isolate this gate. Stub that module boundary only;
        # duplicate/revision reads still use the real HTTP transport path.
        from sabor.tools import nutrition

        server = Server()
        register_tools(
            server,
            {
                "MEALIE_BASE_URL": BASE,
                "MEALIE_API_TOKEN": "baseline",
                "SABOR_TOOLSETS": "core",
                "SABOR_NUTRITION_ENABLED": "true",
            },
        )
        recipe = {"id": "recipe", "slug": "soup"}
        ensure.return_value = {
            "ok": True,
            "revision": nutrition._nutrition_recipe_revision(recipe),
        }
        monkeypatch.setattr(nutrition, "ensure_recipe_nutrition", ensure)
        wire(
            "GET",
            "/api/households/mealplans",
            {"items": []},
            params={"start_date": "2026-01-02", "end_date": "2026-01-02"},
        )
        wire(
            "GET",
            "/api/recipes/soup",
            recipe,
            headers={**HEADERS, "Content-Type": "application/json"},
        )
    else:
        wire("GET", "/api/recipes/soup", {"id": "recipe"})
    wire(
        "POST",
        "/api/households/mealplans",
        {"id": "entry"},
        json={
            "date": "2026-01-02",
            "entryType": "dinner",
            "title": "Soup",
            "text": "",
            "recipeId": "recipe",
        },
    )
    assert server.tools["set_mealplan_entry"](
        "2026-01-02", "dinner", "Soup", "soup"
    ) == {
        "ok": True,
        "id": "entry",
        "date": "2026-01-02",
        "entry_type": "dinner",
        "title": "Soup",
        "has_recipe_link": True,
        "scheduled": True,
        "created": True,
    }
    if gate:
        ensure.assert_called_once_with(
            "soup",
            {
                "tool": "set_mealplan_entry",
                "arguments": {
                    "date_str": "2026-01-02",
                    "entry_type": "dinner",
                    "title": "Soup",
                    "recipe_slug": "soup",
                },
            },
        )


def test_video_post_create_tags(server, wire, monkeypatch):
    import json
    from sabor.importing.video import GeminiNativeVideo

    candidate = {
        "name": "Soup",
        "ingredients": ["Salt"],
        "steps": ["Mix"],
        "servings": "2",
        "notes": "",
        "confidence": 0.9,
    }
    # Media acquisition/extraction are not under refactor; no temp media needed.
    monkeypatch.setattr(
        "sabor.importing.video.download", lambda source, root: (root / "video.mp4", "")
    )
    monkeypatch.setattr(
        GeminiNativeVideo,
        "extract",
        lambda self, path, captions, request: {
            "candidates": [candidate],
            "dropped": 0,
            "limitations": [],
        },
    )
    wire(
        "GET",
        "/api/recipes",
        {"items": [], "total": 0, "page": 1, "per_page": 100, "total_pages": 1},
        params={
            "search": "Soup",
            "page": 1,
            "perPage": 100,
            "orderBy": "name",
            "orderDirection": "asc",
            "requireAllTags": False,
            "requireAllCategories": False,
            "requireAllFoods": False,
            "requireAllTools": False,
        },
    )
    recipe = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Soup",
        "description": "\nSource: https://youtu.be/test",
        "recipeYield": "2",
        "recipeIngredient": ["Salt"],
        "recipeInstructions": ["Mix"],
    }
    wire(
        "POST",
        "/api/recipes/create/html-or-json",
        "soup",
        timeout=60,
        json={"data": json.dumps(recipe, ensure_ascii=False)},
    )
    headers = {**HEADERS, "Content-Type": "application/json"}
    tag = {"id": "video", "name": "video-import", "slug": "video-import"}
    wire("GET", "/api/recipes/soup", {"tags": [OLD]}, headers=headers)
    wire(
        "GET",
        "/api/organizers/tags",
        {"items": [tag]},
        headers=headers,
        params={"search": "video-import", "page": 1, "perPage": 100},
    )
    wire("PATCH", "/api/recipes/soup", {}, headers=headers, json={"tags": [OLD, tag]})
    assert server.tools["import_recipe"](
        "https://youtu.be/test", kind="video", dry_run=False
    ) == {
        "ok": True,
        "data": {
            "preview": False,
            "kind": "video",
            "provider": "gemini_native",
            "candidates": [candidate],
            "dropped": 0,
            "created": ["soup"],
            "skips": [],
            "limitations": [],
            "normalization": {},
            "nutrition": {},
            "warnings": [],
        },
    }


@pytest.mark.parametrize(
    "name,args,message,provider",
    [
        ("get_recipe", ("soup",), "Mealie is not configured.", "mealie"),
        ("get_shopping_lists", (), "Mealie is not configured.", "mealie"),
        ("get_shopping_list_items", (LIST,), "Mealie is not configured.", "mealie"),
        (
            "get_mealplan_week",
            ("2026-01-01", "2026-01-07"),
            "Recipe store operation failed.",
            "mealie",
        ),
        (
            "sync_shopping_list",
            (LIST, {"provider": "bring", "list_id": "target"}),
            "Store or shopping provider is not configured.",
            "sabor",
        ),
        (
            "get_shopping_sync_targets",
            (),
            "Shopping provider is not configured.",
            "sabor",
        ),
    ],
)
def test_missing_store(server, wire, monkeypatch, name, args, message, provider):
    from sabor.core.context import build_context
    from sabor.core.config import load_settings

    # Keep configured registration (otherwise gates omit tools), but exercise
    # the same MCP callable with an explicitly missing store via build_context.
    env = {
        "MEALIE_BASE_URL": BASE,
        "MEALIE_API_TOKEN": "baseline",
        "SABOR_TOOLSETS": "core,shopping_sync",
        "BRING_EMAIL": "baseline@example.com",
        "BRING_PASSWORD": "test-password",
    }
    ctx = build_context(load_settings(env))
    assert ctx.store is None
    monkeypatch.setattr("sabor.server.assemble_context", lambda settings: ctx)
    missing = Server()
    register_tools(missing, env)
    call = missing.tools[name]
    # Only human-readable message text is expected to change in #36, to neutral
    # wording. A later task may relax exactly that field, never code/provider.
    assert call(*args) == {
        "ok": False,
        "error": {
            "code": "provider_unconfigured",
            "message": message,
            "provider": provider,
        },
    }
