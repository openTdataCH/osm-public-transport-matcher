"""What a source adapter returns, for built-in and external integrations alike."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from osm_public_transport_matcher.core.state import SourceState


@dataclass
class AdapterResult:
    """Normalized input plus optional route products, extensions and provenance.

    Adapter implementations own source syntax. Matching policy remains in a
    profile, and presentation remains in a result consumer. External adapters
    need no registration here: they return an ``AdapterResult`` and pass its
    normalized state to the public API.
    """

    source: SourceState
    route_data: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
