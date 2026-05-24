"""
Deploy endpoint — accepts a YAML file, commits it to GitOps,
and returns a deployment ID that can be polled for status.
"""

import uuid
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, User, Deployment
import gitops

router = APIRouter()


class DeployResponse(BaseModel):
    deployment_id: str
    namespace:     str
    git_path:      str
    status:        str
    message:       str


@router.post("/", response_model=DeployResponse, status_code=202)
async def deploy(
    user_id:  str        = Form(...),
    filename: str        = Form(...),
    file:     UploadFile = File(...),
    db:       AsyncSession = Depends(get_db),
):
    """
    Upload a Kubernetes YAML manifest and deploy it via GitOps.

    Steps:
    1. Validate the user exists.
    2. Read + validate the uploaded YAML.
    3. Commit the YAML to the GitOps repository.
    4. FluxCD detects the change and applies it to EKS automatically.
    5. Return a deployment_id to poll /api/status/{deployment_id}.
    """

    # 1. Validate user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 2. Read YAML content (max 1 MB)
    content = await file.read()
    if len(content) > 1_048_576:
        raise HTTPException(status_code=413, detail="YAML file too large (max 1 MB)")

    yaml_text = content.decode("utf-8")

    # 3. Sanitise filename
    safe_filename = filename.replace("/", "-").replace("..", "").strip()
    if not safe_filename.endswith(".yaml") and not safe_filename.endswith(".yml"):
        safe_filename += ".yaml"

    deployment_id = str(uuid.uuid4())

    # 4. Commit to GitOps repo
    try:
        git_path = gitops.deploy_user_yaml(
            namespace=user.namespace,
            username=user.username,
            deployment_id=deployment_id,
            yaml_content=yaml_text,
            filename=safe_filename,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GitOps commit failed: {e}")

    # 5. Record deployment in DB
    dep = Deployment(
        id=deployment_id,
        user_id=user.id,
        name=safe_filename,
        namespace=user.namespace,
        git_path=git_path,
        status="reconciling",
    )
    db.add(dep)
    await db.commit()

    return DeployResponse(
        deployment_id=deployment_id,
        namespace=user.namespace,
        git_path=git_path,
        status="reconciling",
        message=(
            f"Manifest committed to GitOps repo at {git_path}. "
            "FluxCD will reconcile within ~60 seconds."
        ),
    )


@router.delete("/{deployment_id}", status_code=204)
async def delete_deployment(deployment_id: str, db: AsyncSession = Depends(get_db)):
    """Remove a deployment by deleting its manifest from the GitOps repo."""
    result = await db.execute(select(Deployment).where(Deployment.id == deployment_id))
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    try:
        gitops.delete_user_deployment(dep.namespace, dep.name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete from GitOps: {e}")

    dep.status = "deleted"
    await db.commit()


@router.get("/user/{user_id}", response_model=list[DeployResponse])
async def list_user_deployments(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deployment).where(Deployment.user_id == user_id))
    deps = result.scalars().all()
    return [
        DeployResponse(
            deployment_id=d.id,
            namespace=d.namespace,
            git_path=d.git_path,
            status=d.status,
            message="",
        )
        for d in deps
    ]