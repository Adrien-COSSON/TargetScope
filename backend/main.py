# backend/main.py

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from backend.routers.assessment import router as assessment_router
from backend.routers.resolver import router as resolver_router
from backend.routers.synthesis import router as synthesis_router
from backend.utils.http_client import close_client, get_client
from backend.utils.logger import setup_logging

logger = logging.getLogger(__name__)


# 1. lifespan défini en premier
@asynccontextmanager
async def lifespan(app):
    # --- DÉMARRAGE ---
    # s'exécute uniquement dans le processus qui sert l'API
    # (pas dans le processus parent du reloader uvicorn)
    setup_logging()
    await get_client()  # initialise le HTTP client
    logger.info("TargetScope API started")

    yield  # ← l'app tourne normalement ici

    # --- ARRÊT ---
    await close_client()  # ferme le HTTP client proprement
    logger.info("TargetScope API stopped")


# 2. Instanciation de l'API
app = FastAPI(
    title="TargetScope API",
    description="Evidence-grounded target intelligence platform for drug discovery",
    version="0.1.0",
    lifespan=lifespan,
)


# 3. routers enregistrés après
# One assessment call returns every dimension at once, so a few routes are
# enough (the first plan had one router per domain: biology, clinical...).
app.include_router(resolver_router, prefix="/api")
app.include_router(assessment_router, prefix="/api")
app.include_router(synthesis_router, prefix="/api")


# 4. endpoint health
@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)