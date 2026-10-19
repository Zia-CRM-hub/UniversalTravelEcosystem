# Wanderly

Open-source travel booking (flights, trains, buses, hotels, one-tap trip planner) with four front doors on one backend:

| Channel | Code | Stack |
|---|---|---|
| Mobile app (Android/iOS) | [mobile/](mobile/) | React Native + Expo, TypeScript |
| Web app | [web/](web/) | React + Vite, TypeScript |
| Chat agent (in both apps) | [backend/app/services/agent.py](backend/app/services/agent.py) | Any OpenAI-compatible LLM server (Ollama, vLLM, llama.cpp) |
| WhatsApp bot | [backend/app/services/whatsapp.py](backend/app/services/whatsapp.py) | Meta WhatsApp Cloud API webhook |

Backend: Python, FastAPI, async SQLAlchemy, PostgreSQL, Alembic. Places: OpenStreetMap Nominatim. Deploy: Docker Compose + Caddy (auto HTTPS). Metrics: Prometheus. CI: GitHub Actions.

## What is verified and what is not

| Area | Status |
|---|---|
| Backend core, demo supplier, agent, WhatsApp, migrations | **Tested**: 35 passing tests (`cd backend && pytest`), lint clean, smoke-tested on a real uvicorn |
| Duffel adapter | Search request/response shapes **checked against Duffel docs**. Order creation matches the docs except the top-level `type: "instant"`. Place suggestions and the two-step cancellation are **from memory**. Tested only against mocked HTTP, never against Duffel. **Run it in the Duffel sandbox before trusting it.** |
| Amadeus adapter | **Unverified.** Amadeus Self-Service was **shut down on 2026-07-17**, so no new free keys exist. The adapter follows the Self-Service API shape; confirm every path and payload with your Enterprise docs. `AMADEUS_BASE_URL` has no default on purpose. |
| WhatsApp | Written from Meta's documented webhook and Graph API shapes. Signature check, dedupe and throttling are tested; **never run against a real WhatsApp number**. |
| Web and mobile apps | Written but **not built, type-checked or run** (no Node in the authoring environment). Run `npm install && npm run typecheck` in each first and expect small fixes. CI does this. |
| Docker and Compose | Written, **not run** (no Docker in the authoring environment). |
| Payments | **Only a demo gateway.** No real money moves. See below. |

## Architecture

```
 Mobile ─┐                      ┌─ providers/duffel.py   ┐
 Web ────┼─► Caddy ─► FastAPI ──┼─ providers/amadeus.py  ├─► breaker + retries + timeouts
 WhatsApp┘   (/v1)      │       └─ providers/demo.py     ┘
                        ├─ services/search   fan-out to all providers concurrently, partial results
                        ├─ services/booking  state machine, idempotency, payment auth/capture/void
                        ├─ services/planner  round trip + hotel within budget, one currency
                        ├─ services/agent    LLM tool loop (read-only tools) ─► WhatsApp / chat
                        └─ PostgreSQL (users, offer_snapshots, bookings, agent_sessions)
```

Key decisions:

- **Pluggable suppliers.** `PROVIDERS=duffel,amadeus,demo` enables any mix. Each implements `TravelProvider` ([base.py](backend/app/providers/base.py)). Searches fan out concurrently; a slow or broken supplier produces a `warnings` entry, not a failed search. Per-supplier circuit breaker, bounded retries on safe calls only, Prometheus metrics.
- **Offers are server-side snapshots.** Clients get an opaque `offer_id`. Booking reads price and supplier payload from the stored snapshot, so prices can't be tampered with, and offers expire (`expires_at`).
- **Money is integer minor units** with the currency attached. Prices are totals for the whole party.
- **Bookings are idempotent.** `Idempotency-Key` is required. The row is committed as `pending` before any external call, then `confirmed` or `failed`. Supplier orders are never auto-retried. Failed bookings void the payment authorization.
- **The agent cannot book or take payment.** It has read-only tools (search, plan) and hands off with a link. Booking needs passenger PII and payment, which don't belong in a chat transcript, and read-only tools bound the damage of prompt injection. Prices and links in replies are rendered by code from tool results, never typed by the model.
- **Fail-fast config.** The server refuses to start with unknown providers, missing credentials, a weak `SECRET_KEY` in prod, or the fake-booking demo provider in prod.
- **Observability.** JSON logs with request IDs, `/metrics`, `/health/live`, `/health/ready` (shows breaker state). Keep `/metrics` internal (the Caddyfile blocks it publicly).

