from fastapi import APIRouter, HTTPException

from .. import health, schemas

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("", response_model=dict[str, schemas.HealthStatus])
def all_status():
    return health.get_all()


@router.post("/{service_id}/check", response_model=schemas.HealthStatus)
async def check(service_id: str):
    result = await health.check_now(service_id)
    if result is None:
        raise HTTPException(404, "Service not found")
    return result
