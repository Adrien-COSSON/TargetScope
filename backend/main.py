# backend\main.py

# Import libraries
from backend.utils.logger import logger
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from backend.utils.http_client import get_client, close_client
from backend.routers.biology import router as biology_router
from backend.routers.clinical import router as clinical_router
from backend.routers.evidence import router as evidence_router
from backend.routers.expression import router as expression_router
from backend.routers.genetics import router as genetics_router
from backend.routers.patents import router as patents_router
from backend.routers.pharmacology import router as pharmacology_router
from backend.routers.preclinical import router as preclinical_router
from backend.routers.publications import router as publications_router
from backend.routers.synthesis import router as synthesis_router
from backend.routers.target import router as target_router


# 1. lifespan défini en premier
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- DÉMARRAGE ---
    # tout ce qui est écrit ici s'exécute quand uvicorn démarre
    await get_client()          # initialise le HTTP client
    logger.info("TargetScope API started")
    
    yield                       # ← l'app tourne normalement ici
    
    # --- ARRÊT ---
    # tout ce qui est écrit ici s'exécute quand uvicorn s'arrête
    await close_client()        # ferme le HTTP client proprement
    logger.info("TargetScope API stopped")


# 2. Instanciation de l'API
app = FastAPI(title="TargetScope API",
              description="Evidence-grounded target intelligence platform",
              version="0.1.0",
              lifespan=lifespan)


# 3. routers enregistrés après
app.include_router(biology_router, prefix="/api/biology")
app.include_router(clinical_router, prefix="/api/clinical")
app.include_router(evidence_router, prefix="/api/evidence")
app.include_router(expression_router, prefix="/api/expression")
app.include_router(genetics_router, prefix="/api/genetics")
app.include_router(patents_router, prefix="/api/patents")
app.include_router(pharmacology_router, prefix="/api/pharmacology")
app.include_router(preclinical_router, prefix="/api/preclinical")
app.include_router(publications_router, prefix="/api/publications")
app.include_router(synthesis_router, prefix="/api/synthesis")
app.include_router(target_router, prefix="/api/target")


# 4. endpoint health
@app.get("/health")
async def health_check():
    return {"status": "ok"}