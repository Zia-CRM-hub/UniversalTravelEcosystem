from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class Found:
    """A normalized offer plus the provider-private payload needed to book it later."""

    offer: dict
    raw: dict


@dataclass
class BookResult:
    provider_order_id: str
    reference: str  # the supplier's own PNR / confirmation number


class TravelProvider(ABC):
    """One supplier. Implementations must not leak provider types past this boundary.

    Prices are integer minor units for the *whole party* (`adults` / `guests`).
    """

    name: str
    kinds: frozenset[str]

    def supports(self, kind: str) -> bool:
        return kind in self.kinds

    async def search_transport(self, kind: str, origin: str, destination: str, day: date, adults: int) -> list[Found]:
        raise NotImplementedError

    async def search_hotels(self, city: str, check_in: date, check_out: date, stars_min: int, guests: int) -> list[Found]:
        raise NotImplementedError

    @abstractmethod
    async def book(self, offer: dict, raw: dict, passengers: list[dict], contact: dict, reference: str) -> BookResult:
        """Create the order. Never retried automatically (not idempotent at the supplier)."""

    @abstractmethod
    async def cancel(self, provider_order_id: str) -> None: ...

    def health(self) -> dict:
        return {"name": self.name, "kinds": sorted(self.kinds)}

    async def aclose(self) -> None:
        return None
