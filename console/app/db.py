"""The console's database: API clients, the Traffic Scraper servers behind it, the request
log, console users, settings and a tamper-evident audit trail (SQLite by
default, one file in GM_DATA_DIR)."""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (Boolean, DateTime, Integer, LargeBinary, String, Text, TypeDecorator, create_engine, event,
                        select)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from . import crypto
from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


class EncryptedStr(TypeDecorator):
    """A string encrypted with GM_SECRET_KEY (a server's key)."""

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> bytes | None:
        return None if value is None else crypto.encrypt(str(value).encode())

    def process_result_value(self, value: bytes | None, dialect: Any) -> str | None:
        return None if value is None else crypto.decrypt(value).decode()


class Base(DeclarativeBase):
    pass


class ApiClient(Base):
    """An app that may call the service (GET.ride, GET.teksi, ...): one key each."""
    __tablename__ = "api_clients"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16))
    rate_per_minute: Mapped[int] = mapped_column(Integer, default=300)
    created_by: Mapped[str | None] = mapped_column(String(60), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)


class Backend(Base):
    """A Traffic Scraper behind the service (the Mac mini's, the NAS's):
    asked in `position` order, the next one when it doesn't answer."""
    __tablename__ = "backends"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(300))
    key: Mapped[str | None] = mapped_column(EncryptedStr)  # its ROUTE_API_KEY, sent as X-Route-Key
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=4000)
    rest_seconds: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # the minute-by-minute health check
    health_state: Mapped[str | None] = mapped_column(String(20))  # online | offline
    health_detail: Mapped[str | None] = mapped_column(String(300))
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    failing_since: Mapped[datetime | None] = mapped_column(DateTime)


class RequestLog(Base):
    """One call to the service. No coordinates, places or keys: which app, which
    endpoint, which server answered, how it went and how long it took."""
    __tablename__ = "request_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    client_id: Mapped[str | None] = mapped_column(String(40), index=True)
    endpoint: Mapped[str] = mapped_column(String(40))
    status: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, index=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    backend_id: Mapped[str | None] = mapped_column(String(40), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(String(300))


class AdminUser(Base):
    """A person who signs in to the console (password hashed with scrypt)."""
    __tablename__ = "admin_users"
    username: Mapped[str] = mapped_column(String(60), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="viewer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)  # JSON


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60))
    target: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[str | None] = mapped_column(Text)  # never a key
    # Hash chain: each row's hash covers its content and the previous row's, so
    # editing or deleting an old row breaks every hash after it.
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    row_hash: Mapped[str | None] = mapped_column(String(64))


_engine = None
_Session: sessionmaker[Session] | None = None


def init_db() -> None:
    global _engine, _Session
    _last_hash[0] = None
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if s.db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    _engine = create_engine(s.db_url, **kwargs)
    if s.db_url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _pragmas(conn, _):  # noqa: ANN001
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    assert _Session is not None, "init_db() not called"
    db = _Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    with session_scope() as db:
        yield db


# ---- settings the console can change ------------------------------------------------------------

EDITABLE = {
    "total_timeout_ms": (1000, 30000),
    "default_rate_per_minute": (1, 100000),
    "log_retention_days": (1, 365),
}


def get_setting(db: Session, key: str) -> int:
    row = db.get(Setting, key)
    if row is not None:
        try:
            return int(json.loads(row.value))
        except (ValueError, TypeError):
            pass
    return int(getattr(get_settings(), key))


def set_setting(db: Session, key: str, value: int) -> int:
    lo, hi = EDITABLE[key]
    value = max(lo, min(hi, int(value)))
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=json.dumps(value)))
    else:
        row.value = json.dumps(value)
    return value


# ---- audit trail --------------------------------------------------------------------------------

_audit_lock = threading.Lock()
_last_hash: list[str | None] = [None]


def _digest(prev: str | None, at: datetime, actor: str, action: str, target: str | None, detail: str | None) -> str:
    payload = "\x1f".join([prev or "", at.isoformat(), actor, action, target or "", detail or ""])
    return hashlib.sha256(payload.encode()).hexdigest()


def audit(db: Session, actor: str, action: str, target: str | None = None, detail: str | None = None) -> None:
    with _audit_lock:
        if _last_hash[0] is None:
            _last_hash[0] = db.scalar(select(AuditLog.row_hash).order_by(AuditLog.id.desc()).limit(1)) or "genesis"
        at = utcnow()
        h = _digest(_last_hash[0], at, actor, action, target, detail)
        db.add(AuditLog(at=at, actor=actor, action=action, target=target, detail=detail,
                        prev_hash=_last_hash[0], row_hash=h))
        _last_hash[0] = h


def verify_audit_chain(db: Session) -> dict:
    """Recompute every hash: the first row that does not fit shows where the trail was changed."""
    prev = None
    n = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        expected_prev = prev or row.prev_hash
        if row.prev_hash != expected_prev or row.row_hash != _digest(row.prev_hash, row.at, row.actor, row.action,
                                                                      row.target, row.detail):
            return {"ok": False, "rows_checked": n, "broken_at_id": row.id}
        prev = row.row_hash
        n += 1
    return {"ok": True, "rows_checked": n}
