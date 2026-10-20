# Scaling and enterprise architecture

> **Read this first.** None of the numbers or limits below have been load-tested. This document describes how the system is built to scale, which parts are done, which are not, and where it will hurt first. Treat it as a plan to verify, not a benchmark.

## 1. What is built for scale today

| Concern | Status |
|---|---|
| **Stateless API.** Any replica can serve any request; no sticky sessions. Login is a signed token, state is in Postgres | ✅ |
| **Shared rate limiting.** Set `REDIS_URL` and all replicas share counters. Fails open if Redis is down | ✅ (tested with a fake Redis, not a real one) |
| **Separate background worker** (`python -m app.worker`): offer cleanup, stuck-booking detection. Scales independently, safe to run several | ✅ |
| **Async I/O end to end**; supplier searches run concurrently with timeouts, retries, circuit breakers | ✅ |
| **Multi-tenant isolation**, per-tenant supplier allow-lists, plan limits, per-tenant rate-limit keys | ✅ (see [SAAS.md](SAAS.md)) |
| **Migrations** in CI (a test fails if models and migrations drift); the API container migrates on start | ✅ |
| **Health, readiness, Prometheus metrics, JSON logs with request ids** | ✅ |
| Horizontal scaling in Compose: `docker compose up -d --scale api=3` | ⚠️ written, not run; see caveat below |

**Caveat: load balancing in Compose.** The bundled Caddy proxies to the hostname `api`. With several replicas Docker DNS returns several addresses, but Caddy resolves upstream hostnames at start-up and may not pick up replicas added later. For real multi-replica use, put a proper load balancer in front (a cloud load balancer or an ingress controller) or configure Caddy's dynamic upstreams and verify it. This has not been tested.

## 2. Target architecture

```
                 ┌─────────────── CDN / WAF (static web, DDoS, TLS) ───────────────┐
 Web · Mobile ───┤                                                                  │
 WhatsApp ───────┘                                                                  ▼
                                                                          Load balancer / ingress
                                                                                   │
                                          ┌────────────────────────────────────────┼───────────────┐
                                          ▼                                        ▼               ▼
                                     API replica ×N (stateless, autoscaled)   Worker ×M      Admin traffic
                                          │   │                                    │
              ┌───────────────────────────┘   └─────────────┐                      │
              ▼                                             ▼                      ▼
        Redis (rate limits,                      PostgreSQL primary ◄──── PgBouncer (connection pooling)
        [later] offer cache,                       │        │
         supplier concurrency)                     │        └──► read replica(s): admin lists, reports
                                                   └──► backups + PITR, multi-AZ standby

 API replicas ──► suppliers (Duffel, …), payment gateway, LLM server (own autoscaled pool), WhatsApp Graph API
```

## 3. Where it will hurt first (in order)

1. **Offer snapshots write amplification.** Every search stores one row per result, including the private supplier payload. At volume this is the biggest write load and the fastest-growing table, even though rows expire in minutes. *Fix:* move snapshots to Redis with a TTL (they are short-lived by nature), keep only the booked offer in Postgres. The code isolates this behind `services/search.persist` and the snapshot lookups in `services/checkout.py`, so the change is local. Until then the worker deletes expired rows.
2. **Supplier quotas and latency.** Suppliers rate-limit you, and a search fans out to every enabled supplier. *Fix:* a cache for identical searches (short TTL, keyed by route, date and party size, not by tenant), per-supplier concurrency limits shared across replicas in Redis, and per-tenant request budgets.
3. **Database connections.** Each API replica holds up to 10 pooled connections, and Postgres has a small default connection limit. *Fix:* PgBouncer in transaction mode before going past a handful of replicas.
4. **Checkout latency.** Bookings are sequential supplier calls inside one request. Fine at moderate volume; for slow suppliers move confirmation to a queue and let clients poll or receive a push. The state machine already supports this (the booking is `pending` before the supplier call).
5. **LLM cost and latency.** The chat agent is the most expensive thing per request. Run the model server on its own autoscaled pool, cap per-user and per-tenant usage (per-sender WhatsApp and the shared rate limiter exist; per-tenant LLM budgets do not).
6. **The noisy neighbour.** One tenant's burst can still slow everyone through shared database and supplier capacity. Per-tenant rate-limit keys help; per-tenant quotas on searches and checkouts are not built.

