import os
import subprocess
import sys
import unittest
from unittest.mock import Mock

from scripts.nutrition_pilot import run_pilot, serialize_report


class NutritionPilotTests(unittest.TestCase):
    def test_requires_exact_slugs_and_never_publishes_by_default(self):
        calculate = Mock(return_value={"ok": True, "calculation": {"status": "complete"}})
        result = run_pilot(["one", "two"], calculate=calculate)
        self.assertEqual(result["processed"], 2)
        self.assertTrue(result["dry_run"])
        self.assertTrue(all(call.kwargs["publish"] is False for call in calculate.call_args_list))

    def test_report_serialization_preserves_unicode_without_mojibake(self):
        encoded = serialize_report({"results": [{"slug": "jalapeño", "name": "Cebollín"}]})
        self.assertIn("jalapeño", encoded)
        self.assertIn("Cebollín", encoded)
        self.assertNotIn("Ã", encoded)

    def test_mapping_counts_are_bound_to_calculation_chronology(self):
        calculate = Mock(side_effect=[
            {"ok": True, "calculation": {"status": "complete",
                                           "calculated_at": "2026-08-08T10:00:00+00:00",
                                           "applied_mapping_count": 2}},
            {"ok": True, "calculation": {"status": "complete",
                                           "calculated_at": "2026-08-08T11:00:00+00:00",
                                           "applied_mapping_count": 0}},
        ])

        report = run_pilot(["before", "after"], calculate=calculate)

        self.assertEqual(report["results"][0]["applied_mapping_count"], 2)
        self.assertEqual(report["results"][1]["applied_mapping_count"], 0)
        self.assertEqual(report["applied_mapping_count"], 2)

    def test_report_writes_utf8_when_host_stdout_is_ascii(self):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "ascii"
        process = subprocess.run(
            [sys.executable, "-c",
             "from scripts.nutrition_pilot import write_report; "
             "write_report({'slug': 'jalapeño', 'name': 'Cebollín'})"],
            cwd=os.path.dirname(os.path.dirname(__file__)), env=environment,
            capture_output=True, check=False,
        )

        self.assertEqual(process.returncode, 0, process.stderr.decode("utf-8", "replace"))
        self.assertEqual(
            process.stdout.decode("utf-8"),
            '{"name": "Cebollín", "slug": "jalapeño"}\n',
        )


if __name__ == "__main__":
    unittest.main()
