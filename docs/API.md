# API reference

The exact, machine-readable contract is [openapi.json](openapi.json) (also served live at `/docs` when running the backend directly). This page explains the conventions and the flows.

## Conventions

- **Base path:** all business endpoints are under `/v1`. Health checks are at `/health/*`.
- **Auth:** `Authorization: Bearer <token>` from `/v1/auth/login` or `/register`. Endpoints marked 🔒 require it. Search, plan and offer lookup are public.
- **Money:** integers in **minor units** with a `currency`. `283000` INR is 2,830.00 INR. The number of decimals depends on the currency (JPY 0, KWD 3). A `price` is always the **total for `pax` travellers**.
- **Dates:** `YYYY-MM-DD`. Times are as returned by the supplier, in the local time of the airport or station.
- **Errors:** every error is `{"detail": "<human message>", "code": "<stable_code>"}`. Branch on `code`, show `detail`. Validation errors use `code: "validation_error"`.
- **Request tracing:** send `X-Request-ID` (optional); every response echoes one.
- **Rate limiting:** `429` with `Retry-After` (per client IP, default 120/min; health and metrics exempt).

## Endpoints

### Auth

| | |
|---|---|
| `POST /v1/auth/register` | `{email, name, password (8+)}` → `201 {access_token}`. `409 email_taken`. |
| `POST /v1/auth/login` | `{email, password}` → `{access_token}`. `401 bad_credentials`. |
| `GET /v1/auth/me` 🔒 | → `{id, email, name}` |

### Search (public)

`GET /v1/search/transport?kind=flight|train|bus&origin=Delhi&destination=Goa&date=2026-11-01&adults=2`
`GET /v1/search/hotels?city=Goa&check_in=2026-11-01&check_out=2026-11-05&guests=2&stars_min=3`

```json
{
  "offers": [
    { "offer_id": "620fe750…", "provider": "demo", "kind": "train",
      "price": 228000, "currency": "INR", "pax": 2, "expires_at": "2026-10-19T15:09:06Z",
      "carrier": "Metro Link", "number": "ME5575", "origin": "Delhi", "destination": "Jaipur",
      "departure": "2026-10-29T14:05:00", "arrival": "2026-10-29T22:36:00", "duration_min": 511, "stops": 0 }
  ],
  "warnings": [ { "provider": "duffel", "problem": "timeout" } ]
}
```

- Sorted by currency, then price. `warnings` lists suppliers that did not answer; results may be incomplete.
- Hotel offers have `name, stars, rating, check_in, check_out, nights, price_per_night, amenities` instead of the transport fields. Hotel `price` is for the whole stay and party.
- Errors: `422 same_place | past_date | bad_dates | validation_error`.

### Offers and places (public)

- `GET /v1/offers/{offer_id}` → the offer as shown in search. `404 offer_not_found`, `410 offer_expired`. Used to open hand-off links from chat and WhatsApp.
- `GET /v1/places?q=goa` → OpenStreetMap suggestions `[{name, lat, lon, city}]`. `502 places_unavailable`.

### Trip planner (public)

`POST /v1/trips/plan` `{origin, destination, start, end, travelers (1-9), mode, budget?, stars?}`
`budget` is in **major units** (rupees, not paise).

Returns `{currency, outbound, return, hotel, travelers, total, itinerary[], warnings[]}`, where the three offers are bookable and `total` is in minor units. Errors: `404 no_transport | no_hotels`, `422 over_budget | mixed_currency | bad_dates`.

### Checkout 🔒 (how every booking is made)

**Step 1: open a checkout**

`POST /v1/checkouts` `{offer_ids: [..1-5 ids..]}` → `201`

```json
{ "id": "c6b7e857…", "status": "created", "amount": 3386000, "currency": "INR",
  "gateway": "razorpay", "gateway_order_id": "order_Nx…", "gateway_key_id": "rzp_test_…",
  "offers": [ … ] }
```

