# Development guide

## Repository layout

```
backend/
  app/
    api/          routers (thin): auth, search, checkouts, bookings, trips, agent, whatsapp, health
    services/     search, planner, checkout (saga), booking (cancel), payments, agent, whatsapp
    providers/    base (interface), http (retries + breaker), demo, duffel, amadeus, registry
    core/         config (validation), errors, money, observability
    models.py db.py security.py schemas.py main.py
  alembic/        migrations (0001_initial_schema.py)
  tests/          pytest (50 tests; supplier and gateway HTTP is mocked)
web/              React + Vite + TypeScript
mobile/           Expo React Native + TypeScript
scripts/demo.py   one-command demo
docs/             this documentation, openapi.json
docker-compose.yml  web/Caddyfile  .env.example  .github/workflows/ci.yml
```

## Setup and checks

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .          # lint
pytest -q             # 50 tests, ~20s
uvicorn app.main:app --reload     # http://localhost:8000/docs
```

No database setup needed in dev: SQLite is created automatically. Web and mobile:

```bash
cd web    && npm install && npm run typecheck && npm run dev      # http://localhost:5173
cd mobile && npm install && npx expo install --check && npm run typecheck && npm start
```

CI ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs ruff and pytest for the backend and `typecheck` for both clients.

## How the tests work

- `tests/conftest.py` sets env vars **before** importing the app, uses a temp SQLite file, and resets the schema per test. `client` is a FastAPI `TestClient`.
- Suppliers and gateways are swapped by assigning `client.app.state.providers` / `.gateway` to fakes (see `FlakyProvider` and `RecordingGateway` in `tests/test_checkout.py`).
- Supplier and gateway HTTP is mocked with `httpx.MockTransport` (`tests/test_providers.py`, `tests/test_checkout.py`).
- `tests/test_migrations.py` applies the migrations and fails if `models.py` has drifted from them.
- Async tests use `@pytest.mark.anyio`.

## Add a supplier

1. Create `backend/app/providers/<name>.py` with a class extending `TravelProvider`:

```python
class MyProvider(TravelProvider):
    name = "mysupplier"
    kinds = frozenset({"flight"})            # any of flight, train, bus, hotel

    @classmethod
    def from_settings(cls, s, transport=None, breaker=None): ...

    async def search_transport(self, kind, origin, destination, day, adults) -> list[Found]:
        # Found(offer={...normalized...}, raw={...what you need to book later...})
        # offer["price"] is an int in MINOR units, the TOTAL for `adults` travellers.
    async def book(self, offer, raw, passengers, contact, reference) -> BookResult: ...
    async def cancel(self, provider_order_id) -> None: ...
```

2. Use `ProviderHTTP` for HTTP: it gives you timeouts, bounded retries on safe calls, the circuit breaker and metrics. Pass `retry=True` only for searches and GETs, **never** for order creation.
3. Raise `ProviderError(..., client_error=True)` for supplier 4xx (bad request) and let 5xx and timeouts surface as retryable errors. Raise `PriceChanged` if the supplier re-prices.
4. Register it in `registry.py` (`_FACTORIES`) and `core/config.py` (`KNOWN_PROVIDERS`, its settings, and a check in `validate_runtime`).
5. Write tests with `httpx.MockTransport`, like `tests/test_providers.py`. Assert the exact request you send.

Search, the planner, the agent and checkout need no changes.

## Add a payment gateway

Implement the `PaymentGateway` protocol in `services/payments.py` (`create_order`, `authorize`, `capture`, `void`, `refund`, `client_config`), add it to `build_gateway`, and add settings and validation. `authorize` must **verify** the payment server-side (signature, order, amount, currency, status) and never trust the client. Then teach the web and mobile apps to open the gateway's payment UI when `checkout.gateway` matches.

## Database changes

```bash
# edit backend/app/models.py, then:
cd backend && alembic revision --autogenerate -m "add thing"
# review the generated file, then run the tests (they apply it and check for drift)
```

## Conventions

- Routers stay thin; rules live in services; supplier specifics live in providers.
- Money is an integer in minor units everywhere except user-facing inputs (`budget`, in major units) and display.
- Business failures raise `DomainError(status, code, detail)`. Clients branch on `code`.
- Never log request bodies, tokens or URLs with personal data.
- Anything touching money or a supplier order gets a test for the failure path, not just the happy path.
