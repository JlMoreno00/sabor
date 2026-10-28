"""Recipe image download for the built-in stores (#49): offline, no live network."""
import unittest
from unittest.mock import Mock, patch

import pytest

from sabor.importing import images
from sabor.importing import tools as t
from sabor.store import RecipeImage
from sabor.store.sqlite import SqliteRecipeStore

PUBLIC = [(2, 1, 6, '', ('93.184.216.34', 443))]
PRIVATE = [(2, 1, 6, '', ('192.168.1.10', 443))]
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 32
GIF = b'GIF89a' + b'\x00' * 32
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 32


def served(body, content_type='image/png', status=200, headers=None):
    response = Mock(status=status, headers={'Content-Type': content_type, **(headers or {})})
    response.stream.return_value = [body]
    return response


class ImageUrlTests(unittest.TestCase):
    def test_scraped_shapes(self):
        for value, expected in (('https://e.org/a.png', 'https://e.org/a.png'),
                                (['', 'https://e.org/b.jpg'], 'https://e.org/b.jpg'),
                                ({'@type': 'ImageObject', 'url': 'https://e.org/c.webp'}, 'https://e.org/c.webp'),
                                ([{'contentUrl': 'https://e.org/d.gif'}], 'https://e.org/d.gif'),
                                (None, None), ('  ', None), (3, None), ({'url': 5}, None),
                                ('https://e.org/' + 'a' * 4096, None)):
            with self.subTest(value=value):
                self.assertEqual(images.image_url(value), expected)


