"""Offline documentation checks; stdlib only, no imports of application code."""
import ast
from collections import Counter
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
tools = {}
for path in sorted((ROOT / 'sabor').rglob('*.py')):
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Name)
                    and decorator.func.id == 'tool'):
                tools[node.name] = (ast.literal_eval(decorator.args[0]), ast.dump(node.args))

readme = (ROOT / 'README.md').read_text()
reference = readme.split('## Tool reference by toolset\n', 1)[1].split('## Safety and operations', 1)[0]
seen = {}
for group, body in re.findall(r'### ([^\n]+)\n(.*?)(?=### |\Z)', reference, re.S):
    for block in re.findall(r'```python\n(.*?)```', body, re.S):
        for signature in block.strip().splitlines():
            node = ast.parse('def ' + signature + ':\n    pass').body[0]
            assert isinstance(node, ast.FunctionDef)
            if node.name == 'sabor_capabilities':
                assert not node.args.args and group == 'Always on'
            else:
                assert node.name in tools, signature
                assert tools[node.name] == (group, ast.dump(node.args)), signature
                assert node.name not in seen, node.name
                seen[node.name] = group
assert seen.keys() == tools.keys(), tools.keys() - seen.keys()
# Validate example calls as well as the complete signature inventory.
for code in re.findall(r'`([^`\n]+)`', readme):
    match = re.match(r'([a-z][a-z_]+)\(', code)
    if not match or match[1] == 'sabor_capabilities':
        continue
    name = match[1]
    assert name in tools, name
    call = ast.parse(code, mode='eval').body
    assert isinstance(call, ast.Call)
    signature = next(line for line in reference.splitlines() if line.startswith(name + '('))
    definition = ast.parse('def ' + signature + ':\n    pass').body[0]
    assert isinstance(definition, ast.FunctionDef)
    args = definition.args
    assert len(call.args) <= len(args.args), code
    assert {k.arg for k in call.keywords} <= {a.arg for a in args.args}, code
print(f'PASS: {len(seen)} tool names, toolsets and full signatures match code; discovery and inline calls checked')

paths = [ROOT / 'README.md', ROOT / 'CHANGELOG.md']
paths += sorted((ROOT / 'docs').rglob('*.md')) + sorted((ROOT / 'examples').rglob('*.md'))
links = 0
for path in paths:
    for target in re.findall(r'(?<!!)\[[^\]]+\]\(([^\s)]+)(?:\s+[^)]*)?\)', path.read_text()):
        if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith('#'):
            continue
        local, _, anchor = target.partition('#')
        dest = path.parent / local
        assert dest.exists(), (path.relative_to(ROOT), target)
        if anchor and dest.suffix == '.md':
            headings = re.findall(r'^#+\s+(.+)', dest.read_text(), re.M)
            slugs = {re.sub(r'[^\w\- ]', '', h.lower()).replace(' ', '-') for h in headings}
            assert anchor in slugs, (path.relative_to(ROOT), target)
        links += 1
print(f'PASS: {links} relative links resolve across {len(paths)} Markdown files')

removed = re.compile(r'\bmercadona_(?:search_products|get_product_price|estimate_recipe_cost|estimate_shopping_list_cost)\b')
hits = Counter()
for path in paths:
    for number, line in enumerate(path.read_text().splitlines(), 1):
        for name in removed.findall(line):
            assert path == ROOT / 'CHANGELOG.md', (path, number, name)
            hits[name] += 1
            print(f'{path.relative_to(ROOT)}:{number}: {line}')
assert len(hits) == 4 and all(count == 1 for count in hits.values()), hits
print('PASS: four removed names occur only in CHANGELOG migration (one occurrence each)')
