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

    def test_portable_import_skill_follows_the_no_ai_workflow(self):
        # #102 §5: the agent extracts recipes itself; Sabor gives it media, never an AI call.
        path = ROOT / 'skills' / 'sabor-import' / 'SKILL.md'
        self.assertIn(path, SKILLS)
        text = path.read_text(encoding='utf-8')
        for words in ("import_recipe(kind='url'", "import_recipe(kind='video'", 'create_recipe(',
                      'video-import', 'no_transcript', 'no_frames', 'absolute path',
                      'SABOR_MEDIA_ROOTS', 'preview'):
            self.assertIn(words, text)
        # CodeRabbit on #127: it describes only bundle fields that exist today (#125 and the
        # transcriber add theirs), and discloses Mealie's own ingredient parser before saving.
        # #125 and the optional transcriber (#102 PR 4) are in: the skill describes them.
        for field in ('audio_path', 'local_asr', 'sabor-mcp[transcribe]', 'audio_not_saved', 'no_media_paths'):
            self.assertIn(field, text)
        for doc in (text, (ROOT / 'llms-install.md').read_text(encoding='utf-8')):
            self.assertIn("Mealie's ingredient parser", doc)
        for name in ('openai', 'gemini', 'llm', 'api key'):
            self.assertNotIn(name, text.casefold())
        self.assertIn('[skills/sabor-import/SKILL.md](skills/sabor-import/SKILL.md)',
                      (ROOT / 'llms-install.md').read_text(encoding='utf-8'))

    def test_pantry_skill_offers_the_flows_with_preview_and_no_ai(self):
        # #144 phase 1: receipts fill the pantry; every write is previewed and confirmed.
        path = ROOT / 'skills' / 'sabor-pantry' / 'SKILL.md'
        self.assertIn(path, SKILLS)
        text = path.read_text(encoding='utf-8')
        for words in ('get_pantry(', 'edit_pantry(', 'add_pantry_from_receipt(', 'preview_digest',
                      'confirm=True', 'conflict', 'ignored', 'uncertain', 'receipt', 'proactive',
                      'expiring_within_days', 'SABOR_TOOLSETS', 'sabor_capabilities('):
            self.assertIn(words, text)
        # CodeRabbit on #148: a receipt line enters the pantry once, with its corrected
        # quantity; Mealie suggestions take food IDs, never names.
        for words in ('only once', 'corrected quantity', '"op": "set"', 'food_id', 'Mealie', 'never send the name'):
            self.assertIn(words, text)
        self.assertNotIn('fix them first with `edit_pantry`', text)
        for name in ('openai', 'gemini', 'api key'):
            self.assertNotIn(name, text.casefold())
        install = (ROOT / 'llms-install.md').read_text(encoding='utf-8')
        self.assertIn('[skills/sabor-pantry/SKILL.md](skills/sabor-pantry/SKILL.md)', install)

    def test_pantry_skill_offers_deduction_and_shopping_flows(self):
        # #144 point 6: after a meal, offer to subtract it; before a list or an estimate,
        # the pantry is taken into account, and the agent says what that did.
        text = (ROOT / 'skills' / 'sabor-pantry' / 'SKILL.md').read_text(encoding='utf-8')
        for words in ('consume_from_pantry(', 'recipe_slugs', 'mealplan_start', 'mealplan_end',
                      'servings_multiplier', 'already_consumed', 'not_in_pantry', 'shortfall',
                      'add_to_shopping_list(', 'subtract_pantry', 'pantry_coverage', 'partial',
                      'estimate_cost(', 'exclude_pantry'):
            self.assertIn(words, text)
        self.assertNotIn('subtract_pantry=False', text.split('## Units')[0].split('After a meal')[0])

    def test_recipe_table_skill_teaches_tabular_recipe_notation(self):
        path = ROOT / 'skills' / 'sabor-recipe-table' / 'SKILL.md'
        self.assertIn(path, SKILLS)
        text = path.read_text(encoding='utf-8')
        for words in ('Tabular Recipe Notation', 'TRN', 'en tabla', 'rowspan', 'colspan', '<table>',
                      'get_recipe(', 'get_mealplan_week(', 'Raciones', 'Tiempo aprox.',
                      'Servir 4 raciones', 'Cortar en tiras', 'Cocinar 8 min',
                      'Añadir y cocinar 5-7 min', 'Worked example', 'weekly menu'):
            self.assertIn(words, text)
        self.assertIn('[skills/sabor-recipe-table/SKILL.md](skills/sabor-recipe-table/SKILL.md)',
                      (ROOT / 'llms-install.md').read_text(encoding='utf-8'))

    def test_recipe_table_example_fills_every_row(self):
        # A TRN table only renders in line if every row covers the same columns.
        text = (ROOT / 'skills' / 'sabor-recipe-table' / 'SKILL.md').read_text(encoding='utf-8')
        table = re.search(r'```html\n(<table>.*?</table>)\n```', text, re.S)
        assert table is not None, 'worked example has no ```html table'
        rows = re.findall(r'<tr>(.*?)</tr>', table[1], re.S)
        self.assertGreater(len(rows), 2)
        carried = {}  # column index -> rows still covered by an earlier rowspan
        covered_rows = []
        for row in rows:
            covered = {c for c, left in carried.items() if left > 0}
            column = 0
            for cell in re.finditer(r'<t[hd]([^>]*)>', row):
                while column in covered:
                    column += 1
                attrs = cell[1]
                span = int((re.search(r'colspan="(\d+)"', attrs) or [0, 1])[1])
                height = int((re.search(r'rowspan="(\d+)"', attrs) or [0, 1])[1])
                for c in range(column, column + span):
                    self.assertNotIn(c, covered, row)
                    covered.add(c)
                    if height > 1:
                        carried[c] = height
                column += span
            covered_rows.append(covered)
            carried = {c: left - 1 for c, left in carried.items() if left - 1 > 0}
        # Every row covers the same columns with no gaps, and every rowspan ends in the table.
        width = len(covered_rows[0])
        for covered in covered_rows:
            self.assertEqual(covered, set(range(width)))
        self.assertEqual(carried, {})

    def test_onboarding_keeps_configuration_out_of_the_chat(self):
        text = (ROOT / 'skills' / 'sabor-onboarding' / 'SKILL.md').read_text(encoding='utf-8')
        self.assertIn('sabor-mcp setup', text)
        self.assertIn('never', text.casefold())


if __name__ == '__main__':
    unittest.main()
