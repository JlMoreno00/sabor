"""Calendar adapter and context routing regression tests."""
import unittest
import sabor.store.mealie_schedule as schedule
from typing import Any
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sabor.store import DateRange, StoreError
from sabor.store.mealie import MealieRecipeStore
from sabor.tools import mealplan
from sabor.core.toolsets import bind_context


def response(body, status=200):
    return Mock(status_code=status, json=Mock(return_value=body))


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.store = MealieRecipeStore("https://one", "one", self.session)
        from sabor import config
        from sabor.core.context import RuntimeState
        self.ctx = SimpleNamespace(store=self.store, settings=SimpleNamespace(legacy_config=config),
                                   state=RuntimeState())
        self.rows = [dict(id=str(i), date="2026-01-01", entryType="lunch",
                          title="", recipe={"id": "r", "slug": "rice", "name": "Rice"})
                     for i in range(2)]
        self.rows.append(dict(id="3", date="2026-01-01", entryType="dinner", title="Out", text="Cafe"))

    def call(self, name, *args, **kwargs) -> Any:
        return bind_context(getattr(mealplan, name), self.ctx)(*args, **kwargs)

    def test_pages_repeats_and_text(self):
        self.session.get.side_effect = [response(dict(items=self.rows[:2], page=1, total_pages=2)),
                                        response(dict(items=self.rows[2:], page=2, total_pages=2))]
        entries = self.store.get_mealplan(DateRange("2026-01-01", "2026-01-02"))
        self.assertEqual([e.recipe_slug for e in entries], ["rice", "rice", None])
        self.assertEqual(entries[2].title, "Out")
        self.assertEqual(self.session.get.call_args.kwargs["params"]["page"], 2)

    def test_preview_no_delete_and_raw_entries(self):
        self.session.get.return_value = response(dict(items=self.rows))
        result = self.call("clear_mealplan_week", "a", "b")
        self.assertEqual(result["items_to_remove"], self.rows)
        self.assertEqual(result["count"], 3)
        self.session.delete.assert_not_called()

    def test_two_instances(self):
        other_session = Mock()
        other = MealieRecipeStore("https://two", "two", other_session)
        self.session.get.return_value = response(dict(items=[]))
        other_session.get.return_value = response(dict(items=self.rows))
        self.assertEqual(self.store.get_mealplan(DateRange("a", "b")), [])
        self.assertEqual(len(other.get_mealplan(DateRange("a", "b"))), 3)
        self.assertTrue(other_session.get.call_args.args[0].startswith("https://two/"))
        self.assertEqual(other_session.get.call_args.kwargs["headers"]["Authorization"], "Bearer two")

    def test_store_error(self):
        self.session.get.return_value = response({}, 500)
        with self.assertRaises(StoreError):
            self.store.get_mealplan(DateRange("a", "b"))

    def test_direct_registered_parity(self):
        with patch.object(schedule.config, "MEALIE_API_TOKEN", "one"), patch.object(
                schedule.config, "MEALIE_BASE_URL", "https://one"), patch.object(
                schedule.requests, "get", return_value=response(dict(items=self.rows))):
            self.session.get.return_value = response(dict(items=self.rows))
            for name in ("get_mealplan_week", "clear_mealplan_week"):
                self.assertEqual(getattr(mealplan, name)("a", "b"), self.call(name, "a", "b"))

    def test_write_direct_registered_parity(self):
        with patch.object(schedule.config, "MEALIE_API_TOKEN", "one"), patch.object(
                schedule.config, "MEALIE_BASE_URL", "https://one"), patch.object(
                schedule.config, "SABOR_NUTRITION_ENABLED", False), patch.object(
                schedule.requests, "get", return_value=response({"id": "r"})), patch.object(
                schedule.requests, "post", return_value=response({"id": "entry"})), patch.object(
                schedule.requests, "delete", return_value=response({}, 204)):
            self.session.get.return_value = response({"id": "r"})
            self.session.post.return_value = response({"id": "entry"})
            self.session.delete.return_value = response({}, 204)
            for name, args in (("set_mealplan_entry", ("a", "lunch", "Rice", "rice")),
                               ("delete_mealplan_entry", ("entry",))):
                self.assertEqual(getattr(mealplan, name)(*args), self.call(name, *args))

    def test_confirm_partial_and_pagination_conflict(self):
        self.session.get.return_value = response(dict(items=self.rows))
        self.session.delete.side_effect = [response({}, 204), response({}, 500), response({}, 204)]
        result = self.call("clear_mealplan_week", "a", "b", True)
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["errors"], [{"id": "1", "status": 500}])
        self.session.get.side_effect = [
            response(dict(items=self.rows[:1], page=1, total_pages=2)),
            response(dict(items=self.rows[:1], page=2, total_pages=2))]
        with self.assertRaises(StoreError):
            self.store.get_mealplan(DateRange("a", "b"))

    def test_enabled_duplicate_and_revision_gate(self):
        recipe = {"id": "r"}
        with patch.object(schedule.config, "SABOR_NUTRITION_ENABLED", True), patch.object(
                schedule.nutrition, "ensure_recipe_nutrition", return_value={"ok": True, "revision": "v"}), patch.object(
                schedule.nutrition, "_nutrition_fetch_recipe", return_value=recipe), patch.object(
                schedule.nutrition, "_nutrition_recipe_revision", return_value="v") as revision:
            self.session.get.return_value = response(dict(items=self.rows))
            result = self.call("set_mealplan_entry", "2026-01-01", "lunch", "Rice", "rice")
            self.assertEqual(result["code"], "mealplan_entry_existing")
            self.session.post.assert_not_called()
            revision.return_value = "changed"
            result = self.call("set_mealplan_entry", "2026-01-01", "lunch", "Rice", "rice")
            self.assertEqual(result["code"], "nutrition_revision_conflict")
            self.session.post.assert_not_called()

    def test_gate_enabled_disabled_parity(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), patch.object(schedule.config, "SABOR_NUTRITION_ENABLED", enabled), patch.object(
                    schedule.nutrition, "ensure_recipe_nutrition", return_value={"ok": False, "code": "blocked"}) as gate:
                self.session.get.return_value = response({"id": "r"})
                self.session.post.return_value = response({"id": "entry"})
                result = self.call("set_mealplan_entry", "a", "lunch", "Rice", "rice")
                self.assertEqual(result["ok"], not enabled)
                self.assertEqual(gate.call_count, int(enabled))
                with patch.object(schedule.config, "MEALIE_API_TOKEN", "one"), patch.object(
                        schedule.requests, "get", return_value=response({"id": "r"})), patch.object(
                        schedule.requests, "post", return_value=response({"id": "entry"})):
                    self.assertEqual(result, mealplan.set_mealplan_entry("a", "lunch", "Rice", "rice"))
                if enabled:
                    self.assertFalse(result["scheduled"])
                else:
                    self.assertTrue(result["has_recipe_link"])


if __name__ == "__main__":
    unittest.main()