## Run locally

```bash
# backend (demo supplier, SQLite, no keys needed)
cd backend
python -m venv .venv && .venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest && ruff check .
uvicorn app.main:app --reload                         # http://localhost:8000/docs

# web
cd web && npm install && npm run typecheck && npm run dev      # http://localhost:5173 (proxies /v1 to :8000)

# mobile
cd mobile && npm install && npx expo install --check && npm run typecheck && npm start
```

Mobile API URL: `extra.apiUrl` in `mobile/app.json` (`http://10.0.2.2:8000` Android emulator, `http://localhost:8000` iOS simulator, your LAN IP for a phone).

To use the chat agent locally, run any OpenAI-compatible server and set `LLM_BASE_URL`, e.g. `ollama serve` then `LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1`. Use a model that supports tool calling.

## Configure real suppliers

Copy [.env.example](.env.example) to `.env`.

- **Duffel:** `PROVIDERS=duffel`, `DUFFEL_TOKEN=duffel_test_...` (sandbox token). Duffel pays suppliers from your Duffel balance; your payment gateway charges the customer.
- **Amadeus (Enterprise only):** `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET`, `AMADEUS_BASE_URL`, optional `AMADEUS_CURRENCY`.
- **Both:** `PROVIDERS=duffel,amadeus`. Results are grouped by currency, so set `AMADEUS_CURRENCY` to match Duffel's account currency to get comparable prices.
- Duffel and Amadeus here sell **flights only**. Hotels, trains and buses come from the demo provider until you add a real adapter (implement `TravelProvider`, register it in [registry.py](backend/app/providers/registry.py)).

## Deploy

```bash
cp .env.example .env            # set DB_PASSWORD, SECRET_KEY (openssl rand -hex 32), suppliers, WEB_BASE_URL
docker compose up -d --build    # Postgres + API (runs migrations on start) + web + Caddy
docker compose --profile llm up -d && docker compose exec ollama ollama pull llama3.1   # optional agent
```

Put your domain in [web/Caddyfile](web/Caddyfile) for automatic HTTPS. Mobile builds: `npx eas build`, with `apiUrl` pointing at your HTTPS domain. Schema changes: edit `models.py`, then `alembic revision --autogenerate -m "..."` (a test fails if models and migrations drift).

## WhatsApp setup

1. Create a Meta app with the WhatsApp product, a phone number and a permanent access token.
2. Set `WHATSAPP_VERIFY_TOKEN` (any long random string), `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, plus `LLM_BASE_URL` and `WEB_BASE_URL`. All four WhatsApp values are needed or the channel stays off.
3. In Meta's dashboard set the webhook to `https://<your-domain>/v1/whatsapp/webhook` with the same verify token and subscribe to `messages`.
4. Message the number. Replies include Book links that open the web app (login required).

Behaviour: the webhook checks Meta's HMAC signature, answers 200 immediately and replies in a background task, ignores redelivered message IDs, and caps each sender at 10 messages/minute to bound LLM cost. The Cloud API is Meta's hosted service, not open source; swap `WhatsAppClient.send_text` to use Twilio or another provider.

## Known gaps before real customers

1. **Payments.** Implement `PaymentGateway` ([payments.py](backend/app/services/payments.py)) with a real processor. The authorize → book → capture/void flow is already in place.
2. **Reconciliation job.** If the process dies between "supplier order created" and the final commit, the booking stays `pending`. A job should compare pending rows with the supplier and settle them. Capture and refund failures are logged at CRITICAL for the same reason.
3. **Rate limiting is per process.** Move to Redis before running more than one API replica. The same goes for the WhatsApp per-sender throttle.
4. **Offer cleanup** runs opportunistically on search; use a scheduled job at scale.
5. **PII.** Passenger DOB and contact details are stored as plain JSON. Add field-level encryption and a retention policy (GDPR, India DPDP).
6. **Auth.** Email and password only; no email verification, password reset or refresh tokens. The web app keeps its token in `localStorage`.
7. **Hotels, trains, buses** need real adapters. Indian rail (IRCTC) needs their authorisation.
8. **Agent quality** depends on your model. Test tool-calling reliability with the model you choose; the agent is only as good as its prompts and tests, so add evals.
