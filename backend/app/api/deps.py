from dataclasses import dataclass

from fastapi import Depends, Request

from ..core.errors import DomainError
from ..models import Tenant, User
from ..security import current_principal
from ..tenancy import providers_for, resolve_tenant


def get_providers(request: Request) -> dict:
    """Every supplier the PLATFORM has enabled. Use the scopes below for tenant-facing work."""
    return request.app.state.providers


def get_gateway(request: Request):
    return request.app.state.gateway


def get_llm(request: Request):
    return request.app.state.llm


def get_whatsapp(request: Request):
    return request.app.state.whatsapp


@dataclass
class Scope:
    """Who is asking and what they may use: their tenant and that tenant's suppliers."""

    tenant: Tenant
    providers: dict
    user: User | None = None


async def public_scope(tenant: Tenant = Depends(resolve_tenant), providers: dict = Depends(get_providers)) -> Scope:
    """Anonymous traffic (search, planning): tenant comes from the X-Tenant-Slug header, else the default tenant."""
    return Scope(tenant, providers_for(providers, tenant))


async def user_scope(p=Depends(current_principal), providers: dict = Depends(get_providers)) -> Scope:
    """Logged-in traveller features. The tenant is the user's own; any X-Tenant-Slug header is ignored."""
    if p.tenant is None:
        raise DomainError(403, "no_tenant", "Platform administrators do not use traveller features")
    return Scope(p.tenant, providers_for(providers, p.tenant), p.user)
