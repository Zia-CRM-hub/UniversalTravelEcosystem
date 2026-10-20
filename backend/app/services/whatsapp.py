"""WhatsApp Cloud API (Meta) channel for the travel agent.

Notes
* The Cloud API is Meta's hosted service (not open source); this adapter is plain HTTP.
  Replace `WhatsAppClient.send_text` to use Twilio or another BSP.
* Free-form replies are only allowed within 24h of the user's last message; we only
  ever reply to inbound messages, so that holds.
* Written from Meta's documented webhook/Graph API shapes; never run against a live
  WhatsApp number from this environment.
"""
import hashlib
import hmac
import logging

import httpx
from sqlalchemy import select

from ..core.config import Settings, settings
from ..core.errors import DomainError
from ..core.ratelimit import Limiter, MemoryLimiter
from ..db import SessionLocal
from ..models import Tenant
from ..tenancy import providers_for
from . import agent

log = logging.getLogger(__name__)
MAX_LEN = 4000  # WhatsApp text bodies cap at 4096
PER_USER_PER_MIN = 10
SEEN_LIMIT = 50


def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def extract_messages(payload: dict) -> list[dict]:
    """Flatten Meta's nested webhook payload into [{id, from, type, text}]."""
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []):
                out.append({"id": m.get("id"), "from": m.get("from"), "type": m.get("type"),
                            "text": (m.get("text") or {}).get("body", "")})
    return out


class WhatsAppClient:
    def __init__(self, s: Settings, transport: httpx.AsyncBaseTransport | None = None, limiter: Limiter | None = None):
        self.phone_id = s.whatsapp_phone_number_id
        self.http = httpx.AsyncClient(
            base_url=s.whatsapp_graph_url, timeout=15, transport=transport,
            headers={"Authorization": f"Bearer {s.whatsapp_access_token.get_secret_value()}"})
        self.limiter = limiter or MemoryLimiter()

    async def allow(self, wa_id: str) -> bool:
        """Cap per-sender message rate so one number cannot run up LLM cost (shared store when REDIS_URL is set)."""
        ok, _ = await self.limiter.hit(f"wa:{wa_id}", PER_USER_PER_MIN, 60)
        return ok

    async def send_text(self, to: str, body: str) -> None:
        r = await self.http.post(f"/{self.phone_id}/messages", json={
            "messaging_product": "whatsapp", "to": to, "type": "text",
            "text": {"body": body[:MAX_LEN], "preview_url": False}})
        r.raise_for_status()

    async def aclose(self):
        await self.http.aclose()


async def handle_incoming(wa: WhatsAppClient, providers, llm, msg: dict) -> None:
    """Runs in a background task: the webhook must return 200 quickly or Meta retries."""
    to = msg["from"]
    try:
        async with SessionLocal() as db:
            # The WhatsApp number belongs to the default tenant (one number per platform for now; see docs/SAAS.md).
            tenant = await db.scalar(select(Tenant).where(Tenant.slug == settings.default_tenant_slug))
            if not tenant or tenant.status != "active":
                return
            providers = providers_for(providers, tenant)
            session = await agent.get_session(db, tenant.id, "whatsapp", to)
            if msg["id"] in session.seen_message_ids:
                return  # Meta redelivers; never answer twice
            session.seen_message_ids = [*session.seen_message_ids, msg["id"]][-SEEN_LIMIT:]
            await db.commit()
            if msg["type"] != "text" or not msg["text"].strip():
                await wa.send_text(to, "I can only read text messages for now. Tell me where and when you want to travel.")
                return
            if not await wa.allow(to):
                await wa.send_text(to, "You're sending messages quickly. Please wait a minute and try again.")
                return
            try:
                reply, cards = await agent.chat(db, providers, llm, session, msg["text"][:2000])
                await wa.send_text(to, agent.render_text(reply, cards))
            except DomainError as e:
                await wa.send_text(to, e.detail)
    except Exception:
        log.exception("failed handling WhatsApp message %s", msg.get("id"))
