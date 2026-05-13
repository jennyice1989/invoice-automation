from fastapi import APIRouter, Depends
from app.auth import require_api_key
from app.services.lightspeed import LightspeedClient

router = APIRouter(prefix="/lightspeed", tags=["lightspeed"], dependencies=[Depends(require_api_key)])


@router.get("/status")
async def lightspeed_status():
    return await LightspeedClient().health_check()
