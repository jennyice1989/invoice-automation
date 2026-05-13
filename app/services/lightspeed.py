import httpx
from app.config import settings


class LightspeedClient:
    """Placeholder client for Lightspeed Retail X-Series.

    Keep this isolated so we can safely add real product create/update calls after
    OAuth/token setup and final import field mapping are confirmed.
    """

    def __init__(self) -> None:
        self.access_token = settings.lightspeed_access_token
        self.retailer_id = settings.lightspeed_retailer_id
        self.base_url = "https://api.lightspeedapp.com/API/Account"

    async def health_check(self) -> dict:
        if not self.access_token or not self.retailer_id:
            return {"connected": False, "message": "Lightspeed credentials not configured"}
        return {"connected": True, "message": "Credentials are present"}

    async def placeholder_push_products(self, products: list[dict]) -> dict:
        # Real push logic goes here after final approval screen is built.
        return {"pushed": 0, "received": len(products), "message": "Push is disabled in v1 starter"}
