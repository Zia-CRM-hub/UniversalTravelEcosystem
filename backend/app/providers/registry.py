from ..core.config import Settings
from .amadeus import AmadeusProvider
from .base import TravelProvider
from .demo import DemoProvider
from .duffel import DuffelProvider

_FACTORIES = {"demo": DemoProvider, "duffel": DuffelProvider, "amadeus": AmadeusProvider}


def build_providers(s: Settings, transports: dict | None = None) -> dict[str, TravelProvider]:
    """Instantiate exactly the providers named in PROVIDERS. `transports` is a test seam."""
    s.validate_runtime()
    transports = transports or {}
    return {n: _FACTORIES[n].from_settings(s, transport=transports.get(n)) for n in s.enabled_providers}
