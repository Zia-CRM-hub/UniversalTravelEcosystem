# SaaS model: platform, tenants, organizations

Universal Travel Ecosystem is multi-tenant. One deployment serves many customers ("tenants"), each isolated from the others, each able to run its own organizations and users.

```
PLATFORM                      you, the operator           role: super_admin   (belongs to no tenant)
  └── TENANT                  a customer company / brand  role: tenant_admin  (e.g. "Acme Corp")
        └── ORGANIZATION      a department or client      role: org_admin     (e.g. "Acme Sales")
              └── USER        a traveller                 role: member
```

The default tenant `public` serves self-service consumers (anyone can sign up). Business customers get their own tenant, where staff are added by invitation.

## Who can do what

| Capability | super_admin | tenant_admin | org_admin | member |
|---|:-:|:-:|:-:|:-:|
| Create, suspend, reactivate tenants; set plan limits (users, organizations) | ✅ | | | |
| Create a tenant's first admin | ✅ | | | |
| Platform stats and platform-wide audit log | ✅ | | | |
| Tenant branding, sign-up policy, supplier allow-list | | ✅ | | |
| Create and edit organizations and their **travel policy** | | ✅ | | |
| Create users and set roles (member, org admin, tenant admin) | | ✅ | | |
| Add members **to their own organization** | | ✅ | ✅ | |
| Disable users, reset passwords | | ✅ | own org's members | |
| See all bookings and the tenant audit log | | ✅ | | |
| See and cancel bookings of their organization | | ✅ | ✅ | |
| Search, plan, use the AI concierge, book and cancel **their own** trips | | ✅ | ✅ | ✅ |
| Use traveller features | ❌ (platform staff have no tenant) | ✅ | ✅ | ✅ |

Rules enforced in code ([backend/app/api/admin.py](../backend/app/api/admin.py), [platform.py](../backend/app/api/platform.py)):

- Out-of-scope ids return **404, never 403**, so a caller cannot even confirm that another tenant's or organization's record exists.
- You cannot demote or disable **your own** account.
- An org admin must belong to an organization; org admins can only create and manage `member` accounts inside their own organization and cannot promote anyone.
- Plan limits (`max_users`, `max_orgs`) are enforced on creation and on re-activating a user.
- Every admin action writes an **audit event** in the same transaction as the change. Passwords are never logged (a reset is recorded as `"password": "reset"`).

## How a request finds its tenant

| Request | Tenant comes from |
|---|---|
| Public: search, planner, offer lookup, login, sign-up, `GET /v1/tenant/config` | `X-Tenant-Slug` header, else the default tenant |
| Authenticated | The logged-in user's own tenant. **The header is ignored.** The token carries the tenant id and it is checked against the user on every request |
| Super admin login | `X-Tenant-Slug: platform` (reserved; only super admins authenticate there) |

The same email address can exist in two tenants. A suspended tenant returns `403 tenant_suspended` on every route, including for sessions that were already logged in: the tenant and user are re-checked on **every request**, so suspension and user-disabling take effect immediately, not when a token expires.

## Isolation: how tenants are kept apart

This is a **pooled** model: one database, every business row carries a `tenant_id`.

- Tables with `tenant_id`: users, organizations, offer snapshots, checkouts, bookings, chat sessions, audit events.
- Every query is filtered by the caller's tenant (and organization for org admins). There is a test for each boundary ([backend/tests/test_tenancy.py](../backend/tests/test_tenancy.py)): offers, checkouts, bookings, cancellations, user lists and the audit log are all verified unreachable across tenants.
- **Offers belong to a tenant.** A search result's id cannot be used to check out or be viewed from another tenant.
- Each tenant sees only the suppliers in its **allow-list** (a subset of what the platform enabled). The tenant admin sets it; unknown suppliers are rejected.
- Rate limiting is keyed per tenant and client IP, so one tenant's traffic does not exhaust another's budget.

Not done yet (planned hardening, see [SCALING.md](SCALING.md)): Postgres row-level security as a second line of defence, so a missed `WHERE` clause in future code cannot leak data.

## Per-tenant configuration

Set by the tenant admin (`PATCH /v1/admin/tenant`) or the super admin (`PATCH /v1/platform/tenants/{id}`):

| Setting | Meaning |
|---|---|
| `branding.display_name`, `primary_color`, `logo_url` (https only) | Served publicly by `GET /v1/tenant/config` so the web and mobile apps can white-label themselves |
| `self_signup` | Whether strangers can register into this tenant (off for business tenants) |
| `providers` | Supplier allow-list. `null` = every supplier the platform enabled; `[]` = none |
| `max_users`, `max_orgs` (super admin only) | Plan limits |

## Organization travel policy

An organization can set `max_trip_amount` and `policy_currency` (major units, e.g. 50,000 INR). When one of its members opens a checkout above that, the server answers `422 policy_violation` and nothing is held. A suspended organization cannot check out at all (`403 organization_suspended`). Only tenant admins edit policy; org admins cannot raise their own limit.

## Onboarding a new customer (runbook)

1. **Bootstrap the platform once.** Set `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` (12+ characters in production) and start the server. It creates the default tenant and your super admin. Log in with header `X-Tenant-Slug: platform`.
2. **Create the tenant:** `POST /v1/platform/tenants` with `slug`, `name`, plan limits and the first admin's email and password. Share the credentials securely and ask them to change the password.
3. **The tenant admin logs in** (header `X-Tenant-Slug: <slug>`), then:
   - sets branding and the supplier allow-list (`PATCH /v1/admin/tenant`),
   - creates organizations and their travel policy (`POST /v1/admin/organizations`),
   - adds org admins and members (`POST /v1/admin/users`).
4. **Point the customer's apps at the tenant:** build the web app with `VITE_TENANT=<slug>` and the mobile app with `extra.tenant` in `app.json`. For a customer-specific domain, add it to the [Caddyfile](../web/Caddyfile) and build the web app for it.
5. **Offboarding:** suspend the tenant (`status: "suspended"`). All access stops immediately and data is retained.

```bash
# example: onboard a customer
curl -s -X POST $API/v1/platform/tenants -H "Authorization: Bearer $SUPER" -H "Content-Type: application/json" \
  -d '{"slug":"acme","name":"Acme Corp","max_users":100,"max_orgs":10,
       "admin_email":"admin@acme.com","admin_name":"Acme Admin","admin_password":"a-long-initial-password"}'
```

## What is NOT part of the SaaS model yet

Be clear about these before selling it:

| Gap | Today |
|---|---|
| **Admin console UI** | Admin and platform features exist as **API only**. The web and mobile apps have no admin screens yet. (Use the API docs at `/docs`, or build the console.) |
| Billing, subscriptions, invoicing | None. Plan limits exist; charging for them does not |
| SSO (SAML/OIDC), SCIM provisioning | None (email and password only). Enterprise buyers will ask |
| Per-tenant supplier credentials (bring-your-own Duffel account) | Suppliers and the payment gateway are **platform-wide**. A tenant can only restrict which of the platform's suppliers it uses |
| Per-tenant payment gateway or merchant account | Platform-wide |
| Per-tenant WhatsApp number | One number, served by the default tenant |
| Per-tenant data export and deletion, retention policies | None |
| Business-travel workflows (approval chains, cost centres, invoices) | Only the single-trip spend limit exists |
| Database row-level security, per-tenant encryption keys | Not enabled |
| Custom roles and fine-grained permissions | Four fixed roles |
