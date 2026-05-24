"""Status endpoint — polls Kubernetes + FluxCD for real deployment state."""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, Deployment
import k8s

router = APIRouter()


class PodStatus(BaseModel):
    name:     str
    phase:    str
    ready:    bool
    restarts: int
    node:     str | None


class DeploymentStatus(BaseModel):
    deployment_id:  str
    namespace:      str
    git_path:       str
    db_status:      str
    flux_ready:     bool
    flux_reason:    str
    flux_message:   str
    pods:           list[PodStatus]


@router.get("/{deployment_id}", response_model=DeploymentStatus)
async def get_status(deployment_id: str, db: AsyncSession = Depends(get_db)):
    """
    Returns the live status of a deployment by:
    1. Reading our DB record.
    2. Checking the FluxCD Kustomization status.
    3. Listing pods in the user's namespace.
    """
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # FluxCD Kustomization name matches what we set in gitops.py
    ks_name  = f"user-{dep.namespace}"
    flux_ks  = k8s.get_flux_kustomization_status(dep.namespace, ks_name)

    # Live pods
    try:
        pods = k8s.get_namespace_pods(dep.namespace)
    except Exception:
        pods = []

    # Update DB status based on Flux state
    new_status = dep.status
    if flux_ks["ready"]:
        new_status = "deployed"
    elif flux_ks["reason"] in ("ReconciliationFailed", "BuildFailed"):
        new_status = "failed"

    if new_status != dep.status:
        dep.status = new_status
        await db.commit()

    return DeploymentStatus(
        deployment_id=dep.id,
        namespace=dep.namespace,
        git_path=dep.git_path,
        db_status=dep.status,
        flux_ready=flux_ks["ready"],
        flux_reason=flux_ks["reason"],
        flux_message=flux_ks["message"],
        pods=[PodStatus(**p) for p in pods],
    )


@router.get("/namespace/{namespace}", response_model=list[PodStatus])
async def get_namespace_status(namespace: str):
    """List all pods in a namespace — useful for the frontend dashboard."""
    try:
        pods = k8s.get_namespace_pods(namespace)
        return [PodStatus(**p) for p in pods]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))