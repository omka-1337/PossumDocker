from fastapi import APIRouter, Depends, Request

from app.api import auth, backups, configs, console, files, schedules, servers, templates, users
from app.core.auth import current_user
from app.runtime.scheduler import timezone_name

router = APIRouter(prefix="/api")
# Everything but logging in needs a logged-in user; routes add their own permission checks on top.
logged_in = [Depends(current_user)]
router.include_router(auth.router)
for module in (templates, servers, configs, files, backups, schedules, users):
    router.include_router(module.router, dependencies=logged_in)
# WebSockets check the session themselves (see api/console.py).
router.include_router(console.router)


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta", tags=["meta"], dependencies=logged_in)
async def meta(request: Request) -> dict[str, str]:
    """Panel facts the UI needs: schedules run in this time zone."""
    return {"timezone": timezone_name(request.app.state.scheduler.tz)}
