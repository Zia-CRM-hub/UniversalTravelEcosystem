# Operations guide

## 1. Deploy

Requirements: a Linux host with Docker and Compose, a domain pointing at it.

```bash
cp .env.example .env
#   set DB_PASSWORD, SECRET_KEY (openssl rand -hex 32), PROVIDERS, supplier + gateway keys, WEB_BASE_URL
docker compose up -d --build        # Postgres, API (migrates on start), web + Caddy
docker compose logs -f api          # JSON logs; look for "started env=prod providers=[...] gateway=..."
```

- Put your domain in [web/Caddyfile](../web/Caddyfile) (replace `:80`) for automatic HTTPS. The Caddyfile proxies `/v1/*` and `/health/*` to the API and blocks `/metrics`.
- If the API exits immediately, read its first log lines: configuration errors are listed there, all at once.
- Optional open-source model for the agent: `docker compose --profile llm up -d && docker compose exec ollama ollama pull llama3.1`, then set `LLM_BASE_URL=http://ollama:11434/v1` in `.env` and `docker compose up -d api`.
- **Scaling out:** Redis is included in Compose (`REDIS_URL`), so several API replicas share rate limits. The API is stateless; the **worker** runs as its own container (included). Read [SCALING.md](SCALING.md) first: the bundled Caddy has not been verified to balance across replicas, so use a real load balancer for multi-replica deployments.
- **First start:** set `SUPERADMIN_EMAIL` and `SUPERADMIN_PASSWORD` to create the platform super admin, then onboard customers as described in [SAAS.md](SAAS.md#onboarding-a-new-customer-runbook).
- Mobile releases: `npx eas build`, with `extra.apiUrl` set to your HTTPS domain. Card payments need the development or release build, not Expo Go.

> Docker and Compose files have not been run in the authoring environment. Expect to fix small things on the first `docker compose up`.

## 2. Connect the real services

### Duffel (flights)
1. Create a Duffel account and a **test** token (`duffel_test_…`). Set `PROVIDERS=duffel`, `DUFFEL_TOKEN=…`.
2. Search and book in the sandbox. The adapter was written against Duffel's documented search and order shapes but has only run against mocked HTTP: place suggestions, the `type: "instant"` order field and the two-step cancellation are from memory. **Fix anything the sandbox rejects before going live.**
3. Live mode additionally needs your Duffel balance funded: Duffel pays the airline from your balance and you charge the customer via Razorpay.

### Amadeus
Self-Service was shut down on 2026-07-17. Only Enterprise customers can use it. Set `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET`, `AMADEUS_BASE_URL` (from your contract), and optionally `AMADEUS_CURRENCY`. The adapter is unverified against any Amadeus environment; confirm each path and payload with your Enterprise documentation.

### Razorpay (payments)
1. Create test keys; set `PAYMENT_GATEWAY=razorpay`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`.
2. **Dashboard → Account & Settings → Payments Capture → Change → Manual Capture.** The flow depends on payments staying `authorized` until all bookings succeed. If the account auto-captures, the code still works (it detects `captured` and refunds instead of releasing) but customers pay before the trip is confirmed.
3. Per Razorpay's docs, an uncaptured payment is auto-refunded after the manual-capture timeout (12 minutes to 3 days). Customers are told it is released automatically; the refund to their bank takes 5–7 working days.
4. `GET /v1/payments/{id}` fields and the refund endpoint are from memory; verify in test mode.
5. Mobile: Razorpay's React Native SDK is a native module (`react-native-razorpay`), so you need a development build (`npx expo prebuild` / EAS dev client).

### WhatsApp
1. Meta for Developers → create an app → add **WhatsApp** → get a phone number id and a permanent access token.
2. Set `WHATSAPP_VERIFY_TOKEN` (random string), `WHATSAPP_APP_SECRET`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, plus `LLM_BASE_URL` and `WEB_BASE_URL`.
3. Webhook URL `https://<domain>/v1/whatsapp/webhook`, verify token as above, subscribe to **messages**.
4. Text the number. Replies list options with **Book** links to the web app.

Notes: replies are only sent in response to inbound messages (inside Meta's 24-hour window). Non-text messages get a short "text only" reply. Each sender is capped at 10 messages a minute. The Cloud API is Meta's hosted service; to use Twilio or another provider, replace `WhatsAppClient.send_text`.

## 3. Database and migrations

- Production schema is managed by Alembic; the API container runs `alembic upgrade head` before starting.
- Change a model, then `cd backend && alembic revision --autogenerate -m "describe change"` and review the file. A test fails if models and migrations drift.
- Back up Postgres (`pg_dump` nightly at minimum). Bookings and checkouts are the system of record for money movement.
- `offer_snapshots` grows with every search. Expired rows older than an hour are pruned on search; at scale run a scheduled `DELETE … WHERE expires_at < now() - interval '1 hour'`.

## 4. Monitoring

**Endpoints:** `/health/live` (liveness), `/health/ready` (DB, supplier breaker states, agent and WhatsApp flags), `/metrics` (Prometheus, keep internal).

**Metrics:** `http_requests_total{method,route,status}`, `http_request_seconds`, `provider_calls_total{provider,outcome}`, `provider_call_seconds`, `bookings_total{provider,outcome}`.

**Suggested alerts**

| Alert | Why |
|---|---|
| Any `CRITICAL` log line | Money or supplier orders need a human (see runbook) |
| `provider_calls_total{outcome="circuit_open"}` rising | A supplier is down; users see warnings |
| `bookings_total{outcome="failed"}` ratio above normal | Supplier or payment trouble |
| 5xx rate on `/v1/checkouts/{checkout_id}/confirm` | The most valuable endpoint |
| `/health/ready` not 200 | Database down |

**Logs** are JSON, one line each, with `request_id` (also returned as `X-Request-ID`). Supplier calls log provider and outcome; `httpx` request logging is silenced because URLs can carry tokens or personal data.

## 5. Runbook: what the CRITICAL logs mean

| Log message | Meaning | What to do |
|---|---|---|
| `CAPTURE FAILED for booked checkout …` | Bookings are confirmed but the payment could not be captured (`payment_state=capture_failed`). | Capture the payment in the Razorpay dashboard (amount = checkout amount) before the manual-capture timeout, or the customer is refunded while holding bookings. |
| `VOID FAILED for checkout …` | A failed checkout's payment could not be released. | It will auto-release at the timeout; if the account auto-captured, refund it manually. |
| `COMPENSATION FAILED: supplier order … must be cancelled manually` | One booking in a failed trip could not be cancelled at the supplier (`status=needs_attention`). | Cancel that order in the supplier's dashboard, then set the booking to `cancelled`. |
| `REFUND FAILED for cancelled booking …` | Supplier cancelled but the refund failed; the booking shows "refund pending manual review". | Refund that amount in the gateway dashboard. |
| `STALE PENDING booking …` / `STALE checkout …` | The worker found a booking or checkout abandoned mid-flight (process crash). The booking is now `needs_attention`. | Check the supplier for an order with that reference; cancel or confirm by hand, and reconcile the payment in the gateway. |

Also look for bookings stuck in `pending` for more than a few minutes (the process died mid-booking): check the supplier for an order carrying the booking's reference (`metadata.ute_ref` on Duffel) and settle by hand. An automated reconciliation job is the first thing to build next.

## 6. Security checklist before real customers

- [ ] `APP_ENV=prod`, unique `SECRET_KEY`, `.env` not committed, secrets in a secret manager.
- [ ] HTTPS only (Caddy with a real domain); `/metrics` not public.
- [ ] Razorpay in live mode with Manual Capture on; supplier keys are live keys.
- [ ] Postgres not exposed to the internet; backups tested.
- [ ] Encryption at rest for the database and backups; decide on field-level encryption and a retention period for passenger data (GDPR, India's DPDP Act).
- [ ] Email verification and password reset (not built).
- [ ] Privacy policy, terms and cancellation rules shown before payment.
- [ ] Penetration test and dependency scanning in CI.

## 7. Known gaps (be honest about these before launch)

1. **Reconciliation is detect-only.** The worker flags bookings stuck in `pending` as `needs_attention` and fails stuck checkouts (with CRITICAL logs), but a person still has to check the supplier and gateway. Nothing resolves them automatically.
2. **Rate limiting is shared only if `REDIS_URL` is set** (it is in the shipped Compose file). The Redis path is tested against a fake Redis, not a real one.
3. **Passenger data is plain JSON in the database.**
4. **Auth is basic** (no verification, reset or refresh tokens); the web app stores its token in `localStorage`.
5. **Hotels, trains and buses** have only the demo supplier; flights are only real through Duffel (or Amadeus Enterprise). Indian rail (IRCTC) needs their authorisation.
6. **Integrations are unverified live**: Duffel, Amadeus, Razorpay and WhatsApp were tested only against mocked HTTP.
7. **Web, mobile, Docker and CI were not run** in the authoring environment (no Node or Docker there).
8. **Agent quality** depends on the model you choose and has no automated evals.
9. **No webhook from Razorpay.** If the customer pays and then loses connectivity before confirm, nothing is captured (the hold releases by itself), which is safe but means that booking is lost. A webhook-driven completion would recover it.
