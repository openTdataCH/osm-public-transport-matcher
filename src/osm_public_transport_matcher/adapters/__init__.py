"""Curated format readers outside the matching core.

External adapters return an :class:`AdapterResult` without being registered
in this package. Import individual built-ins to keep dependencies lazy.
"""

from .base import AdapterResult

__all__ = ["AdapterResult"]
