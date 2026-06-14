from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.config import (
    load_watchlist, save_watchlist, load_interval, load_priority_interval,
    load_valid_intervals, load_valid_priority_intervals,
)

router = APIRouter(prefix="/api/config")


class WatchlistUpdate(BaseModel):
    add: list[str] = []
    remove: list[str] = []
    replace: list[str] | None = None


@router.get("/watchlist")
def get_watchlist():
    return {"watchlist": load_watchlist()}


@router.post("/watchlist")
def update_watchlist(body: WatchlistUpdate):
    if body.replace is not None:
        tickers = [t.upper() for t in body.replace]
    else:
        current = set(load_watchlist())
        current.update(t.upper() for t in body.add)
        current -= {t.upper() for t in body.remove}
        tickers = sorted(current)

    if not tickers:
        raise HTTPException(status_code=400, detail="Watchlist cannot be empty")

    save_watchlist(tickers)
    return {"watchlist": tickers}


@router.get("/interval")
def get_interval():
    return {"interval_hours": load_interval()}


@router.post("/interval")
def set_interval(body: dict):
    from app.scheduler import reschedule
    hours = body.get("interval_hours")
    if hours not in load_valid_intervals():
        raise HTTPException(status_code=400, detail=f"interval_hours must be one of {load_valid_intervals()}")
    reschedule(hours)
    return {"interval_hours": hours}


@router.get("/priority-interval")
def get_priority_interval():
    return {"priority_interval_minutes": load_priority_interval()}


@router.post("/priority-interval")
def set_priority_interval(body: dict):
    from app.scheduler import reschedule_priority
    minutes = body.get("priority_interval_minutes")
    if minutes not in load_valid_priority_intervals():
        raise HTTPException(status_code=400, detail=f"priority_interval_minutes must be one of {load_valid_priority_intervals()}")
    reschedule_priority(minutes)
    return {"priority_interval_minutes": minutes}
