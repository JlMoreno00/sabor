"""Repository review policy does not enforce a generic docstring percentage."""
from pathlib import Path
import unittest


class ReviewConfigTests(unittest.TestCase):
    def test_docstring_coverage_check_is_disabled(self):
        config = (Path(__file__).resolve().parents[1] / '.coderabbit.yaml').read_text(encoding='utf-8')
        # Freeze the nested policy and quote "off" so YAML parsers keep it a string.
        self.assertRegex(config, r'(?m)^reviews:\n(?:[ \t].*\n|\n)*?'
                                 r'  pre_merge_checks:\n    docstrings:\n      mode: "off"\s*$')
