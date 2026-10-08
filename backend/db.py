"""
Persistence for users, access (CTX assignments) and the audit trail.

This is the first part of the app that can't live in process memory: if a
restart wiped role assignments or the audit log, access control would
silently reset. Contract data stays where it is (state.py, loaded from
files or Snowflake) - only identity/access/config lives here.

SQLite by default (zero setup for dev); set DATABASE_URL to a Postgres URL
for anything shared, e.g. postgresql+psycopg://user:pass@host/dbname.
Tables are created with create_all() on startup, which only ever ADDS
tables - when a later phase needs to change an existing column, introduce
Alembic at that point rather than before.
"""
import json
import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./app.db")
_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    # FastAPI runs sync endpoints/dependencies in a thread pool, so one
    # SQLite connection can be touched from more than one thread over time.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=not _is_sqlite,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    # Naive UTC: SQLite drops tzinfo anyway, so store/return one consistent
    # shape everywhere and append "Z" only when serializing for the API.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


class Base(DeclarativeBase):
    pass


class Ctx(Base):
    """An area code (3 digits, e.g. "034"). The name is what admins see when
    assigning access - codes are auto-discovered from the loaded contracts
    (see sync_ctx_codes) and named by an admin afterwards."""
    __tablename__ = "ctx"
    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable identity from the identity provider (Entra "oid" claim). Never
    # key users on email: emails change, and Microsoft explicitly warns
    # against using the email claim for authorization.
    oid: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(10), default="user")        # admin | user
    status: Mapped[str] = mapped_column(String(10), default="pending")   # pending | active | disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ctx_links: Mapped[list["UserCtx"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )


class UserCtx(Base):
    __tablename__ = "user_ctx"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    ctx_code: Mapped[str] = mapped_column(ForeignKey("ctx.code"), primary_key=True)
    user: Mapped[User] = relationship(back_populates="ctx_links")
    ctx: Mapped[Ctx] = relationship(lazy="joined")


class AuditLog(Base):
    """Append-only: nothing in the app updates or deletes rows here."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ctx_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_db():
    with SessionLocal() as db:
        yield db


def audit(db: Session, actor: User | None, action: str, entity: str,
          entity_id=None, ctx_code: str | None = None, detail: dict | None = None) -> None:
    """Adds an audit row to the caller's transaction (no commit here, so the
    audit entry and the change it describes succeed or fail together)."""
    db.add(AuditLog(
        at=utcnow(),
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        entity=entity,
        entity_id=str(entity_id) if entity_id is not None else None,
        ctx_code=ctx_code,
        detail=json.dumps(detail) if detail is not None else None,
    ))


def reconcile_status(user: User) -> None:
    """A user is active iff they're an admin or hold at least one CTX;
    otherwise they're pending. 'disabled' is an explicit admin decision and
    is never changed implicitly."""
    if user.status == "disabled":
        return
    user.status = "active" if (user.role == "admin" or user.ctx_links) else "pending"


def sync_ctx_codes(db: Session, codes) -> list[str]:
    """Registers any CTX code seen in the loaded contracts that isn't in the
    ctx table yet (named after its own code until an admin renames it).
    Returns the codes it added."""
    existing = set(db.scalars(select(Ctx.code)))
    added = sorted(set(codes) - existing)
    for code in added:
        db.add(Ctx(code=code, name=code))
    return added
