"""Portable Agent Skills and the agent installer reference only real tools (#67)."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILLS = sorted((ROOT / 'skills').glob('*/SKILL.md')) + sorted((ROOT / 'examples').rglob('SKILL.md'))


def _tools():
    names = {'sabor_capabilities'}
    for path in (ROOT / 'sabor').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.FunctionDef) and any(
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == 'tool'
                    for d in node.decorator_list):
                names.add(node.name)
    return names


def _frontmatter(text):
    match = re.match(r'---\n(.*?)\n---\n', text, re.S)
    if match is None:
        raise AssertionError('missing frontmatter')
    return dict(line.split(': ', 1) for line in match[1].splitlines() if ': ' in line)


class AgentSkillTests(unittest.TestCase):
    def test_portable_onboarding_skill_exists(self):
        self.assertIn(ROOT / 'skills' / 'sabor-onboarding' / 'SKILL.md', SKILLS)

    def test_frontmatter_follows_the_agent_skills_specification(self):
        for path in SKILLS:
            with self.subTest(skill=path.parent.name):
                meta = _frontmatter(path.read_text(encoding='utf-8'))
                self.assertEqual(meta.get('name'), path.parent.name)
                self.assertRegex(meta['name'], r'^[a-z0-9]+(-[a-z0-9]+)*$')
                self.assertLessEqual(len(meta['name']), 64)
                self.assertTrue(meta.get('description', '').strip())
                self.assertLessEqual(len(meta['description']), 1024)

    def test_only_real_tools_are_named(self):
        tools = _tools()
        for path in [*SKILLS, ROOT / 'llms-install.md']:
            with self.subTest(file=str(path.relative_to(ROOT))):
                called = set(re.findall(r'`([a-z_][a-z0-9_]*)\(', path.read_text(encoding='utf-8')))
                self.assertEqual(called - tools, set())

    def test_onboarding_keeps_configuration_out_of_the_chat(self):
        text = (ROOT / 'skills' / 'sabor-onboarding' / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('sabor-mcp setup', text)
        self.assertIn('never', text.casefold())


if __name__ == '__main__':
    unittest.main()
