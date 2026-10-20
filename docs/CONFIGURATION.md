# Configuration reference

Everything is configured through environment variables (or a `.env` file). Names are the upper-case form of the settings in [backend/app/core/config.py](../backend/app/core/config.py). Copy [.env.example](../.env.example) to `.env` for Docker.

The server **validates configuration at startup and refuses to start** if something is unsafe or incomplete, naming every problem at once.

## Core

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `dev` | `prod` turns on the strict checks below. Docker Compose sets `prod`. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./ute.db` | Production: `postgresql+psycopg://user:pass@host:5432/db`. |
| `AUTO_CREATE_TABLES` | true in dev, false in prod | Dev convenience. Production uses `alembic upgrade head` (the container does this on start). |
| `SECRET_KEY` | dev placeholder | Signs login tokens. **Prod requires a unique value of 32+ characters** (`openssl rand -hex 32`). |
| `TOKEN_MINUTES` | `10080` (7 days) | Login token lifetime. |
| `OFFER_TTL_MINUTES` | `15` | How long a search result can be booked (suppliers that report their own expiry override this). |
| `CORS_ORIGINS` | empty | Comma-separated origins. Empty disables CORS. The provided setup serves web and API from one origin, and native apps ignore CORS. |
| `RATE_LIMIT_PER_MINUTE` | `120` | Per (tenant, client IP). `0` disables. |
| `REDIS_URL` | empty | e.g. `redis://redis:6379/0`. Shares rate-limit counters (and the WhatsApp throttle) across API replicas; **required before running more than one replica**. Empty = in-process counters. If Redis is down the limiter allows requests. |

## SaaS and background worker

| Variable | Default | Notes |
|---|---|---|
| `DEFAULT_TENANT_SLUG` | `public` | The self-service consumer tenant, created at start-up if missing. Used when a request has no `X-Tenant-Slug`; also the tenant that WhatsApp chats belong to. |
| `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` | none | If both are set, the platform super admin is created on start-up (idempotent). **Prod requires a password of 12+ characters.** Log in with header `X-Tenant-Slug: platform`. |
| `WORKER_INTERVAL_S` | `60` | Seconds between worker cycles (`python -m app.worker`). |
| `STALE_PENDING_MINUTES` | `15` | A booking left `pending` (or a checkout left `processing`) longer than this is flagged `needs_attention` / failed by the worker, with a CRITICAL log. |

Tenant-level settings (branding, supplier allow-list, sign-up policy, plan limits) live in the database and are managed through the admin API, not environment variables. See [SAAS.md](SAAS.md).

## Suppliers

| Variable | Default | Notes |
|---|---|---|
| `PROVIDERS` | `demo` | Comma-separated: `demo`, `duffel`, `amadeus`. |
| `ALLOW_DEMO_IN_PROD` | `false` | The demo supplier and gateway create **fake** bookings and take **no** money; prod refuses them unless this is `true`. |
| `PROVIDER_TIMEOUT_S` | `20` | Per supplier call. |
| `PROVIDER_RETRIES` | `2` | For safe calls only (searches, GETs). Orders are never retried. |
| `BREAKER_THRESHOLD` / `BREAKER_RESET_S` | `5` / `30` | Failures before a supplier's circuit opens, and seconds before a probe. |
| `DUFFEL_TOKEN` | none | Required with `duffel`. Use a `duffel_test_…` token first. |
| `DUFFEL_BASE_URL` | `https://api.duffel.com` | |
| `AMADEUS_CLIENT_ID` / `AMADEUS_CLIENT_SECRET` | none | Required with `amadeus`. Enterprise customers only (Self-Service closed 2026-07-17). |
| `AMADEUS_BASE_URL` | **none** | Required with `amadeus`; take it from your Enterprise contract. |
| `AMADEUS_CURRENCY` | none | Pin result currency so Amadeus and Duffel prices are comparable. |

## Payments

| Variable | Default | Notes |
|---|---|---|
| `PAYMENT_GATEWAY` | `demo` | `demo` or `razorpay`. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | none | Required with `razorpay`. Use `rzp_test_…` keys first. **Enable Manual Capture** in the Razorpay dashboard (see OPERATIONS). |
| `RAZORPAY_BASE_URL` | `https://api.razorpay.com` | |

## Chat agent

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | none (agent off) | Any OpenAI-compatible server, e.g. `http://ollama:11434/v1`. |
| `LLM_MODEL` | `llama3.1` | Must support tool calling. |
| `LLM_API_KEY` | none | If your server needs one. |
| `LLM_TIMEOUT_S` | `60` | |
| `AGENT_MAX_STEPS` | `5` | Max tool-call rounds per message. |
| `WEB_BASE_URL` | `http://localhost:5173` | Public URL of the web app; used to build the **Book** links in chat and WhatsApp. |

## WhatsApp (all four required, otherwise the channel stays off)

| Variable | Notes |
|---|---|
| `WHATSAPP_VERIFY_TOKEN` | Any long random string; you type the same one into Meta's dashboard. |
| `WHATSAPP_APP_SECRET` | The Meta app secret; used to verify webhook signatures. |
| `WHATSAPP_ACCESS_TOKEN` | Permanent access token for sending. |
| `WHATSAPP_PHONE_NUMBER_ID` | The sending number's id. |
| `WHATSAPP_GRAPH_URL` | Default `https://graph.facebook.com/v21.0`. |

## Places

`NOMINATIM_URL` (default the public OSM server, which is rate-limited to ~1 request/second and meant for light use; self-host Nominatim for production) and `USER_AGENT`.

## Client configuration

- **Web:** same-origin by default. Set `VITE_API_URL` at build time only if the API is on another origin. In dev, Vite proxies `/v1` to `localhost:8000`.
- **Mobile:** `extra.apiUrl` in `mobile/app.json` (`http://10.0.2.2:8000` Android emulator, `http://localhost:8000` iOS simulator, your LAN IP for a phone, your HTTPS domain for releases).

## Production start-up checks (what the server rejects)

A `SUPERADMIN_EMAIL` without a 12+ character `SUPERADMIN_PASSWORD` in `prod`; unknown provider names; `duffel` without `DUFFEL_TOKEN`; `amadeus` without id, secret and base URL; `razorpay` without keys; in `prod`: a missing, default or short `SECRET_KEY`, the demo supplier or the demo gateway without `ALLOW_DEMO_IN_PROD=true`.
