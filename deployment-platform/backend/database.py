"""SQLite database — tracks users and their deployments."""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
import sqlalchemy as sa

DATABASE_URL = "sqlite+aiosqlite:///./platform.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# ── Models ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id           = sa.Column(sa.String, primary_key=True)   # slugified username
    username     = sa.Column(sa.String, unique=True, nullable=False)
    email        = sa.Column(sa.String, unique=True, nullable=False)
    namespace    = sa.Column(sa.String, unique=True, nullable=False)
    created_at   = sa.Column(sa.DateTime, server_default=sa.func.now())


class Deployment(Base):
    __tablename__ = "deployments"

    id           = sa.Column(sa.String, primary_key=True)
    user_id      = sa.Column(sa.String, sa.ForeignKey("users.id"), nullable=False)
    name         = sa.Column(sa.String, nullable=False)
    namespace    = sa.Column(sa.String, nullable=False)
    git_path     = sa.Column(sa.String, nullable=False)   # path in GitOps repo
    status       = sa.Column(sa.String, default="pending")
    created_at   = sa.Column(sa.DateTime, server_default=sa.func.now())
    updated_at   = sa.Column(sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now())


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session