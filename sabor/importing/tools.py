"""Import orchestration: recipe web pages, and video media bundles; the client agent extracts the recipe."""
import ipaddress
import json
import os
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit
from typing import NoReturn

import requests
import urllib3
from urllib3.connection import HTTPConnection, HTTPSConnection
from sabor.core import errors
from sabor.core.config import load_settings
from sabor.core.envelope import fail, ok
from sabor.core.redaction import sanitize
from sabor.core.toolsets import current_context, tool, import_ready


def settings():
    return load_settings(os.environ)


def http_url(source):
    p = urlsplit(source)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('Invalid URL')
    p.port
    return p


def local_path(source):
    """An absolute path in the platform's sense (/x, C:\\x, \\\\server\\share), checked before any URL parsing."""
    return os.path.isabs(source)


def detect(source):
    if local_path(source):
        # An absolute path names a local video file (stdio only, under SABOR_MEDIA_ROOTS).
        return 'video'
    p = urlsplit(source)
    if p.scheme:
        http_url(source)
        host = p.hostname.lower()
        if any(host == h or host.endswith('.' + h) for h in
               ('youtube.com', 'youtu.be', 'tiktok.com', 'vimeo.com', 'instagram.com')) or p.path.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')):
            return 'video'
        return 'url'
    if source.startswith(('./', '../')):
        raise ValueError('Local media needs an absolute path')
    return 'text'


class ImportErrorResult(Exception):
    def __init__(self, result):
        self.result = result


def reject(code, message, provider='sabor', data=None) -> NoReturn:
    raise ImportErrorResult(fail(code, message, provider, data))


def request(method, path, cfg, **kwargs):
    response = method(cfg.mealie_base_url.rstrip('/') + '/api/recipes' + path,
                      headers={'Authorization': 'Bearer ' + cfg.mealie_api_token}, timeout=45, **kwargs)
    if not 200 <= response.status_code < 300:
        raise ImportErrorResult(errors.from_http(response.status_code, 'mealie'))
    return response.json()


def duplicate(name, cfg):
    page = 1
    while page <= 1000:
        data = request(requests.get, '', cfg, params={'search': name, 'page': page, 'perPage': 100})
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise ValueError('Invalid search')
        for item in data['items']:
            if ' '.join(item['name'].casefold().split()) == ' '.join(name.casefold().split()):
                if not isinstance(item.get('slug'), str):
                    raise ValueError('Invalid slug')
                return item['slug']
        pages = data.get('total_pages')
        if type(pages) is not int or pages < 0:
            raise ValueError('Invalid pagination')
        if page >= pages:
            return None
        page += 1
    raise ValueError('Search budget exceeded')


