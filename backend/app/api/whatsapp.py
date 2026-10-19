import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse

from ..core.config import settings
from ..core.errors import DomainError
from ..services import whatsapp as wa
from .deps import get_llm, get_providers, get_whatsapp

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
log = logging.getLogger(__name__)


@router.get("/webhook")
async def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
                 challenge: str = Query("", alias="hub.challenge")):
    """Meta's one-time subscription handshake."""
    expected = settings.whatsapp_verify_token
    if not expected or mode != "subscribe" or token != expected.get_secret_value():
        raise DomainError(403, "forbidden", "Verification failed")
    return PlainTextResponse(challenge)


@router.post("/webhook")
async def receive(request: Request, tasks: BackgroundTasks, client=Depends(get_whatsapp),
                  providers=Depends(get_providers), llm=Depends(get_llm)):
    if client is None:
        raise DomainError(404, "whatsapp_disabled", "WhatsApp channel is not configured")
    body = await request.body()
    secret = settings.whatsapp_app_secret.get_secret_value()
    if not wa.verify_signature(secret, body, request.headers.get("X-Hub-Signature-256")):
        raise DomainError(403, "bad_signature", "Invalid signature")
    try:
        payload = json.loads(body)
    except ValueError:
        return {"status": "ignored"}
    for msg in wa.extract_messages(payload):
        if msg["id"] and msg["from"]:
            tasks.add_task(wa.handle_incoming, client, providers, llm, msg)
    return {"status": "ok"}  # always 200 once authentic, or Meta keeps retrying
