"""Context-aware tools receive their context explicitly per call (#4)."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from typing import Any
from unittest.mock import patch

from support import install_runtime_dependency_stubs

install_runtime_dependency_stubs()
from sabor.core import toolsets
from sabor.core.toolsets import bind_context, current_context, tool, use_context


def _collect(fn, **kwargs):
    """Decorate a test tool without leaking it into the shared catalog."""
    before = list(toolsets.catalog)
    try:
        return tool("core", uses_context=True, **kwargs)(fn)
    finally:
        toolsets.catalog[:] = before


class ContextInjectionTests(unittest.TestCase):
    def test_bound_tool_reads_its_own_context(self):
        def which() -> Any:
            """Return the bound context name."""
            return {"name": current_context().name}
        fn = _collect(which)
        one = bind_context(fn, SimpleNamespace(name="one"))
        two = bind_context(fn, SimpleNamespace(name="two"))
        self.assertEqual((one()["name"], two()["name"]), ("one", "two"))

    def test_callable_results_are_returned_unchanged(self):
        marker = lambda ctx: "deferred"  # noqa: E731
        def returns_callable() -> Any:
            """Return a callable as a legitimate value."""
            return marker
        fn = _collect(returns_callable)
        self.assertIs(bind_context(fn, SimpleNamespace())(), marker)
        with patch("sabor.core.context.default_context", side_effect=AssertionError("built")):
            self.assertIs(fn(), marker)

    def test_context_is_unbound_outside_calls_and_after_errors(self):
        with self.assertRaises(LookupError):
            current_context()
        def boom() -> Any:
            """Fail inside a bound call."""
            current_context()
            raise ValueError("boom")
        with self.assertRaises(ValueError):
            bind_context(_collect(boom), SimpleNamespace())()
        with self.assertRaises(LookupError):
            current_context()

    def test_direct_calls_build_compatibility_context_lazily_once(self):
        def validate(value: int) -> Any:
            """Validate before reading the context twice."""
            if value < 0:
                return {"ok": False}
            return {"same": current_context() is current_context()}
        fn = _collect(validate)
        ctx = SimpleNamespace()
        with patch("sabor.core.context.default_context", return_value=ctx) as default:
            self.assertEqual(fn(-1), {"ok": False})
            default.assert_not_called()
            self.assertEqual(fn(1), {"same": True})
            default.assert_called_once_with()

    def test_nested_and_threaded_bindings_do_not_leak(self):
        seen = {}
        with use_context(SimpleNamespace(name="outer")):
            with use_context(SimpleNamespace(name="inner")):
                seen["inner"] = current_context().name
            seen["outer"] = current_context().name
            def other_thread():
                try:
                    current_context()
                except LookupError:
                    seen["thread"] = "unbound"
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join()
        self.assertEqual(seen, {"inner": "inner", "outer": "outer", "thread": "unbound"})

    def test_no_tool_body_returns_a_deferred_callable(self):
        root = Path(__file__).resolve().parents[1] / "sabor"
        offenders = []
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Lambda) and [a.arg for a in node.args.args] == ["ctx"]:
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
