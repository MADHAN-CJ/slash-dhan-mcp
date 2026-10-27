"""Read-only Python library over the Dhan v2 API, used by scripts the AI assistant writes."""

from .api import *  # noqa: F403
from .api import __all__ as _api_all

__version__ = "0.1.0"
__all__ = [*_api_all, "__version__"]
