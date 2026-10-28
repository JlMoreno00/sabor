"""Download recipe images for the built-in stores (#49).

Fetching reuses the import SSRF guard (`tools.safe_bytes`: public IPs pinned at
every hop, bounded redirects, size cap, connect/read timeouts) and adds a total
deadline and a check that the bytes really are an image.
"""
import time
from urllib.parse import urljoin, urlsplit

MAX_MEGABYTES = 5
MAX_BYTES = MAX_MEGABYTES * 1024 * 1024
DEADLINE_SECONDS = 30
MAX_URL_LENGTH = 2048
_DECLARED = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')


class ImageError(Exception):
    """The image could not be downloaded safely; the reason is not exposed."""


def image_url(value):
    """First usable URL from a scraped schema.org image (string, list or ImageObject)."""
    if isinstance(value, str):
        value = value.strip()
        return value if value and len(value) <= MAX_URL_LENGTH else None
    if isinstance(value, dict):
        return image_url(value.get('url') or value.get('contentUrl'))
    if isinstance(value, list):
        for item in value:
            url = image_url(item)
            if url:
                return url
    return None


def sniff(data):
    """Image type from the file signature, or None when the bytes are not an image."""
    if data.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def fetch_recipe_image(url):
    """Return (bytes, sniffed content type) or raise ImageError."""
    from .tools import safe_bytes
    start = time.monotonic()
    try:
        content, _declared = safe_bytes(url, MAX_MEGABYTES, _DECLARED, 'Image',
                                        expired=lambda: time.monotonic() - start > DEADLINE_SECONDS)
    except Exception:
        raise ImageError('image_download_failed') from None
    kind = sniff(content)
    if kind is None:
        raise ImageError('image_download_failed')
    return content, kind


def keep_recipe_image(store, slug, value, page_url):
    """Download and keep a scraped image for a created recipe; never raises.

    Returns the report for the import response, or None when there is no usable
    http(s) image or the store does not keep images (Mealie handles its own).
    """
    from sabor.store import RecipeImage
    setter = getattr(store, 'set_recipe_image', None)
    url = image_url(value)
    if setter is None or url is None:
        return None
    url = urljoin(page_url, url)
    if urlsplit(url).scheme not in ('http', 'https') or len(url) > MAX_URL_LENGTH:
        return None
    try:
        data, kind = fetch_recipe_image(url)
        image = RecipeImage(url, kind, data)
        report = {'stored': True, 'source_url': url, 'content_type': kind, 'bytes': len(data)}
    except Exception:
        image = RecipeImage(url)
        report = {'stored': False, 'source_url': url, 'warning': 'image_download_failed'}
    try:
        setter(slug, image)
    except Exception:
        return {'stored': False, 'source_url': url, 'warning': 'image_store_failed'}
    return report
