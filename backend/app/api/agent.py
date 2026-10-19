from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import User
from ..schemas import ChatIn, ChatOut
from ..security import current_user
from ..services import agent
from .deps import get_llm, get_providers

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db),
               providers=Depends(get_providers), llm=Depends(get_llm)):
    """Shared by the web and mobile apps. Authenticated so LLM cost is attributable and rate-limitable."""
    session = await agent.get_session(db, "app", user.id)
    reply, cards = await agent.chat(db, providers, llm, session, body.message)
    return ChatOut(reply=reply, cards=cards)


@router.delete("/session", status_code=204)
async def reset(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    session = await agent.get_session(db, "app", user.id)
    session.messages = []
    await db.commit()
