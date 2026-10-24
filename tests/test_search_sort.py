"""Search sort transport and tool contracts."""
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
import requests
from tests.support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()
from sabor.tools import recipes as r


class SearchSortTests(unittest.TestCase):
    def test_tool_sorts_and_mealie_params(self):
        with patch.object(r.config, "MEALIE_API_TOKEN", "test"), patch.object(requests, "get") as get:
            get.return_value = Mock(status_code=200)
            get.return_value.json.return_value = dict(items=[], total=0, page=1, per_page=25, total_pages=0)
            for sort in ("name", "created_at", "updated_at", "rating", "last_made", "random"):
                for direction in ("asc", "desc"):
                    with self.subTest(sort=sort, direction=direction):
                        result = r.search_recipes(sort=sort, direction=direction)
                        self.assertTrue(result["ok"], result)
                        expected = dict(search="", page=1, perPage=25,
                                        requireAllTags=False, requireAllCategories=False,
                                        requireAllFoods=False, requireAllTools=False,
                                        orderBy="update_at" if sort == "updated_at" else sort,
                                        orderDirection="asc" if sort == "random" else direction)
                        if sort in ("rating", "last_made"):
                            expected["orderByNullPosition"] = "last"
                        if sort == "random":
                            expected["paginationSeed"] = datetime.now(timezone.utc).date().isoformat()
                        self.assertEqual(get.call_args.kwargs["params"], expected)
            self.assertEqual(r.search_recipes(sort="unknown")["error"]["code"], "invalid_argument")

    def test_sqlite_rating_tool_envelope(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from types import SimpleNamespace
        from sabor.core.toolsets import bind_context
        from sabor.store.sqlite import SqliteRecipeStore
        from sabor.store import StoreError, store_error_envelope
        with TemporaryDirectory() as directory:
            ctx = SimpleNamespace(store=SqliteRecipeStore(Path(directory) / "store.db"))
            self.assertEqual(bind_context(r.search_recipes, ctx)(sort="rating"),
                             store_error_envelope(StoreError("unsupported_operation", "sqlite")))
