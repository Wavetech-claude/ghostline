"""Corporate actions waiting for the owner in the real book, and the owner's answer (ghostline.corporate)."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ghostline import corporate
from ghostline.auth import OwnerSub

router = APIRouter()


@router.get("/corporate-actions")
def waiting(request: Request, sub: str = OwnerSub) -> dict:
    return {"pending": corporate.pending(request.app.state.db)}


class ConfirmIn(BaseModel):
    amount: Decimal | None = Field(default=None, gt=0, description="a dividend: the cash the broker paid")


@router.post("/corporate-actions/{action_id}/confirm")
def confirm(request: Request, action_id: str, body: ConfirmIn | None = None, sub: str = OwnerSub) -> dict:
    try:
        return corporate.decide(request.app.state.db, action_id, True, body.amount if body else None)
    except LookupError:
        raise HTTPException(status_code=404, detail="nothing waits under that id") from None


@router.post("/corporate-actions/{action_id}/dismiss")
def dismiss(request: Request, action_id: str, sub: str = OwnerSub) -> dict:
    try:
        return corporate.decide(request.app.state.db, action_id, False)
    except LookupError:
        raise HTTPException(status_code=404, detail="nothing waits under that id") from None
