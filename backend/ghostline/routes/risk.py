"""The risk rules: read by anyone signed in, written by the owner only. The worker's token never reaches them."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ghostline import risk, risk_service
from ghostline.auth import OwnerSub

router = APIRouter()


class RuleIn(BaseModel):
    key: str
    value: Decimal = Field(ge=0)
    enabled: bool = True


class RulesIn(BaseModel):
    rules: list[RuleIn] = Field(min_length=1, max_length=len(risk.KEYS))


@router.get("/risk-rules")
def get_rules(request: Request, sub: str = OwnerSub) -> dict:
    db = request.app.state.db
    rows = db.fetch_all("SELECT key, value, enabled, updated_at FROM risk_rules")
    by_key = {r["key"]: r for r in rows}
    rs = risk.RuleSet.from_rows(rows)
    out = []
    for s in risk.suggestions():
        r = by_key.get(s["key"])
        out.append({**s, "value": str(r["value"]) if r else None, "enabled": r["enabled"] if r else None,
                    "updated_at": r["updated_at"].isoformat() if r else None})
    return {"rules": out, "missing": rs.missing, "complete": not rs.missing}


@router.put("/risk-rules")
def put_rules(body: RulesIn, request: Request, sub: str = OwnerSub) -> dict:
    unknown = [r.key for r in body.rules if r.key not in risk.RULES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown rule: {', '.join(unknown)}")
    for r in body.rules:
        kind = risk.RULES[r.key][0]
        if kind == "share" and not (0 <= r.value <= 1):
            raise HTTPException(status_code=422, detail=f"{r.key} is a share of the balance, between 0 and 1")
        if kind == "switch" and r.value not in (0, 1):
            raise HTTPException(status_code=422, detail=f"{r.key} is a switch: 0 or 1")
    changes = risk_service.save_rules(request.app.state.db, [r.model_dump() for r in body.rules], by=sub)
    return {**get_rules(request, sub), "changes": changes}


@router.get("/risk-rules/changes")
def rule_changes(request: Request, sub: str = OwnerSub) -> dict:
    rows = request.app.state.db.fetch_all(
        "SELECT id, key, old_value, old_enabled, new_value, new_enabled, changed_by, at FROM risk_rule_changes "
        "ORDER BY id DESC LIMIT 200")
    return {"changes": [{**r, "old_value": None if r["old_value"] is None else str(r["old_value"]),
                         "new_value": str(r["new_value"]), "at": r["at"].isoformat()} for r in rows]}
