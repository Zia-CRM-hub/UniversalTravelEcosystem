# Universal Travel Ecosystem

A next-generation global travel ecosystem: plan and book travel and stays from the **web**, a **mobile app**, an **AI concierge**, and **WhatsApp**, all on one open-source backend.

## Vision, and how much of it exists today

The vision is one platform that combines everything below. Most of it is not built yet, and this table says so plainly.

| Vision | Today |
|---|---|
| ✈️ **Flights** | **Built**: Duffel and Amadeus adapters (configurable), plus a synthetic supplier. Real suppliers are *unverified against the live services*. |
| 🚆🚌 **Trains, buses** | Synthetic supplier only. No real inventory yet. |
| 🚢🛣️ **Cruises, road trips** | Not built. |
| 🏨 **Hotels** | Synthetic supplier only. |
| 🏖️🏡🧘 **Resorts, homestays, retreats** | Not built (needs inventory sources). |
| 💼 **Business travel** | Not built (needs company accounts, travel policies, approvals, invoicing). |
| 🌍 **Leisure and adventure** | Partly: one-tap trip planner with a day-by-day outline of generic ideas. No adventure content. |
| 🕉 **Spiritual and wellness journeys** | Not built. |
| 🎟 **Experiences and events** | Not built. |
| 🚕 **Mobility and local transport** | Not built. |
| 🤖 **AI travel concierge** | **Built (v1)**: chat agent in the web app, mobile app and WhatsApp that searches and plans, then hands off a Book link. It cannot book or take payment in chat, by design. |
| ❤️ **Personalized experience** | Not built (no profiles, preferences or history-based recommendations yet). |

What *is* built is the foundation the rest plugs into: a supplier abstraction (add a new kind of inventory by implementing one interface), a safe multi-booking checkout with a single payment, the agent, and the channels. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#8-extension-points).

## The four front doors

| Channel | Code | Stack |
|---|---|---|
| Web app | [web/](web/) | React, Vite, TypeScript |
| Mobile app (Android/iOS) | [mobile/](mobile/) | React Native, Expo, TypeScript |
| AI concierge (inside both apps) | [backend/app/services/agent.py](backend/app/services/agent.py) | Any OpenAI-compatible model server (Ollama, vLLM, llama.cpp) |
| WhatsApp | [backend/app/services/whatsapp.py](backend/app/services/whatsapp.py) | Meta WhatsApp Cloud API webhook |

Backend: Python, FastAPI, async SQLAlchemy, PostgreSQL, Alembic. Places: OpenStreetMap. Deploy: Docker Compose and Caddy (automatic HTTPS). Metrics: Prometheus. CI: GitHub Actions. Payments: Razorpay (manual capture) or a demo gateway.

## See it in 20 seconds

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python ../scripts/demo.py
```

This starts the real API plus stand-ins for an LLM and WhatsApp, then walks through search, trip planning, a one-payment multi-booking checkout, cancellation, the chat agent and a signed WhatsApp message. Full narrated walkthrough: [docs/DEMO.md](docs/DEMO.md).

## What is verified and what is not

| Area | Status |
|---|---|
| Backend: search, planner, checkout saga, agent, WhatsApp handling, migrations | **Tested**: 50 passing tests, lint clean, demo script runs end to end |
| Duffel adapter | Search and order shapes checked against Duffel's docs; place suggestions, `type: "instant"` and the two-step cancellation are from memory. Mocked HTTP only. **Never run against Duffel.** |
| Amadeus adapter | **Unverified.** Amadeus Self-Service was shut down on 2026-07-17; only Enterprise customers can use it. |
| Razorpay gateway | Flow and signature verification checked against Razorpay's docs; payment-fetch fields and the refund endpoint are from memory. Mocked HTTP only. |
| WhatsApp | Signature check, de-duplication and throttling are tested. **Never run against a real WhatsApp number.** |
| Web and mobile apps, Docker, CI | Written but **not built or run** (no Node or Docker where this was authored). Run `npm run typecheck` first and expect small fixes. |
| Real hotels, trains, buses | None yet; synthetic only. |

## Documentation

| Document | What is in it |
|---|---|
| [docs/DEMO.md](docs/DEMO.md) | Narrated demo: scripted, apps, and a live-services checklist; what to say about limits |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, data model, the checkout saga, failure handling, security model, trade-offs |
| [docs/API.md](docs/API.md) | Conventions, every endpoint, error codes, client checklist (exact spec: [docs/openapi.json](docs/openapi.json)) |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every environment variable and the production start-up checks |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Deploy, connect Duffel/Razorpay/WhatsApp, monitoring, runbook for CRITICAL logs, security checklist, known gaps |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Repo layout, tests, how to add a supplier or gateway, migrations |

## Quick start

```bash
# backend (demo supplier, SQLite, no keys needed)
cd backend && pip install -r requirements-dev.txt
pytest && ruff check .
uvicorn app.main:app --reload                         # http://localhost:8000/docs

# web
cd web && npm install && npm run typecheck && npm run dev      # http://localhost:5173

# mobile
cd mobile && npm install && npx expo install --check && npm run typecheck && npm start

# deploy
cp .env.example .env && docker compose up -d --build
```

## Suggested path from here

1. **Make today's slice real:** run Duffel in its sandbox and Razorpay in test mode ([DEMO.md, part C](docs/DEMO.md#c-live-services-demo-checklist)), build and fix the web and mobile apps, then add the reconciliation job.
2. **Add real inventory** for hotels, then trains and buses, by implementing the supplier interface.
3. **Broaden categories** one at a time (homestays and retreats, experiences and events, local mobility), each as new supplier kinds plus search and checkout support.
4. **Then personalization and business travel**, which depend on having enough real data and accounts to be worth building.