## 4. A staged path

| Stage | When | What to do |
|---|---|---|
| **1. Single host** | Pilot, first tenants | `docker compose up` as shipped: Postgres, API, worker, Redis, web. Back up Postgres nightly |
| **2. A few replicas** | Steady real traffic | Managed Postgres (backups, PITR, multi-AZ), 2–3 API replicas behind a real load balancer, Redis (managed), worker ×1, metrics and alerts wired |
| **3. Growth** | Search volume or tenant count grows | PgBouncer, snapshot store moved to Redis, search cache, per-supplier concurrency limits, read replica for admin and reporting queries, autoscaling on CPU and request latency |
| **4. Enterprise tier** | Large customers with contractual demands | SSO and SCIM, data-residency regions, optional schema-per-tenant or database-per-tenant for the largest tenants, dedicated worker pools, customer-managed keys |
| **5. Multi-region** | Global latency or DR requirements | Regional stacks with tenant homing (a tenant lives in one region), a global control plane for tenant metadata, regional suppliers and gateways |

## 5. Tenancy model options

Today's **pooled** model (shared tables with `tenant_id`) is the cheapest and simplest to operate. Alternatives, and when they apply:

| Model | Isolation | Cost | When |
|---|---|---|---|
| **Pooled** (today) | Application-enforced (every query filtered); add Postgres RLS for defence in depth | Lowest | Most tenants |
| Schema per tenant | Stronger, per-tenant backup/restore | Migrations run per schema | Mid-size customers with compliance asks |
| Database per tenant | Strongest, per-tenant keys and regions | Highest operational cost | A few very large or regulated customers |

A sensible end state is hybrid: pooled for the long tail, dedicated databases for the few that pay for it. The `tenant_id` on every row keeps that migration possible.

**Row-level security (recommended next):** in Postgres, enable RLS on tenant tables and set `app.tenant_id` per transaction (`SET LOCAL`), so even a query that forgets its tenant filter returns nothing for other tenants. Not enabled yet; the test suite is the only guard today.

## 6. Reliability and operations

- **Suggested SLOs** (to be measured, not promised): search p95 under 3 s (suppliers dominate), checkout confirm success above 99% excluding supplier declines, API availability 99.9%.
- **Alerts:** see [OPERATIONS.md](OPERATIONS.md#4-monitoring). Add queue/worker lag once confirmation moves to a queue.
- **Backups:** Postgres with point-in-time recovery; test restores. Bookings and checkouts are the system of record for money.
- **Deploys:** the API container migrates on start, which is safe for one replica; with several, run migrations as a separate one-off job before rolling out, and keep migrations backwards-compatible (add columns before using them).
- **Tracing:** request ids exist. Add OpenTelemetry tracing across API → supplier → gateway calls before debugging production at scale.
- **Metric cardinality:** do not add `tenant` as a metric label for thousands of tenants; use logs or a top-N approach.

## 7. Enterprise buyer checklist

| Ask | Status |
|---|---|
| Tenant isolation, audit log, role-based access | ✅ built (pooled model) |
| SSO (SAML/OIDC), SCIM user provisioning | ❌ |
| MFA | ❌ |
| Data residency, per-tenant encryption keys | ❌ |
| Audit log export and retention controls | ❌ (read via API only) |
| SLA, status page, incident process | ❌ (process, not code) |
| Penetration test, SOC 2 / ISO 27001 | ❌ |
| Billing and usage metering | ❌ |
| Admin console UI | ❌ (API only) |