All offers must share a currency and traveller count (`422 mixed_currency | mixed_party`). Errors: `404 offer_not_found`, `410 offer_expired`, `503 provider_disabled`, `502 payment_unavailable`.

**Between the steps** (Razorpay only): open Razorpay Checkout with `gateway_key_id`, `gateway_order_id`, `amount`, `currency`. On success it returns `razorpay_payment_id` and `razorpay_signature`. With `gateway: "demo"` skip this.

**Step 2: confirm**

`POST /v1/checkouts/{id}/confirm`

```json
{ "payment": { "payment_id": "pay_…", "signature": "…" },
  "passengers": [ { "title": "ms", "gender": "f", "given_name": "Asha", "family_name": "Rao", "born_on": "1990-05-17" } ],
  "contact": { "email": "asha@example.com", "phone": "+919876543210" } }
```

- `payment` is required by real gateways and ignored by the demo gateway.
- One passenger per traveller in the offers (`pax`). Names must match the travel document. Phone is E.164.
- `200 {checkout_id, status: "booked", bookings: [...]}`. **Confirming again returns the same bookings**, so retry freely after a network error.

| Error | Meaning |
|---|---|
| `402 payment_failed` | Payment could not be verified. Nothing booked. |
| `409 price_changed` | Supplier re-priced. Nothing booked; search again. |
| `409 in_progress` | Another confirm for this checkout is running. |
| `409 checkout_failed` | This checkout already failed (the `detail` says why). Open a new one. |
| `410 offer_expired` | An offer expired before confirmation. Not charged. |
| `422 passenger_count` / `provider_rejected` | Wrong number of travellers / the supplier refused (e.g. bad passport data). |
| `502 provider_error` / `503 provider_unavailable` | Supplier failed or is down. Earlier bookings undone, payment released. |

A booking is only ever `confirmed` after the supplier confirms it, and money is captured only after **all** of a checkout's bookings are confirmed.

### Bookings 🔒

- `GET /v1/bookings` → the user's bookings (excluding in-flight `pending`), newest first. Passenger names only; no dates of birth.
- `POST /v1/bookings/{id}/cancel` → cancels at the supplier, then refunds that booking's share of the payment. `404` for someone else's booking, `409 not_cancellable` unless it is `confirmed`, `502/503` if the supplier cannot cancel (the booking stays confirmed).

Booking `status`: `pending`, `confirmed`, `failed`, `cancelled`, `needs_attention` (a human must check the supplier order).

### Chat agent 🔒

- `POST /v1/agent/chat` `{message}` → `{reply, cards}`. `cards` are `{"type":"offer","n":1,"offer":{…},"link":"…"}` or `{"type":"trip","total":…,"currency":…,"offers":[…],"link":"…"}`. `503 agent_unavailable` if no LLM is configured or reachable.
- `DELETE /v1/agent/session` → clears the conversation (`204`).

### WhatsApp (called by Meta, not by apps)

- `GET /v1/whatsapp/webhook` subscription handshake (`hub.mode`, `hub.verify_token`, `hub.challenge`).
- `POST /v1/whatsapp/webhook` message delivery. Requires a valid `X-Hub-Signature-256`, else `403`. Always `200` quickly once authentic.

### Operations

- `GET /health/live` process is up. `GET /health/ready` database reachable, plus supplier breaker states and whether the agent and WhatsApp are enabled.
- `GET /metrics` Prometheus metrics. **Do not expose publicly** (the provided Caddyfile blocks it).

## Client checklist for the booking screen

1. Collect traveller details and contact; the number of travellers equals the offers' `pax`.
2. `POST /checkouts` → keep the checkout.
3. If `gateway == "razorpay"` and no payment yet, open Razorpay; store the result.
4. `POST /confirm`. **On a network error**, retry step 4 with the same checkout and stored payment. **On an API error**, discard both and start over.
5. Show `bookings[].reference`.
