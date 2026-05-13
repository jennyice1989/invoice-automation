from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import invoices, lightspeed

app = FastAPI(title=settings.app_name, version="0.1.0")

origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(invoices.router)
app.include_router(lightspeed.router)


@app.get("/")
def root():
    return {"name": settings.app_name, "status": "running", "docs": "/docs"}


@app.get("/health")
def health():
    return {"ok": True}
