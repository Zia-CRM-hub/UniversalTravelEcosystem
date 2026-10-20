from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import ChatIn, ChatOut
from ..services import agent
from .deps import Scope, get_llm, user_scope

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, scope: Scope = Depends(user_scope), db: AsyncSession = Depends(get_db), llm=Depends(get_llm)):
    """Shared by the web and mobile apps. Authenticated so LLM cost is attributable and rate-limitable."""
    session = await agent.get_session(db, scope.tenant.id, "app", scope.user.id)
    reply, cards = await agent.chat(db, scope.providers, llm, session, body.message)
    return ChatOut(reply=reply, cards=cards)


@router.delete("/session", status_code=204)
async def reset(scope: Scope = Depends(user_scope), db: AsyncSession = Depends(get_db)):
    session = await agent.get_session(db, scope.tenant.id, "app", scope.user.id)
    session.messages = []
    await db.commit()
