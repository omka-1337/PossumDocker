from fastapi import APIRouter

from app.api import console, servers, templates

router = APIRouter(prefix="/api")
router.include_router(templates.router)
router.include_router(servers.router)
router.include_router(console.router)


@router.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
