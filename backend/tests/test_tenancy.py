"""SaaS model: platform (super admin) -> tenant -> organization -> user."""
import asyncio
from datetime import timedelta

import pytest

from app.core.config import Settings
from app.db import SessionLocal
from app.models import Booking, OfferSnapshot, now
from app.worker import cleanup_offers, flag_stale_pending
from tests.conftest import D1, confirm, open_checkout

SUPER = ("root@platform.com", "superadmin-pass-123")
PW = "tenant-admin-pw-1"


def login(c, email, password, tenant=None):
    h = {"X-Tenant-Slug": tenant} if tenant else {}
    r = c.post("/v1/auth/login", json={"email": email, "password": password}, headers=h)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", **h}


def super_admin(c):
    return login(c, *SUPER, tenant="platform")


def make_tenant(c, slug, **kw):
    su = super_admin(c)
    body = {"slug": slug, "name": slug.title() + " Corp", "admin_email": f"admin@{slug}.com", "admin_name": "Admin",
            "admin_password": PW, **kw}
    r = c.post("/v1/platform/tenants", headers=su, json=body)
    assert r.status_code == 201, r.text
    return r.json(), login(c, f"admin@{slug}.com", PW, tenant=slug)


def add_org(c, admin, name="Sales", **kw):
    r = c.post("/v1/admin/organizations", headers=admin, json={"name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def add_user(c, admin, slug, email, role="member", org=None, password="member-pass-1"):
    r = c.post("/v1/admin/users", headers=admin, json={"email": email, "name": email.split("@")[0], "password": password,
                                                       "role": role, "organization_id": org})
    assert r.status_code == 201, r.text
    return r.json(), login(c, email, password, tenant=slug)


def offer_for(c, slug, adults=1):
    r = c.get("/v1/search/transport", headers={"X-Tenant-Slug": slug},
              params={"kind": "train", "origin": "Delhi", "destination": "Jaipur", "date": D1, "adults": adults})
    assert r.status_code == 200, r.text
    return r.json()["offers"][0]


def book(c, headers, offer):
    co = open_checkout(c, headers, [offer])
    assert co.status_code == 201, co.text
    r = confirm(c, headers, co.json()["id"])
    assert r.status_code == 200, r.text
    return r.json()["bookings"][0]


# ---- platform / super admin ------------------------------------------------------------------

def test_super_admin_manages_tenants_and_is_separate_from_travellers(client):
    su = super_admin(client)
    me = client.get("/v1/auth/me", headers=su).json()
    assert me["role"] == "super_admin" and me["tenant"] is None
    # platform staff do not use traveller features
    assert client.post("/v1/agent/chat", headers=su, json={"message": "hi"}).status_code == 403
    tenant, admin = make_tenant(client, "acme")
    assert tenant["status"] == "active" and tenant["settings"]["self_signup"] is False
    slugs = [t["slug"] for t in client.get("/v1/platform/tenants", headers=su).json()]
    assert slugs == ["public", "acme"]
    assert client.post("/v1/platform/tenants", headers=su, json={
        "slug": "acme", "name": "Dup", "admin_email": "x@y.com", "admin_name": "x", "admin_password": PW}).status_code == 409
    assert client.post("/v1/platform/tenants", headers=su, json={
        "slug": "platform", "name": "Nope", "admin_email": "x@y.com", "admin_name": "x", "admin_password": PW}).status_code == 422
    stats = client.get("/v1/platform/stats", headers=su).json()
    assert stats["tenants"] == 2 and stats["users"] >= 1
    # a tenant admin is not a platform admin
    assert client.get("/v1/platform/tenants", headers=admin).status_code == 403


def test_super_admin_login_only_via_platform_slug(client):
    r = client.post("/v1/auth/login", json={"email": SUPER[0], "password": SUPER[1]})  # default tenant
    assert r.status_code == 401
    make_tenant(client, "acme")
    r = client.post("/v1/auth/login", json={"email": SUPER[0], "password": SUPER[1]}, headers={"X-Tenant-Slug": "acme"})
    assert r.status_code == 401


def test_signup_is_per_tenant_policy(client):
    ok = client.post("/v1/auth/register", json={"email": "p@x.com", "name": "P", "password": "password123"})
    assert ok.status_code == 201  # the default consumer tenant allows self-service
    make_tenant(client, "acme")
    r = client.post("/v1/auth/register", headers={"X-Tenant-Slug": "acme"},
                    json={"email": "p@x.com", "name": "P", "password": "password123"})
    assert r.status_code == 403 and r.json()["code"] == "signup_disabled"
    assert client.post("/v1/auth/register", headers={"X-Tenant-Slug": "nope"},
                       json={"email": "p@x.com", "name": "P", "password": "password123"}).status_code == 404


# ---- isolation ----------------------------------------------------------------------------------

def test_tenants_cannot_see_or_use_each_others_data(client):
    _, admin_a = make_tenant(client, "alpha")
    _, admin_b = make_tenant(client, "beta")
    _, alice = add_user(client, admin_a, "alpha", "same@x.com")
    _, bob = add_user(client, admin_b, "beta", "same@x.com")  # the same email may exist in two tenants
    offer_a = offer_for(client, "alpha")
    booking = book(client, alice, offer_a)

    # B cannot resolve, check out or cancel A's things
    assert client.get(f"/v1/offers/{offer_a['offer_id']}", headers={"X-Tenant-Slug": "beta"}).status_code == 404
    assert client.get(f"/v1/offers/{offer_a['offer_id']}", headers={"X-Tenant-Slug": "alpha"}).status_code == 200
    assert open_checkout(client, bob, [offer_a]).status_code == 404
    assert client.post(f"/v1/bookings/{booking['id']}/cancel", headers=bob).status_code == 404
    assert client.post(f"/v1/bookings/{booking['id']}/cancel", headers=admin_b).status_code == 404
    assert client.get("/v1/admin/bookings", headers=admin_b).json() == []
    assert [b["id"] for b in client.get("/v1/admin/bookings", headers=admin_a).json()] == [booking["id"]]
    assert {u["email"] for u in client.get("/v1/admin/users", headers=admin_b).json()} == {"admin@beta.com", "same@x.com"}
    # a token is bound to its own tenant; sending another tenant's header changes nothing
    assert client.get("/v1/auth/me", headers={**alice, "X-Tenant-Slug": "beta"}).json()["tenant"]["slug"] == "alpha"


# ---- roles ----------------------------------------------------------------------------------------

def test_role_scopes_tenant_admin_org_admin_member(client):
    _, admin = make_tenant(client, "acme")
    sales, ops = add_org(client, admin, "Sales"), add_org(client, admin, "Ops")
    _, oa = add_user(client, admin, "acme", "boss@acme.com", "org_admin", sales["id"])
    s1, m_sales = add_user(client, admin, "acme", "s1@acme.com", "member", sales["id"])
    o1, m_ops = add_user(client, admin, "acme", "o1@acme.com", "member", ops["id"])
    ta, _ = add_user(client, admin, "acme", "ta2@acme.com", "tenant_admin")

    assert client.get("/v1/admin/users", headers=m_sales).status_code == 403  # members have no admin access
    assert client.get("/v1/admin/audit", headers=oa).status_code == 403  # audit is tenant-admin only
    assert client.post("/v1/admin/organizations", headers=oa, json={"name": "X org"}).status_code == 403

    emails = {u["email"] for u in client.get("/v1/admin/users", headers=oa).json()}
    assert emails == {"boss@acme.com", "s1@acme.com"}  # only their own organization
    assert [o["name"] for o in client.get("/v1/admin/organizations", headers=oa).json()] == ["Sales"]

    # org admin: can add members to their org, nothing more
    r = client.post("/v1/admin/users", headers=oa, json={"email": "new@acme.com", "name": "n", "password": "member-pass-1"})
    assert r.status_code == 201 and r.json()["organization_id"] == sales["id"]
    up = client.post("/v1/admin/users", headers=oa, json={"email": "x@acme.com", "name": "n", "password": "member-pass-1", "role": "org_admin"})
    assert up.status_code == 403
    assert client.patch(f"/v1/admin/users/{o1['id']}", headers=oa, json={"name": "hijack"}).status_code == 404  # other org
    assert client.patch(f"/v1/admin/users/{ta['id']}", headers=oa, json={"is_active": False}).status_code == 404
    moved = client.patch(f"/v1/admin/users/{s1['id']}", headers=oa, json={"organization_id": ops["id"]})
    assert moved.status_code == 403
    assert client.patch(f"/v1/admin/users/{s1['id']}", headers=oa, json={"role": "org_admin"}).status_code == 403

    # bookings are visible by scope
    book(client, m_sales, offer_for(client, "acme"))
    book(client, m_ops, offer_for(client, "acme"))
    assert len(client.get("/v1/admin/bookings", headers=admin).json()) == 2
    assert len(client.get("/v1/admin/bookings", headers=oa).json()) == 1
    assert len(client.get(f"/v1/admin/bookings?organization_id={ops['id']}", headers=admin).json()) == 1
    assert len(client.get("/v1/bookings", headers=m_sales).json()) == 1  # a member only ever sees their own


def test_admins_can_cancel_within_scope_and_it_is_audited(client):
    _, admin = make_tenant(client, "acme")
    sales, ops = add_org(client, admin, "Sales"), add_org(client, admin, "Ops")
    _, oa = add_user(client, admin, "acme", "boss@acme.com", "org_admin", sales["id"])
    _, m_sales = add_user(client, admin, "acme", "s1@acme.com", "member", sales["id"])
    _, m_ops = add_user(client, admin, "acme", "o1@acme.com", "member", ops["id"])
    b_sales, b_ops = book(client, m_sales, offer_for(client, "acme")), book(client, m_ops, offer_for(client, "acme"))
    assert client.post(f"/v1/bookings/{b_ops['id']}/cancel", headers=oa).status_code == 404  # other organization
    assert client.post(f"/v1/bookings/{b_sales['id']}/cancel", headers=oa).json()["status"] == "cancelled"
    assert client.post(f"/v1/bookings/{b_ops['id']}/cancel", headers=admin).json()["status"] == "cancelled"
    actions = [e["action"] for e in client.get("/v1/admin/audit", headers=admin).json()]
    assert actions.count("booking.cancel_by_admin") == 2


def test_cannot_demote_or_disable_yourself(client):
    _, admin = make_tenant(client, "acme")
    me = client.get("/v1/auth/me", headers=admin).json()
    for body in ({"role": "member"}, {"is_active": False}):
        r = client.patch(f"/v1/admin/users/{me['id']}", headers=admin, json=body)
        assert r.status_code == 403 and r.json()["code"] == "cannot_modify_self"


def test_org_admin_requires_an_organization(client):
    _, admin = make_tenant(client, "acme")
    r = client.post("/v1/admin/users", headers=admin, json={"email": "a@acme.com", "name": "a", "password": "member-pass-1", "role": "org_admin"})
    assert r.status_code == 422 and r.json()["code"] == "organization_required"
    r = client.post("/v1/admin/users", headers=admin, json={"email": "a@acme.com", "name": "a", "password": "member-pass-1",
                                                            "organization_id": "no-such-org"})
    assert r.status_code == 404


# ---- travel policy ---------------------------------------------------------------------------------

def test_organization_travel_policy_blocks_expensive_trips(client):
    _, admin = make_tenant(client, "acme")
    strict = add_org(client, admin, "Strict", max_trip_amount=100, policy_currency="INR")  # 100 rupees
    loose = add_org(client, admin, "Loose", max_trip_amount=1_000_000, policy_currency="INR")
    assert strict["max_trip_amount"] == 100 and strict["policy_currency"] == "INR"
    _, m_strict = add_user(client, admin, "acme", "s@acme.com", "member", strict["id"])
    _, m_loose = add_user(client, admin, "acme", "l@acme.com", "member", loose["id"])
    offer = offer_for(client, "acme")
    r = open_checkout(client, m_strict, [offer])
    assert r.status_code == 422 and r.json()["code"] == "policy_violation"
    assert open_checkout(client, m_loose, [offer]).status_code == 201
    # tenant admin raises the limit, the same member can now book
    assert client.patch(f"/v1/admin/organizations/{strict['id']}", headers=admin, json={"max_trip_amount": 1_000_000}).json()["max_trip_amount"] == 1_000_000
    assert open_checkout(client, m_strict, [offer]).status_code == 201
    # clearing the limit
    cleared = client.patch(f"/v1/admin/organizations/{strict['id']}", headers=admin, json={"max_trip_amount": None})
    assert cleared.json()["max_trip_amount"] is None
    # a limit needs a currency
    assert client.post("/v1/admin/organizations", headers=admin, json={"name": "Bad", "max_trip_amount": 5}).status_code == 422


def test_suspended_organization_cannot_check_out(client):
    _, admin = make_tenant(client, "acme")
    org = add_org(client, admin, "Sales")
    _, m = add_user(client, admin, "acme", "s@acme.com", "member", org["id"])
    offer = offer_for(client, "acme")
    client.patch(f"/v1/admin/organizations/{org['id']}", headers=admin, json={"status": "suspended"})
    r = open_checkout(client, m, [offer])
    assert r.status_code == 403 and r.json()["code"] == "organization_suspended"


# ---- suspension, deactivation, limits ---------------------------------------------------------------------

def test_suspending_a_tenant_locks_everyone_out_immediately(client):
    su = super_admin(client)
    tenant, admin = make_tenant(client, "acme")
    _, member = add_user(client, admin, "acme", "m@acme.com")
    assert client.get("/v1/auth/me", headers=member).status_code == 200
    client.patch(f"/v1/platform/tenants/{tenant['id']}", headers=su, json={"status": "suspended"})
    for h in (member, admin):  # existing tokens stop working at once
        r = client.get("/v1/auth/me", headers=h)
        assert r.status_code == 403 and r.json()["code"] == "tenant_suspended"
    assert client.post("/v1/auth/login", headers={"X-Tenant-Slug": "acme"},
                       json={"email": "m@acme.com", "password": "member-pass-1"}).status_code == 403
    assert client.get("/v1/search/transport", headers={"X-Tenant-Slug": "acme"},
                      params={"kind": "bus", "origin": "Delhi", "destination": "Goa", "date": D1}).status_code == 403
    assert client.get("/v1/search/transport", params={"kind": "bus", "origin": "Delhi", "destination": "Goa", "date": D1}).status_code == 200
    client.patch(f"/v1/platform/tenants/{tenant['id']}", headers=su, json={"status": "active"})
    assert client.get("/v1/auth/me", headers=member).status_code == 200


def test_disabled_user_is_locked_out_immediately(client):
    _, admin = make_tenant(client, "acme")
    u, member = add_user(client, admin, "acme", "m@acme.com")
    client.patch(f"/v1/admin/users/{u['id']}", headers=admin, json={"is_active": False})
    r = client.get("/v1/auth/me", headers=member)
    assert r.status_code == 403 and r.json()["code"] == "account_disabled"
    assert client.post("/v1/auth/login", headers={"X-Tenant-Slug": "acme"},
                       json={"email": "m@acme.com", "password": "member-pass-1"}).status_code == 403


def test_plan_limits(client):
    su = super_admin(client)
    tenant, admin = make_tenant(client, "tiny", max_users=2, max_orgs=1)
    add_org(client, admin, "One")
    over_org = client.post("/v1/admin/organizations", headers=admin, json={"name": "Two"})
    assert over_org.status_code == 403 and over_org.json()["code"] == "plan_limit"
    u, _ = add_user(client, admin, "tiny", "a@tiny.com")  # admin + 1 = 2 users
    over = client.post("/v1/admin/users", headers=admin, json={"email": "b@tiny.com", "name": "b", "password": "member-pass-1"})
    assert over.status_code == 403 and over.json()["code"] == "plan_limit"
    client.patch(f"/v1/admin/users/{u['id']}", headers=admin, json={"is_active": False})  # frees a seat
    assert client.post("/v1/admin/users", headers=admin, json={"email": "b@tiny.com", "name": "b", "password": "member-pass-1"}).status_code == 201
    # the platform raises the limit
    assert client.patch(f"/v1/platform/tenants/{tenant['id']}", headers=su, json={"max_users": 10}).json()["max_users"] == 10


# ---- per-tenant configuration ----------------------------------------------------------------------------------

def test_tenant_supplier_allow_list_and_branding(client):
    _, admin = make_tenant(client, "acme")
    cfg = client.get("/v1/tenant/config", headers={"X-Tenant-Slug": "acme"}).json()
    assert cfg["slug"] == "acme" and cfg["branding"]["display_name"] == "Acme Corp" and cfg["self_signup"] is False
    r = client.patch("/v1/admin/tenant", headers=admin, json={"branding": {"display_name": "Acme Travel", "primary_color": "#112233"}})
    assert r.status_code == 200
    cfg = client.get("/v1/tenant/config", headers={"X-Tenant-Slug": "acme"}).json()
    assert cfg["branding"]["display_name"] == "Acme Travel" and cfg["branding"]["primary_color"] == "#112233"
    assert client.patch("/v1/admin/tenant", headers=admin, json={"branding": {"logo_url": "http://insecure"}}).status_code == 422
    assert client.patch("/v1/admin/tenant", headers=admin, json={"providers": ["skyscanner"]}).status_code == 422
    # allow-list narrows what this tenant can search
    assert offer_for(client, "acme")["provider"] == "demo"
    client.patch("/v1/admin/tenant", headers=admin, json={"providers": []})
    r = client.get("/v1/search/transport", headers={"X-Tenant-Slug": "acme"},
                   params={"kind": "bus", "origin": "Delhi", "destination": "Goa", "date": D1})
    assert r.json()["offers"] == []
    assert offer_for(client, "public")["provider"] == "demo"  # other tenants unaffected
    client.patch("/v1/admin/tenant", headers=admin, json={"providers": None})  # null = all platform suppliers
    assert offer_for(client, "acme")["provider"] == "demo"
    assert client.patch("/v1/admin/tenant", headers=admin, json={"self_signup": True}).json()["settings"]["self_signup"] is True


# ---- audit ------------------------------------------------------------------------------------------------------

def test_audit_log_records_admin_actions_without_secrets(client):
    su = super_admin(client)
    tenant, admin = make_tenant(client, "acme")
    org = add_org(client, admin, "Sales")
    u, _ = add_user(client, admin, "acme", "m@acme.com", org=org["id"])
    client.patch(f"/v1/admin/users/{u['id']}", headers=admin, json={"password": "brand-new-secret-9"})
    events = client.get("/v1/admin/audit", headers=admin).json()
    actions = {e["action"] for e in events}
    assert {"tenant.create", "org.create", "user.create", "user.update"} <= actions
    assert "brand-new-secret-9" not in str(events) and PW not in str(events)
    pw_event = next(e for e in events if e["action"] == "user.update")
    assert pw_event["detail"]["password"] == "reset" and pw_event["actor_email"] == "admin@acme.com"
    assert client.get(f"/v1/platform/audit?tenant_id={tenant['id']}", headers=su).status_code == 200
    # tenants only ever see their own trail
    _, other = make_tenant(client, "beta")
    assert all("acme" not in str(e) for e in client.get("/v1/admin/audit", headers=other).json())


# ---- bootstrap & config ------------------------------------------------------------------------------------------

def test_bootstrap_is_idempotent(client):
    from app.tenancy import ensure_platform

    async def twice():
        async with SessionLocal() as db:
            await ensure_platform(db)
            await ensure_platform(db)

    asyncio.run(twice())
    su = super_admin(client)
    assert [t["slug"] for t in client.get("/v1/platform/tenants", headers=su).json()] == ["public"]


def test_prod_requires_strong_superadmin_password():
    base = dict(_env_file=None, app_env="prod", providers="duffel", duffel_token="x", secret_key="k" * 40,
                payment_gateway="razorpay", razorpay_key_id="k", razorpay_key_secret="s")
    with pytest.raises(RuntimeError, match="SUPERADMIN_PASSWORD"):
        Settings(**base, superadmin_email="a@b.com", superadmin_password="short").validate_runtime()
    Settings(**base, superadmin_email="a@b.com", superadmin_password="long-enough-pass-1").validate_runtime()


# ---- background worker ---------------------------------------------------------------------------------------------

def test_worker_cleans_expired_offers_and_flags_stale_bookings(client):
    _, admin = make_tenant(client, "acme")
    _, member = add_user(client, admin, "acme", "m@acme.com")
    fresh = offer_for(client, "acme")
    booking = book(client, member, offer_for(client, "acme"))

    async def work():
        async with SessionLocal() as db:
            old = OfferSnapshot(tenant_id=(await db.get(Booking, booking["id"])).tenant_id, provider="demo", kind="train", price=1,
                                currency="INR", offer={}, raw={}, expires_at=now() - timedelta(hours=3))
            db.add(old)
            await db.commit()
        async with SessionLocal() as db:
            deleted = await cleanup_offers(db)
        async with SessionLocal() as db:
            b = await db.get(Booking, booking["id"])
            b.status, b.created_at = "pending", now() - timedelta(hours=1)  # a crash left this behind
            await db.commit()
        async with SessionLocal() as db:
            flagged = await flag_stale_pending(db)
        async with SessionLocal() as db:
            return deleted, flagged, (await db.get(Booking, booking["id"])).status, await db.get(OfferSnapshot, fresh["offer_id"])

    deleted, flagged, status, kept = asyncio.run(work())
    assert deleted >= 1 and kept is not None  # unexpired offers survive
    assert flagged["bookings"] == 1 and status == "needs_attention"
    visible = client.get("/v1/admin/bookings?status=needs_attention", headers=admin).json()  # ops can see it
    assert [b["id"] for b in visible] == [booking["id"]]
