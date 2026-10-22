"""Environment configuration and runtime defaults for direct-call compatibility."""
import os
from sabor.core.legacy_config import load_legacy_config

_config = load_legacy_config(os.environ)
# Retain the explicit runtime-variable declaration used by the manifest contract.
MEALIE_API_TOKEN = os.getenv("MEALIE_API_TOKEN", "")
globals().update({name: getattr(_config, name) for name in _config.values})
