# Architecture

## 1. What the system does

Universal Travel Ecosystem lets a person search and book flights, trains, buses and hotels, plan a whole trip in one tap, and do all of it from four places: a **web app**, a **mobile app**, an in-app **chat agent**, and **WhatsApp**. All four talk to one backend API.

```
 Mobile app (Expo) ─┐
 Web app (React) ───┼──► Caddy (HTTPS, static web) ──► FastAPI  /v1/*
 WhatsApp (Meta) ───┘   webhook ───────────────────────►   │
                                                           ├── services/search    ──┐ fan-out, concurrent
                                                           ├── services/planner     │
                                                           ├── services/checkout    ├──► providers/duffel   ─► Duffel API
                                                           ├── services/agent ──► LLM│    providers/amadeus  ─► Amadeus (Enterprise)
                                                           ├── services/whatsapp    │    providers/demo      (synthetic)
                                                           └── services/payments ──┘──► Razorpay / demo gateway
                                                           │
                                                      PostgreSQL (Alembic migrations)
```

## 2. Layers and where the code lives

| Layer | Code | Responsibility |
|---|---|---|
| HTTP API | `backend/app/api/` | Thin routers: validate input, call a service, shape output. No business rules. |
| Services | `backend/app/services/` | Business logic: search fan-out, planning, the checkout saga, the agent loop, WhatsApp handling, payments. |
| Providers | `backend/app/providers/` | One adapter per supplier behind the `TravelProvider` interface. Nothing supplier-specific leaks past it. |
| Persistence | `backend/app/models.py`, `alembic/` | SQLAlchemy models and migrations. |
| Core | `backend/app/core/` | Settings and fail-fast validation, error types, money helpers, logging and metrics. |
| Clients | `web/`, `mobile/` | React and React Native apps. They hold no business rules: prices, availability and payment truth come from the server. |

## 3. Data model

| Table | Purpose | Key facts |
|---|---|---|
| `users` | Accounts | scrypt password hash. |
| `offer_snapshots` | What the user was shown | normalized `offer` (sent to clients), private `raw` supplier payload (never leaves the server), `price`, `expires_at`. |
| `checkouts` | One payment covering one or more bookings | `status`: created, processing, booked, failed. `payment_state`: none, authorized, captured, released, void_failed, capture_failed. |
| `bookings` | One supplier order each | `status`: pending, confirmed, failed, cancelled, needs_attention. Written as `pending` **before** the supplier call. |
| `agent_sessions` | Chat history per channel | Visible dialogue only (no tool traffic), plus seen WhatsApp message ids for de-duplication. |

Money is stored as **integer minor units** (paise, cents) with the currency alongside. Exponents follow ISO 4217 (JPY has 0, KWD has 3). Prices are always the **total for the whole party** that was searched.

## 4. The core flows

### 4.1 Search

1. Client calls `GET /v1/search/transport` (or `/hotels`).
2. The service asks **every enabled provider that supports that kind**, concurrently, each under a timeout.
3. A provider that times out, errors, or has an open circuit breaker contributes a **warning** instead of failing the search.
4. Results are grouped by currency, sorted by price, and stored as `offer_snapshots`. The client gets an opaque `offer_id` per offer.

### 4.2 Checkout (the important one)

Real trips need several bookings and one payment. The checkout is a saga:

```
POST /v1/checkouts {offer_ids}      validate offers, sum the price, open a gateway order
        │                           (customer pays in the gateway UI: money is HELD, not taken)
POST /v1/checkouts/{id}/confirm
        ├─ claim: created → processing (atomic; only one confirm can win)
        ├─ re-validate offers are unexpired and the traveller count matches
        ├─ gateway.authorize   verify signature, order, amount and status with the gateway
        ├─ for each offer:     write booking `pending`, call supplier, mark `confirmed`
        │     └─ if any fails: cancel the supplier orders already placed,
        │                      void the payment, checkout → failed
        └─ gateway.capture     only now is money actually taken → checkout → booked
```

Guarantees:

