from fastapi import APIRouter, Request

from app.api import backups, configs, console, files, schedules, servers, templates
from app.runtime.scheduler import timezone_name

router = APIRouter(prefix="/api")
router.include_router(templates.router)
router.include_router(servers.router)
router.include_router(configs.router)
router.include_router(files.router)
router.include_router(backups.router)
router.include_router(schedules.router)
router.include_router(console.router)


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/meta", tags=["meta"])
async def meta(request: Request) -> dict[str, str]:
    """Panel facts the UI needs: schedules run in this time zone."""
    return {"timezone": timezone_name(request.app.state.scheduler.tz)}