@patch.object(t.socket, 'getaddrinfo', return_value=PUBLIC)
@patch.object(t, 'image_response', create=True)
class FetchTests(unittest.TestCase):
    def test_real_images_use_their_sniffed_type(self, fetch, dns):
        for body, declared, expected in ((PNG, 'image/png', 'image/png'), (JPEG, 'image/jpeg', 'image/jpeg'),
                                         (GIF, 'image/gif', 'image/gif'), (WEBP, 'image/webp', 'image/webp'),
                                         # A real JPEG served with a wrong image type is stored as JPEG.
                                         (JPEG, 'image/png', 'image/jpeg')):
            with self.subTest(expected=expected, declared=declared):
                fetch.return_value = served(body, declared)
                self.assertEqual(images.fetch_recipe_image('https://e.org/a'), (body, expected))

    def test_only_real_images(self, fetch, dns):
        # S3: the bytes must be an image, whatever the header claims.
        for body, declared in ((b'<html>not an image</html>', 'image/png'), (b'', 'image/png'),
                               (PNG, 'text/html'), (b'\x00' * 64, 'image/jpeg'), (PNG, 'image/svg+xml')):
            with self.subTest(declared=declared, body=body[:8]):
                fetch.return_value = served(body, declared)
                with self.assertRaises(images.ImageError):
                    images.fetch_recipe_image('https://e.org/a')

    def test_size_limit(self, fetch, dns):
        limit = images.MAX_BYTES
        fetch.return_value = served(PNG, headers={'Content-Length': str(limit + 1)})
        with self.assertRaises(images.ImageError):
            images.fetch_recipe_image('https://e.org/a')
        fetch.return_value = served(PNG + b'\x00' * limit)
        with self.assertRaises(images.ImageError):
            images.fetch_recipe_image('https://e.org/a')
        fetch.return_value.close.assert_called()

    def test_total_deadline(self, fetch, dns):
        # S4: a slow trickle that stays under the read timeout still stops at the deadline.
        response = served(PNG)
        response.stream.return_value = iter([PNG[:8], PNG[8:]])
        fetch.return_value = response
        clock = iter([0.0, 0.0, images.DEADLINE_SECONDS + 1, images.DEADLINE_SECONDS + 2])
        with patch.object(images.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(images.ImageError):
                images.fetch_recipe_image('https://e.org/a')
        response.close.assert_called()

    def test_http_errors_and_bad_urls(self, fetch, dns):
        fetch.return_value = served(PNG, status=404)
        with self.assertRaises(images.ImageError):
            images.fetch_recipe_image('https://e.org/a')
        for url in ('ftp://e.org/a.png', 'file:///etc/passwd', 'https://user:pw@e.org/a.png', 'not a url'):
            with self.subTest(url=url), self.assertRaises(images.ImageError):
                images.fetch_recipe_image(url)


class SsrfTests(unittest.TestCase):
    @patch.object(t, 'image_response', create=True)
    def test_private_and_local_addresses_are_blocked(self, fetch):
        # S6: private, loopback, link-local and unspecified addresses never get a request.
        for address in ('127.0.0.1', '10.0.0.5', '192.168.1.10', '172.16.0.1', '169.254.169.254',
                        '0.0.0.0', '::1', 'fd00::1', 'fe80::1', '100.64.0.1', '::ffff:127.0.0.1',
                        # Multicast and IPv4 embedded in IPv6 (NAT64, 6to4, Teredo) that Python
                        # still reports as global.
                        '224.0.0.1', '64:ff9b::7f00:1', '64:ff9b::a00:1', '2002:7f00:1::1',
                        '2001:0:4136:e378:8000:63bf:80ff:fffe'):
            with self.subTest(address=address), patch.object(
                    t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
                with self.assertRaises(images.ImageError):
                    images.fetch_recipe_image('https://e.org/a.png')
        fetch.assert_not_called()

    @patch.object(t, 'image_response', create=True)
    def test_public_addresses_are_allowed(self, fetch):
        for address in ('93.184.216.34', '2606:2800:220:1:248:1893:25c8:1946', '::ffff:93.184.216.34',
                        '64:ff9b::5db8:d822'):
            fetch.return_value = served(PNG)
            with self.subTest(address=address), patch.object(
                    t.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', (address, 443))]):
                self.assertEqual(images.fetch_recipe_image('https://e.org/a.png'), (PNG, 'image/png'))

    @patch.object(t.socket, 'getaddrinfo', side_effect=[PUBLIC, PRIVATE])
    @patch.object(t, 'image_response', create=True)
    def test_redirect_to_private_is_blocked(self, fetch, dns):
        fetch.return_value = served(b'', status=302, headers={'Location': 'https://internal/a.png'})
        with self.assertRaises(images.ImageError):
            images.fetch_recipe_image('https://e.org/a.png')
        self.assertEqual(fetch.call_count, 1)



PAGE = (b'<html><script type="application/ld+json">{"@type": "Recipe", "name": "Sopa de tomate", '
        b'"recipeIngredient": ["2 tomates"], "recipeInstructions": ["Cocer"], '
        b'"image": {"@type": "ImageObject", "url": "/img/sopa.png"}}</script></html>')


@pytest.fixture
def importer(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from sabor.server import register_tools
    server = FastMCP('images')
    register_tools(server, {'SABOR_STORE_DB': str(tmp_path / 'recipes.db'), 'SABOR_TOOLSETS': 'core,import'})
    tools = {tool.name: tool.fn for tool in server._tool_manager.list_tools()}
    tools['store'] = lambda: SqliteRecipeStore(tmp_path / 'recipes.db')
    return tools


def import_page(importer, page=PAGE, dns=PUBLIC, body=PNG, dry_run=False):
    with patch('recipe_scrapers.scrape_html', side_effect=ValueError('unsupported')), \
            patch.object(t, 'html_bytes', return_value=(page, 'text/html')), \
            patch.object(t.socket, 'getaddrinfo', return_value=dns), \
            patch.object(t, 'image_response', create=True, return_value=served(body)) as fetch:
        result = importer['import_recipe']('https://recetas.example/sopa', kind='url', dry_run=dry_run)
    return result, fetch


def test_url_import_downloads_and_keeps_the_image(importer):
    # S1/S2: the bytes and the original (absolute) URL are kept.
    result, fetch = import_page(importer)
    assert result['ok'], result
    slug = result['data']['created'][0]
    assert result['data']['images'] == {slug: {'stored': True, 'source_url': 'https://recetas.example/img/sopa.png',
                                               'content_type': 'image/png', 'bytes': len(PNG)}}
    assert fetch.call_args.args[0].path == '/img/sopa.png'
    assert importer['store']().get_recipe_image(slug) == RecipeImage(
        'https://recetas.example/img/sopa.png', 'image/png', PNG)


@pytest.mark.parametrize('dns,body', [(PRIVATE, PNG), (PUBLIC, b'<html>no</html>')])
def test_failed_download_still_imports_with_the_url(importer, dns, body):
    # S5/S6: blocked or invalid image -> recipe imported, URL kept, warning, nothing broken.
    result, _ = import_page(importer, dns=dns, body=body)
    assert result['ok'], result
    slug = result['data']['created'][0]
    url = 'https://recetas.example/img/sopa.png'
    assert result['data']['images'] == {slug: {'stored': False, 'source_url': url, 'warning': 'image_download_failed'}}
    assert {'slug': slug, 'code': 'image_warning', 'warning': 'image_download_failed'} in result['data']['warnings']
    assert importer['store']().get_recipe_image(slug) == RecipeImage(url)
    recipe = importer['get_recipe'](slug)
    assert recipe['ok'] and 'image' not in recipe['data']


def test_no_image_and_previews_are_unchanged(importer):
    page = PAGE.replace(b', "image": {"@type": "ImageObject", "url": "/img/sopa.png"}', b'')
    result, fetch = import_page(importer, page=page)
    assert result['ok'] and 'images' not in result['data']
    fetch.assert_not_called()
    # A preview never downloads anything.
    preview, fetch = import_page(importer, dry_run=True)
    assert preview['ok'] and 'images' not in preview['data']
    fetch.assert_not_called()


def test_non_http_image_urls_are_ignored(importer):
    page = PAGE.replace(b'/img/sopa.png', b'data:image/png;base64,AAAA')
    result, fetch = import_page(importer, page=page)
    assert result['ok'] and 'images' not in result['data']
    fetch.assert_not_called()
    assert importer['store']().get_recipe_image(result['data']['created'][0]) is None


if __name__ == '__main__':
    unittest.main()
