"""User management — register a new user → creates namespace + GitOps bootstrap."""

import re
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, User
import k8s
import gitops

router = APIRouter()


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr


class UserResponse(BaseModel):
    id:        str
    username:  str
    email:     str
    namespace: str


def _slugify(text: str) -> str:
    """Convert a username to a valid Kubernetes namespace name."""
    slug = re.sub(r"[^a-z0-9-]", "-", text.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:50]  # namespace names must be ≤ 63 chars


@router.post("/register", response_model=UserResponse, status_code=201)
async def register_user(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Register a new user.
    1. Creates a record in the database.
    2. Creates a Kubernetes namespace with quotas + network policy.
    3. Commits the bootstrap GitOps manifests to GitHub.
    """
    # Check username uniqueness
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    user_id   = str(uuid.uuid4())
    namespace = f"user-{_slugify(req.username)}"

    # Create Kubernetes namespace
    try:
        k8s.create_user_namespace(namespace, user_id, req.username)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create namespace: {e}")

    # Bootstrap GitOps structure
    try:
        gitops.bootstrap_user_in_gitops(namespace, user_id, req.username)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to bootstrap GitOps: {e}")

    # Save to database
    user = User(id=user_id, username=req.username, email=req.email, namespace=namespace)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return UserResponse(id=user.id, username=user.username, email=user.email, namespace=user.namespace)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(id=user.id, username=user.username, email=user.email, namespace=user.namespace)


@router.get("/", response_model=list[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    users = result.scalars().all()
    return [UserResponse(id=u.id, username=u.username, email=u.email, namespace=u.namespace) for u in users]