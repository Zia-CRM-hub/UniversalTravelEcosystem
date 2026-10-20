from fastapi import APIRouter, Depends

from ..models import Tenant
from ..schemas import TenantConfigOut
from ..tenancy import resolve_tenant

router = APIRouter(prefix="/tenant", tags=["tenant"])


@router.get("/config", response_model=TenantConfigOut)
async def config(tenant: Tenant = Depends(resolve_tenant)):
    """Public. Clients call this on start-up (with X-Tenant-Slug) to white-label themselves."""
    s = tenant.settings or {}
    branding = {"display_name": tenant.name, "primary_color": None, "logo_url": None, **(s.get("branding") or {})}
    branding["display_name"] = branding.get("display_name") or tenant.name
    return TenantConfigOut(slug=tenant.slug, name=tenant.name, self_signup=bool(s.get("self_signup", False)), branding=branding)
