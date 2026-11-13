"""Published uvx commands pin Python >=3.12 (0.4.0 pre-release pass).

uv resolves a named tool (`uvx sabor-mcp`, `uvx --from 'sabor-mcp[postgres]'`) with its
default interpreter and does not honour requires-python: with a managed 3.10 or the macOS
system 3.9 the install fails (checked with uv 0.9.26 and 0.13.7). `--python ">=3.12"` fixes it.
"""

import base64
import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / "README.md",
    ROOT / "README.es.md",
    ROOT / "llms-install.md",
    *sorted((ROOT / "docs").glob("*.md")),
]
PIN = '--python ">=3.12"'
ARGS = [
    "--python",
    ">=3.12",
    "--from",
    "git+https://github.com/JlMoreno00/sabor",
    "sabor-mcp",
]


class InstallPythonPinTests(unittest.TestCase):
    def test_every_named_uvx_command_pins_python(self):
        # A named spec is `sabor-mcp` or `'sabor-mcp[extra]…'` right after uvx's options.
        named = re.compile(
            r"uvx (?P<options>(?:--python (?:\"[^\"]*\"|\S+) )?)(?:--from )?'?(?:git\+https://github\.com/JlMoreno00/sabor|sabor-mcp)"
        )
        found = 0
        for path in DOCS:
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                for match in named.finditer(line):
                    found += 1
                    # Quoted for a shell; bare inside add-mcp's single-quoted command string.
                    self.assertIn(
                        match["options"],
                        (f"{PIN} ", "--python >=3.12 "),
                        f"{path.name}:{number}",
                    )
        self.assertGreater(found, 20)

    def test_add_mcp_command_has_no_inner_quotes(self):
        # add-mcp splits its command on spaces and keeps quotes, so `">=3.12"` would reach uv quoted.
        # Outer double quotes protect `>` in POSIX shells and in Windows cmd.exe alike.
        text = (ROOT / "llms-install.md").read_text(encoding="utf-8")
        self.assertIn(
            'npx add-mcp "uvx --python >=3.12 --from git+https://github.com/JlMoreno00/sabor sabor-mcp" --name sabor -g',
            text,
        )
        self.assertNotIn("npx add-mcp 'uvx", text)

    def test_cli_commands_use_the_pin(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for command in (
            'claude mcp add --scope user sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp',
            'codex mcp add sabor -- uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp',
            'gemini mcp add --scope user sabor uvx --python ">=3.12" --from git+https://github.com/JlMoreno00/sabor sabor-mcp',
            """code --add-mcp '{"name":"sabor","command":"uvx","args":["--python",">=3.12","--from","git+https://github.com/JlMoreno00/sabor","sabor-mcp"]}\'""",
        ):
            self.assertIn(command, readme)
        self.assertIn(
            f"uvx {PIN} --from git+https://github.com/JlMoreno00/sabor sabor-mcp",
            readme,
        )

    def links(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        return dict(
            re.findall(
                r"\[(Add to Cursor|Install in VS Code|Insiders|Add to Kiro|Add to LM Studio|"
                r"Install in Goose)\]\((\S+?)\)\)?",
                readme,
            )
        )

    def test_one_click_links_carry_separate_args(self):
        links = self.links()
        self.assertEqual(len(links), 6)
        for name in ("Add to Cursor", "Add to LM Studio"):
            config = json.loads(
                base64.b64decode(parse_qs(urlsplit(links[name]).query)["config"][0])
            )
            self.assertEqual(config, {"command": "uvx", "args": ARGS}, name)
        kiro = json.loads(parse_qs(urlsplit(links["Add to Kiro"]).query)["config"][0])
        self.assertEqual(kiro, {"command": "uvx", "args": ARGS})
        for name in ("Install in VS Code", "Insiders"):
            inner = unquote(parse_qs(urlsplit(links[name]).query)["url"][0])
            config = json.loads(unquote(inner.split("install?", 1)[1]))
            self.assertEqual(
                config, {"name": "sabor", "command": "uvx", "args": ARGS}, name
            )
        goose = parse_qs(urlsplit(links["Install in Goose"]).query)
        self.assertEqual((goose["cmd"], goose["arg"]), (["uvx"], ARGS))

    def test_registry_package_passes_the_pin_to_uvx(self):
        server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
        package = server["packages"][1]
        self.assertEqual(package["runtimeHint"], "uvx")
        self.assertEqual(
            package["runtimeArguments"],
            [{"type": "named", "name": "--python", "value": ">=3.12"}],
        )


if __name__ == "__main__":
    unittest.main()
