"""Store-independent import paths through real SQLite registration."""
import json
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import nullcontext

import pytest
from mcp.server.fastmcp import FastMCP

from sabor.server import register_tools
from sabor.importing import tools as t


@pytest.fixture
def importer(tmp_path):
    server = FastMCP('imports')
    register_tools(server, {'SABOR_STORE_DB': str(tmp_path / 'recipes.db'),
        'SABOR_TOOLSETS': 'core,import', 'SABOR_LLM_BASE_URL': 'http://localhost:11434/v1',
        'SABOR_LLM_MODEL': 'vision'})
    return {tool.name: tool.fn for tool in server._tool_manager.list_tools()}


@pytest.mark.parametrize('page', ['scraper', 'graph'])
def test_url_preview_commit(importer, page):
    html = (Path(__file__).parent / 'fixtures/import' / (page + '.html')).read_bytes()
    fallback = patch('recipe_scrapers.scrape_html', side_effect=ValueError('unsupported')) if page == 'graph' else nullcontext()
    png = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
    # The graph page names an image: the download is simulated, never a live request (#49).
    with fallback, patch.object(t, 'html_bytes', return_value=(html, 'text/html')), patch(
            'sabor.importing.images.fetch_recipe_image', return_value=(png, 'image/png')) as image, patch(
            'sabor.tools.nutrition._nutrition_after_import') as nutrition:
        preview = importer['import_recipe']('https://example.org/recipe', kind='url')
        assert preview['ok'], preview
        assert preview['data']['candidates'][0]['ingredients']
        result = importer['import_recipe']('https://example.org/recipe', kind='url', dry_run=False)
        assert result['ok'], result
        assert result['data']['created']
        stored = importer['get_recipe'](result['data']['created'][0])
        assert 'Source: https://example.org/recipe' in str(stored)
        if page == 'graph':
            image.assert_called_once_with('https://example.org/soup.png')
            assert result['data']['images'][result['data']['created'][0]]['stored'] is True
        nutrition.assert_not_called()
        duplicate = importer['import_recipe']('https://example.org/recipe', kind='url', dry_run=False)
        assert duplicate['data']['skips'][0]['reason'] == 'duplicate'


def test_no_recipe(importer):
    html = (Path(__file__).parent / 'fixtures/import/empty.html').read_bytes()
    with patch.object(t, 'html_bytes', return_value=(html, 'text/html')):
        assert importer['import_recipe']('https://example.org')['error']['code'] == 'not_found'


@pytest.mark.parametrize('kind', ['text', 'image', 'video'])
def test_extracted_preview_commit(importer, kind):
    candidate = dict(name='Sopa', ingredients=['200 g harina'], steps=['Mezclar'],
                     servings='2', notes='Notas', confidence=.9)
    response = Mock(status_code=200)
    response.json.return_value = {'choices': [{'message': {'content': json.dumps({'recipes': [candidate]})}}]}
    source = 'recipe text' if kind == 'text' else 'https://example.org/recipe'
    with patch.object(t.requests, 'post', return_value=response) as post, patch.object(
            t, 'image_bytes', return_value=(b'png', 'image/png')), patch(
            'sabor.importing.video.preview_video', return_value={
                'candidates': [candidate], 'dropped': 0, 'limitations': []}), patch(
            'sabor.importing.video.tag_video'), patch(
            'sabor.tools.nutrition._nutrition_after_import') as nutrition:
        preview = importer['import_recipe'](source, kind=kind)
        assert preview['ok'], preview
        assert preview['data']['candidates'][0]['name'] == 'Sopa'
        result = importer['import_recipe'](source, kind=kind, dry_run=False)
        assert result['ok'], result
        assert result['data']['created']
        nutrition.assert_not_called()
        if kind == 'image':
            assert post.call_args.kwargs['json']['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')


def test_image_unconfigured(tmp_path):
    server = FastMCP('no-llm')
    register_tools(server, {'SABOR_STORE_DB': str(tmp_path / 'recipes.db'), 'SABOR_TOOLSETS': 'import'})
    tool = next(x.fn for x in server._tool_manager.list_tools() if x.name == 'import_recipe')
    result = tool('https://example.org/image', kind='image')
    assert result['error']['code'] == 'provider_unconfigured'
    assert 'Mealie' not in result['error']['message']


@pytest.mark.parametrize('case', ['private', 'redirect', 'size', 'type'])
def test_html_fetch_safety(case):
    public = [(2, 1, 6, '', ('93.184.216.34', 443))]
    private = [(2, 1, 6, '', ('127.0.0.1', 443))]
    response = Mock(status=200, headers={'Content-Type': 'text/html'})
    response.stream.return_value = [b'<html>']
    if case == 'redirect':
        response.status, response.headers = 302, {'Location': 'https://private.test'}
    elif case == 'size':
        response.stream.return_value = [b'x' * (5 * 1024 * 1024), b'x']
    elif case == 'type':
        response.headers = {'Content-Type': 'application/pdf'}
    with patch.object(t.socket, 'getaddrinfo', side_effect=[private] if case == 'private' else [public, private]), patch.object(t, 'image_response', return_value=response):
        with pytest.raises(t.ImportErrorResult) as error:
            t.html_bytes('https://example.org')
        assert error.value.result['error']['code'] == 'invalid_argument'


def test_real_scraper_fixture():
    from sabor.importing.scrape import recipe_from_html
    html = (Path(__file__).parent / 'fixtures/import/scraper.html').read_bytes()
    with patch('sabor.importing.scrape.LinkedData') as fallback:
        data = recipe_from_html(html, 'https://example.org/recipe')
        assert data is not None
        assert data['name'] == 'Sopa'
        assert data['recipeIngredient'] == ['200 g harina']
        fallback.assert_not_called()
