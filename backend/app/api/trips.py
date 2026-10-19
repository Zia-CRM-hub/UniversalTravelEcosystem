from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import TripPlanIn
from ..services import planner
from .deps import get_providers

router = APIRouter(prefix="/trips", tags=["trips"])


@router.post("/plan")
async def plan(body: TripPlanIn, providers=Depends(get_providers), db: AsyncSession = Depends(get_db)):
    return await planner.plan_trip(db, providers, origin=body.origin, destination=body.destination, start=body.start,
                                   end=body.end, travelers=body.travelers, mode=body.mode, budget=body.budget,
                                   stars=body.stars)
