"""Release packaging contract (#33): one-command compose and a gated image workflow."""
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-image.yml"


def _top_level_block(text, key):
    """Lines of a top-level YAML mapping block, without a YAML dependency."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(rf"^{key}:\s*$", line))
    block = []
    for line in lines[start + 1:]:
        if line and not line.startswith((" ", "#")):
            break
        if line.strip() and not line.strip().startswith("#"):
            block.append(line)
    return block


class PublishWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_only_on_manual_dispatch_or_version_tags(self):
        triggers = [line.strip() for line in _top_level_block(self.text, '"?on"?')]
        self.assertEqual(triggers, ["workflow_dispatch:", "push:", "tags:", '- "v*"'])
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("branches", self.text)

    def test_only_the_repository_owner_can_publish(self):
        self.assertIn("if: github.actor == github.repository_owner", self.text)

    def test_least_privilege_and_pinned_actions(self):
        permissions = [line.strip() for line in _top_level_block(self.text, "permissions")]
        self.assertEqual(permissions, ["contents: read"])
        self.assertIn("packages: write", self.text)
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

    def test_publishes_only_the_sabor_ghcr_image(self):
        self.assertIn("ghcr.io/jlmoreno00/sabor", self.text)
        self.assertEqual(self.text.count("push: true"), 1)


@unittest.skipUnless(shutil.which("docker"), "docker CLI not installed")
class ComposeConfigTests(unittest.TestCase):
    def test_compose_contract(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-compose.py")],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