- **Never charged for a failed trip.** Capture happens last, after every supplier confirmed. A void (or the gateway's own timeout) releases the hold.
- **Safe to retry.** Confirming an already-booked checkout returns the same bookings (HTTP 200), so a dropped connection never double-books. The clients also keep the payment result so a customer is not asked to pay twice after a network failure.
- **No payment bypass.** There is no endpoint that books without a checkout.
- **Supplier orders are never auto-retried** (they are not idempotent at the supplier).
- **Traceability.** The booking row exists before the supplier is called, so a crash leaves a `pending` row rather than an invisible supplier order.

### 4.3 Chat agent and WhatsApp

The agent is a tool-calling loop against any OpenAI-compatible server (`LLM_BASE_URL`). Its tools are **read-only**: `search_transport`, `search_hotels`, `plan_trip`. It cannot book or take payment. It answers with options and a **Book link** that opens the web app, where the customer logs in and checks out normally.

Design choices that matter:

- **Prices and links are rendered by code from tool results**, never typed by the model, so the model cannot invent a price or a link. The model sees option numbers, not offer ids.
- **Tool output is data.** Supplier text (carrier or hotel names) goes into tool messages and the system prompt tells the model to ignore instructions found there. Read-only tools also bound the damage of a successful injection.
- **History keeps only the visible dialogue**, not tool traffic, so sessions stay small and carry little personal data.

WhatsApp adds: HMAC signature verification of Meta's webhook, an immediate `200` with the reply sent in a background task, de-duplication of redelivered message ids, and a per-sender rate cap (10 per minute) to bound LLM cost.

## 5. Resilience

| Failure | Behaviour |
|---|---|
| Supplier slow or down during search | Warning in the response; other suppliers' results still returned. |
| Supplier keeps failing | Circuit breaker opens (default after 5 failures, retry after 30s); calls fail fast with `unavailable`. |
| Supplier 5xx or timeout on a **search or GET** | Up to 2 retries with backoff and jitter. |
| Supplier 5xx on an **order** | No retry; booking fails; earlier bookings in the checkout are undone; payment voided. |
| Supplier 4xx (our request was bad) | Does not count against the breaker; surfaced as `provider_rejected`. |
| Fare changed between search and booking (Amadeus re-prices) | `price_changed` (409); no order is placed. |
| Payment declined or invalid signature | Nothing booked; `payment_failed` (402). |
| Capture fails after bookings confirmed | Bookings stand; `payment_state=capture_failed`; **CRITICAL** log for finance. |
| Cancelling a supplier order fails during compensation | Booking marked `needs_attention`; **CRITICAL** log for a human. |
| LLM server down | `agent_unavailable` (503); the rest of the app is unaffected. |

## 6. Security model

- Passwords: scrypt. Sessions: signed JWT (HS256) bearer tokens.
- Prices cannot be tampered with: clients send only an `offer_id`; the server uses its stored snapshot.
- Payment verification: HMAC signature, the order id stored on **our** server (not the client's), exact amount and currency, and gateway-reported status. A payment cannot be replayed against a different checkout.
- The supplier payload (`raw`) and secrets never reach clients. Settings use `SecretStr`.
- The server **refuses to start** in production with a weak or default `SECRET_KEY`, missing supplier or gateway credentials, or the fake demo supplier or gateway (unless explicitly allowed).
- Bookings, checkouts and chat sessions are scoped to the logged-in user (404, not 403, for other users' ids).
- Passenger date of birth is stored but never echoed back by the API.

Not yet done (see [OPERATIONS.md](OPERATIONS.md) known gaps): field-level encryption of passenger data, email verification and password reset, refresh tokens, shared-store rate limiting.

## 7. Key decisions and trade-offs

| Decision | Why | Cost |
|---|---|---|
| Opaque `offer_id` + server-side snapshots | Tamper-proof prices, expiry, supplier payload stays private | Extra table; needs periodic cleanup |
| Minor-unit integers | No float drift | Clients must format using the currency's exponent |
| Price = total for the party | Matches how suppliers price; avoids per-person rounding | Search must include party size |
| One payment per checkout, capture last | Real trips span suppliers; customers should never pay for a half-booked trip | More states to reason about (hence the saga) |
| Agent is read-only | Keeps payment and personal data out of chat; limits injection impact | Customer finishes in the app or web |
| In-process rate limiting | Zero dependencies | Per process: use Redis before running several replicas |
| Async everywhere | Concurrent supplier calls | Slightly more complex tests |

## 8. Extension points

- **A new supplier**: implement `TravelProvider` (see [DEVELOPMENT.md](DEVELOPMENT.md)). Search, planning, the agent and checkout pick it up automatically.
- **A new payment gateway**: implement `PaymentGateway` in `services/payments.py`.
- **A new channel** (Telegram, SMS): call `services/agent.chat(...)` with a new channel name and render with `render_text`, as `services/whatsapp.py` does.