def plain_steps(value):
    """Flatten schema.org strings, steps and recursively nested sections."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in plain_steps(item)]
    if isinstance(value, dict):
        if value.get('@type') == 'HowToSection':
            return plain_steps(value.get('itemListElement', []))
        if isinstance(value.get('text'), str):
            return [value['text']]
    raise ValueError('Invalid instructions')


def _shutdown(sock):
    try:
        # The plain socket method also works for TLS sockets and leaves the SSL object alone.
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


class _HTTPConnection(HTTPConnection):
    """Stop waiting for response headers at a monotonic deadline.

    urllib3 timeouts apply between socket reads, so a server trickling its headers
    one byte at a time could otherwise hold the hop past a total deadline. A timer
    shuts the socket down at the deadline, which fails the blocked read.
    """
    def __init__(self, *args, header_deadline=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.header_deadline = header_deadline

    def getresponse(self):
        if self.header_deadline is None or self.sock is None:
            return super().getresponse()
        timer = threading.Timer(max(self.header_deadline - time.monotonic(), 0), _shutdown, (self.sock,))
        timer.daemon = True
        timer.start()
        try:
            return super().getresponse()
        finally:
            timer.cancel()


class _HTTPSConnection(_HTTPConnection, HTTPSConnection):
    pass


def image_response(parsed, address, timeout=None):
    """Connect only to a validated literal IP; preserve Host, TLS SNI and verification.

    No proxy environment or hostname re-resolution is used. Each redirect creates
    a fresh validated connection; connections are not reused across downloads.
    A timeout with `total` also bounds the wait for the response headers.
    """
    timeout = timeout or urllib3.Timeout(connect=10, read=20)
    total = timeout.total
    deadline = time.monotonic() + total if isinstance(total, (int, float)) else None
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if parsed.scheme == 'https':
        pool = urllib3.HTTPSConnectionPool(address, assert_hostname=parsed.hostname,
            server_hostname=parsed.hostname, cert_reqs='CERT_REQUIRED', port=port, maxsize=1,
            header_deadline=deadline)
    else:
        pool = urllib3.HTTPConnectionPool(address, port=port, maxsize=1, header_deadline=deadline)
    # urllib3's connection Protocol types reject its own concrete classes, hence setattr.
    setattr(pool, 'ConnectionCls', _HTTPSConnection if parsed.scheme == 'https' else _HTTPConnection)
    host = parsed.netloc
    target = (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')
    try:
        return pool.urlopen('GET', target, headers={'Host': host}, redirect=False,
            retries=False, preload_content=False, timeout=timeout)
    finally:
        # With preload_content=False the checked-out response owns its socket.
        pool.close()


def html_bytes(url, final=None):
    """HTML page bytes; `final`, when given, receives the URL after redirects."""
    return safe_bytes(url, 5, ('text/html', 'application/xhtml+xml'), 'HTML', final=final)


_NAT64 = ipaddress.ip_network('64:ff9b::/96')


def public_address(text):
    """True only for a global unicast address, unwrapping IPv4 embedded in IPv6.

    Python reports multicast and NAT64/6to4/Teredo addresses that carry a private or
    loopback IPv4 as global, so those are checked on the embedded IPv4 (#49).
    """
    address = ipaddress.ip_address(text)
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            address = address.ipv4_mapped
        elif address in _NAT64:
            address = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
        elif address.sixtofour:
            address = address.sixtofour
        elif address.teredo:
            address = address.teredo[1]
    return address.is_global and not address.is_multicast


_READ_TIMEOUT = 20


def _body_chunks(response, remaining):
    """Response body chunks; with a deadline, one socket read at a time.

    Without `remaining` the body streams as before. With it each read is a single
    `read1` whose socket timeout is the time left (at most the read timeout), so a
    server trickling bytes cannot hold a 64 KB `stream` block past the deadline.
    """
    read1 = getattr(response, 'read1', None)
    if remaining is None:
        yield from response.stream(65536)
        return
    if read1 is None:
        # urllib3 1.26 has no read1: refuse rather than stream past the deadline.
        raise ValueError('Deadline-bounded reads require urllib3 read1')
    while True:
        left = remaining()
        if left <= 0:
            return
        sock = getattr(getattr(response, 'connection', None), 'sock', None)
        if sock is not None:
            sock.settimeout(min(_READ_TIMEOUT, left))
        chunk = read1(65536)
        if not chunk:
            return
        yield chunk


def _resolve(host, port, remaining):
    """getaddrinfo bounded by the time left; it has no timeout of its own.

    With a deadline the lookup runs in a daemon thread that is abandoned, not joined,
    when time runs out; the resolver finishes it in the background.
    """
    if remaining is None:
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    outcome = {}

    def lookup():
        try:
            outcome['addresses'] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:
            outcome['error'] = exc
    thread = threading.Thread(target=lookup, name='sabor-dns', daemon=True)
    thread.start()
    thread.join(max(remaining(), 0))
    if thread.is_alive():
        raise TimeoutError('DNS lookup exceeded the time limit')
    if 'error' in outcome:
        raise outcome['error']
    return outcome['addresses']


def safe_bytes(url, megabytes, content_types, label, remaining=None, final=None):
    """Fetch bounded typed content through pinned public IPs at every hop.

    `remaining`, when given, returns the seconds left of a total deadline; it bounds
    every DNS lookup, hop and read. `final`, when given, receives the URL after redirects.
    """
    def expired():
        return remaining is not None and remaining() <= 0

    for _ in range(4):
        if expired():
            reject('timeout', f'{label} download exceeded its time limit.')
        p = http_url(url)
        addresses = _resolve(p.hostname, p.port or (443 if p.scheme == 'https' else 80), remaining)
        if not addresses or any(not public_address(a[4][0]) for a in addresses):
            reject('invalid_argument', f'{label} URL must resolve to public addresses.')
        if remaining is None:
            response = image_response(p, addresses[0][4][0])
        else:
            # Connection and header wait count against the deadline too.
            response = image_response(p, addresses[0][4][0], timeout=urllib3.Timeout(
                connect=10, read=_READ_TIMEOUT, total=max(remaining(), 0.001)))
        try:
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers['Location'])
                continue
            if not 200 <= response.status < 300:
                raise ImportErrorResult(errors.from_http(response.status, 'sabor'))
            if int(response.headers.get('Content-Length', 0)) > megabytes * 1024 * 1024:
                reject('invalid_argument', f'{label} exceeds the {megabytes} MB limit.')
            content = bytearray()
            for chunk in _body_chunks(response, remaining):
                content.extend(chunk)
                if len(content) > megabytes * 1024 * 1024:
                    reject('invalid_argument', f'{label} exceeds the {megabytes} MB limit.')
                if expired():
                    reject('timeout', f'{label} download exceeded its time limit.')
            if expired():
                reject('timeout', f'{label} download exceeded its time limit.')
            mime = response.headers.get('Content-Type', '').split(';')[0]
            if mime not in content_types or not content:
                reject('invalid_argument', 'A supported image is required.' if label == 'Image' else 'An HTML page is required.')
            if final is not None:
                final.append(url)
            return bytes(content), mime
        finally:
            response.close()
    reject('invalid_argument', 'Too many image redirects.' if label == 'Image' else 'Too many HTML redirects.')


@tool('import', requires=import_ready, uses_context=True)
def import_recipe(source: str, kind: str = 'auto', dry_run: bool = True,
                  language: str = 'en', max_recipes: int = 8,
                  min_confidence: float = 0.55) -> dict:
    """Preview or import a recipe from a web page URL; Sabor uses no AI provider.

    A video URL, or an absolute path to a local video file when Sabor runs on the user's
    machine, returns its metadata, subtitles and frames (on stdio also as files) and writes
    no recipe: read the recipe from them, then save it with create_recipe. For text or
    images, read the recipe yourself and save it with create_recipe.
    """
    return _import_recipe(current_context(), source, kind, dry_run, language, max_recipes, min_confidence)


def _import_recipe(ctx, source, kind, dry_run, language, max_recipes, min_confidence):
    provider = 'sabor'
    created = []
    images = {}
    try:
        if (not isinstance(source, str) or not source.strip() or len(source) > 20000
            or kind not in ('auto', 'url', 'text', 'image', 'video') or type(dry_run) is not bool
            or type(max_recipes) is not int or not 1 <= max_recipes <= 20
            or type(min_confidence) not in (int, float) or not 0 <= min_confidence <= 1
            or not isinstance(language, str) or not 1 <= len(language) <= 32):
            reject('invalid_argument', 'Invalid import arguments.')
        detected = detect(source)
        kind = detected if kind == 'auto' else kind
        if kind == 'video' and urlsplit(source).scheme in ('http', 'https'):
            # A media bundle for the client agent (#102): nothing is extracted or written,
            # so dry_run has no effect.
            http_url(source)
            from .video import bundle
            return ok(dict(preview=True, kind='video', provider='sabor', candidates=[], dropped=0,
                           created=[], skips=[]) | bundle(source, language, ctx.settings.media_roots,
                                                         keep=ctx.settings.local_media))
        if kind == 'video' and local_path(source):
            if not ctx.settings.local_media:
                reject('unsupported_operation', 'Local video files are read only when Sabor runs on your '
                       'machine (stdio). Pass the video URL instead, or read the recipe yourself and save '
                       'it with create_recipe.')
            from .video import local_bundle
            return ok(dict(preview=True, kind='video', provider='sabor', candidates=[], dropped=0,
                           created=[], skips=[]) | local_bundle(source, ctx.settings.media_roots, language))
        if kind != 'url':
            # Sabor has no AI provider (#102): the client agent reads text and images
            # itself and saves the recipe it extracts with create_recipe.
            reject('unsupported_operation', f'Sabor does not extract recipes from {kind}: read the '
                   'recipe yourself and save it with create_recipe, adding the source to the description.')
        native = kind in getattr(ctx.store, 'native_import_kinds', frozenset())
        provider = 'mealie' if native else 'sabor'
        if ctx.store is None:
            reject('provider_unconfigured', 'Recipe store is not configured.', 'mealie')
        http_url(source)
        page_url = source
        data = ctx.store.preview_url(source) if native else None
        if not native:
            from .scrape import recipe_from_html
            final = []
            html, _ = html_bytes(source, final)
            # Relative recipe links and images resolve against the page after redirects.
            page_url = final[0] if final else source
            data = recipe_from_html(html, page_url)
            if data is None:
                reject('not_found', 'Could not extract a recipe at this URL.', provider)
        if data == 'recipe_scrapers was unable to scrape this URL':
            reject('not_found', 'Mealie could not extract a recipe at this URL.', provider)
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict) or not isinstance(data.get('name'), str) or not data['name'].strip():
            raise ValueError('Invalid recipe')
        candidates = [{'name': data['name'], 'ingredients': data.get('recipeIngredient', []),
            'steps': plain_steps(data.get('recipeInstructions', [])), 'yield': data.get('recipeYield'),
            'times': {k: data.get(k) for k in ('totalTime', 'prepTime', 'cookTime')}, 'image': data.get('image')}]
        skips = []
        if not dry_run:
            for c in candidates:
                slug = store_duplicate(c['name'], ctx.store)
                if slug:
                    if native:
                        reject('conflict', 'An exact normalized recipe name already exists.', 'mealie', {'slug': slug})
                    skips.append({'name': c['name'], 'slug': slug, 'reason': 'duplicate'})
                    continue
                if native:
                    slug = ctx.store.import_url(source)
                else:
                    from sabor.store import RecipeDraft, Ingredient, Instruction
                    provider = 'mealie'
                    draft = RecipeDraft(name=c['name'], description=f'Source: {source}',
                        recipeYield=c.get('yield') or '',
                        totalTime=c['times'].get('totalTime'), prepTime=c['times'].get('prepTime'),
                        cookTime=c['times'].get('cookTime'),
                        recipeIngredient=tuple(Ingredient(originalText=x) for x in c['ingredients']),
                        recipeInstructions=tuple(Instruction(text=x) for x in c['steps']))
                    slug = ctx.store.create_recipe(draft).slug
                    provider = 'sabor'
                if not isinstance(slug, str) or not slug.strip():
                    raise ValueError('Invalid created slug')
                created.append(slug)
                if not native:
                    # Built-in stores keep the scraped image; Mealie's native import does its own.
                    from .images import keep_recipe_image
                    report = keep_recipe_image(ctx.store, slug, c.get('image'), page_url)
                    if report:
                        images[slug] = report
        hooks = {} if dry_run else run_hooks(ctx, created)
        if images:
            hooks['images'] = images
            hooks.setdefault('warnings', []).extend(
                {'slug': slug, 'code': 'image_warning', 'warning': report['warning']}
                for slug, report in images.items() if 'warning' in report)
        return ok(dict(preview=dry_run, kind=kind, provider=provider, candidates=candidates,
                       dropped=0, created=created, skips=skips, limitations=[]) | hooks)
    except ImportErrorResult as exc:
        return partial(ctx, created, provider) if created else exc.result
    except Exception as exc:
        from sabor.store import StoreError
        if isinstance(exc, StoreError) and not created:
            return fail(exc.code, 'Recipe store import failed.', exc.provider)
        if created:
            return partial(ctx, created, provider)
        if isinstance(exc, ValueError) and provider == 'sabor':
            return fail('invalid_argument', 'Invalid import source or arguments.')
        return errors.from_exception(exc, provider)


def run_hooks(ctx, slugs):
    """Run after_commit exactly once; a crash becomes a warning, never an import failure.

    Callers invoke it after every creation succeeded or failed, so a hook crash can
    neither re-run the hooks nor turn a completed import into partial_failure.
    """
    try:
        return after_commit(ctx, slugs)
    except Exception:
        return {'normalization': {}, 'nutrition': {}, 'warnings': [
            {'slug': None, 'code': 'post_commit_hooks_failed', 'warning': 'post_commit_hooks_failed'}]}


def partial(ctx, created, provider):
    """Return partial_failure after running the post-commit hooks on created slugs."""
    result = errors.partial(created, ['remaining'], provider)
    result['data'].update(sanitize(run_hooks(ctx, created)))
    return result


def after_commit(ctx, slugs):
    """Apply create_recipe's normalization and nutrition hooks to committed slugs.

    Hook failures are reported as warnings and never fail the import.
    """
    from sabor.tools import nutrition
    normalizer = getattr(ctx.store, 'normalize_imported', None)
    normalization, enriched, warnings = {}, {}, []
    for slug in slugs:
        if ctx.settings.normalize_imports and normalizer:
            try:
                normalized = normalizer(slug)
            except Exception:
                normalized = {'ok': False, 'warning': 'normalization_failed'}
            normalization[slug] = normalized
            if not normalized.get('ok'):
                warnings.append({'slug': slug, 'code': 'normalization_warning', 'warning':
                    normalized.get('warning') or normalized.get('error') or 'normalization_failed'})
        result = nutrition._nutrition_after_import({}, slug) if ctx.store.id == 'mealie' else {}
        if 'nutrition' in result:
            enriched[slug] = result['nutrition']
        if 'nutrition_warning' in result:
            warnings.append({'slug': slug, 'code': 'nutrition_warning', 'warning': result['nutrition_warning']})
    return {'normalization': normalization, 'nutrition': enriched, 'warnings': warnings}


def store_duplicate(name, store):
    from sabor.store import RecipeSearch
    for number in range(1, 1001):
        page = store.search_recipes(RecipeSearch(query=name, page=number, per_page=100))
        for item in page.items:
            if ' '.join(item.name.casefold().split()) == ' '.join(name.casefold().split()):
                if not isinstance(item.slug, str):
                    raise ValueError('Invalid slug')
                return item.slug
        if number >= page.total_pages:
            return None
    raise ValueError('Search budget exceeded')
