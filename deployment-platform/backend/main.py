"""
Deployment Platform — Backend API
Runs inside the EKS cluster (deployment-platform namespace).
Uses the mounted service-account token to talk to the Kubernetes API,
and the github-credentials secret to push YAML files to the GitOps repo.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import deploy, status, users
from database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown logic."""
    logger.info("Initialising database …")
    await init_db()
    logger.info("Backend API ready")
    yield
    logger.info("Backend API shutting down")


app = FastAPI(
    title="Deployment Platform API",
    description="One-click GitOps deployment platform backed by EKS + FluxCD",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────
# In production replace "*" with your frontend domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────
app.include_router(users.router,  prefix="/api/users",  tags=["users"])
app.include_router(deploy.router, prefix="/api/deploy", tags=["deploy"])
app.include_router(status.router, prefix="/api/status", tags=["status"])


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}