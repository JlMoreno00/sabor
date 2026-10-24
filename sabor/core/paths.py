"""Pure portable path defaults shared by runtime configuration and imports."""
from pathlib import Path
import sys


def catalog_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    checkout = root / 'catalog'
    return checkout if checkout.is_dir() else root / 'sabor' / '_catalog'


def data_dir(env) -> Path:
    explicit = env.get('SABOR_DATA_DIR', '').strip()
    if explicit:
        return Path(explicit)
    if Path('/data').is_dir():
        return Path('/data')
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'sabor'
    if sys.platform == 'win32':
        return Path(dict(env).get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'sabor'
    return Path(dict(env).get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'sabor'
