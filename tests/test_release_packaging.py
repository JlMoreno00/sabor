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


class PublishPyPITests(PublishWorkflowTests):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/publish-pypi.yml").read_text(encoding="utf-8")

    def test_runs_only_on_manual_dispatch_or_version_tags(self):
        triggers = [line.strip() for line in _top_level_block(self.text, '"?on"?')]
        self.assertEqual(triggers, [
            "workflow_dispatch:", "inputs:", "target:",
            "description: Package index (TestPyPI unless pypi is chosen explicitly)",
            "type: choice", "options:", "- testpypi", "- pypi", "default: testpypi",
            "push:", "tags:", '- "v*"'])
        self.assertNotIn("pull_request", self.text)
        self.assertNotIn("branches", self.text)

    def test_manual_runs_default_to_testpypi_and_tags_to_pypi(self):
        # Tag pushes carry no inputs, so `inputs.target || 'pypi'` selects PyPI only for tags
        # or an explicit choice; a plain manual run uploads to TestPyPI (#62).
        self.assertEqual(self.text.count("inputs.target || 'pypi'"), 2)
        self.assertIn("environment: ${{ inputs.target || 'pypi' }}", self.text)
        self.assertIn("repository-url: ${{ (inputs.target || 'pypi') == 'testpypi'"
                      " && 'https://test.pypi.org/legacy/' || 'https://upload.pypi.org/legacy/' }}",
                      self.text)

    def test_least_privilege_and_pinned_actions(self):
        permissions = [line.strip() for line in _top_level_block(self.text, "permissions")]
        self.assertEqual(permissions, ["contents: read"])
        self.assertRegex(self.text, r"    permissions:\n      contents: read\n      id-token: write")
        self.assertIn("persist-credentials: false", self.text)
        uses = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertEqual(uses, [
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e",
            "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086",
        ])

    def test_publishes_only_the_sabor_ghcr_image(self):
        self.assertEqual(self.text.count("uses: pypa/gh-action-pypi-publish@"), 1)
        self.assertIn("run: uv build", self.text)
        self.assertIn("if: startsWith(github.ref, 'refs/tags/')", self.text)
        self.assertIn('os.environ["RELEASE_TAG"] == f"v{version}"', self.text)


@unittest.skipUnless(shutil.which("docker"), "docker CLI not installed")
class ComposeConfigTests(unittest.TestCase):
    def test_compose_contract(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-compose.py")],
                                capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
