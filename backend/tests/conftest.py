import asyncio
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

# Must be set before `app` is imported.
_DB = Path(tempfile.gettempdir()) / "ute_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB.as_posix()}"
os.environ["PROVIDERS"] = "demo"
os.environ["APP_ENV"] = "dev"
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["WHATSAPP_VERIFY_TOKEN"] = ""
os.environ["SUPERADMIN_EMAIL"] = "root@platform.com"
os.environ["SUPERADMIN_PASSWORD"] = "superadmin-pass-123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

D1 = (date.today() + timedelta(days=10)).isoformat()
D2 = (date.today() + timedelta(days=14)).isoformat()


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _reset():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture()
def client():
    asyncio.run(_reset())
    with TestClient(app) as c:
        original, gateway = dict(app.state.providers), app.state.gateway
        yield c
        app.state.providers, app.state.gateway = original, gateway
        app.state.llm = None
        app.state.whatsapp = None


def register(c, email="a@b.com"):
    r = c.post("/v1/auth/register", json={"email": email, "name": "Asha", "password": "password123"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def passengers(n=1):
    return [{"title": "ms", "gender": "f", "given_name": f"Asha{i}", "family_name": "Rao", "born_on": "1990-05-17"}
            for i in range(n)]


CONTACT = {"email": "asha@example.com", "phone": "+919876543210"}


def first_offer(c, kind="flight", adults=1):
    p = {"kind": kind, "origin": "Delhi", "destination": "Goa", "date": D1, "adults": adults}
    r = c.get("/v1/search/transport", params=p)
    assert r.status_code == 200, r.text
    return r.json()["offers"][0]


def open_checkout(c, headers, offers):
    return c.post("/v1/checkouts", headers=headers, json={"offer_ids": [o["offer_id"] for o in offers]})


def confirm(c, headers, checkout_id, n=1, payment=None):
    return c.post(f"/v1/checkouts/{checkout_id}/confirm", headers=headers,
                  json={"payment": payment, "passengers": passengers(n), "contact": CONTACT})


def book(c, headers, offers, n=1):
    """Open a checkout and confirm it; returns the confirm response."""
    offers = offers if isinstance(offers, list) else [offers]
    co = open_checkout(c, headers, offers)
    assert co.status_code == 201, co.text
    return confirm(c, headers, co.json()["id"], n)
