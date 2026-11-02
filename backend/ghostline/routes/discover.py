"""Discover: movers by day, week or month (stocks, ETFs, one industry), sectors, industries and themes. All read
stored data (the worker pulls it in production; staging reads production's nightly copy)."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, Request

from ghostline import discover
from ghostline.auth import OwnerSub

router = APIRouter()
PERIOD = "^(day|week|month)$"
MIN_PRICE = Decimal(5)          # stocks under $5 are left out unless the owner lowers it


@router.get("/discover/movers")
def movers(request: Request, period: str = Query(default="day", pattern=PERIOD),
           direction: str = Query(default="gainers", pattern="^(gainers|losers|active)$"),
           kind: str = Query(default="all", pattern="^(all|stocks|etfs)$"),
           industry: str | None = Query(default=None, max_length=80),
           min_price: Annotated[Decimal, Query(ge=0, le=10000)] = MIN_PRICE,
           limit: int = Query(default=25, ge=1, le=50), sub: str = OwnerSub) -> dict:
    return discover.movers(request.app.state.db, period, direction, kind, industry or None, min_price, limit)


@router.get("/discover/sectors")
def sectors(request: Request, period: str = Query(default="day", pattern=PERIOD), sub: str = OwnerSub) -> dict:
    return discover.sectors(request.app.state.db, period)


@router.get("/discover/industries")
def industries(request: Request, period: str = Query(default="day", pattern=PERIOD), sub: str = OwnerSub) -> dict:
    return discover.industries(request.app.state.db, period)


@router.get("/discover/industry-names")
def industry_names(request: Request, sub: str = OwnerSub) -> dict:
    return {"industries": discover.industry_names(request.app.state.db)}


@router.get("/discover/themes")
def themes(request: Request, period: str = Query(default="week", pattern=PERIOD), sub: str = OwnerSub) -> dict:
    return discover.themes(request.app.state.db, period)
