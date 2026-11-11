"""Opaque server-side sessions, Argon2 passwords and CSRF checks. No model keys in UI."""

import hashlib
import hmac
import secrets
import time
from typing import Literal

from fastapi import HTTPException, Request
from pwdlib import PasswordHash
from pydantic import Field, SecretStr
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from .domain import Strict
from .persistence import login_limits, sessions, users
from .store import Conflict

hasher = PasswordHash.recommended()
dummy_hash = hasher.hash(secrets.token_urlsafe(24))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Login(Strict):
    username: str = Field(pattern=r"^[a-zA-Z0-9_.-]{3,64}$")
    password: SecretStr


class UserCreate(Login):
    role: Literal["admin", "operator", "reviewer"] = "operator"


class Identity:
    def __init__(self, store, settings):
        self.store, self.s = store, settings

    def create_user(self, username, password, role):
        if len(password) < 12 or role not in ("admin", "operator", "reviewer"):
            raise ValueError("Use a valid role and password with at least 12 characters")
        password_hash = hasher.hash(password)
        try:
            with self.store.connect(write=True) as db:
                db.execute(
                    insert(users).values(
                        username=username,
                        password_hash=password_hash,
                        role=role,
                        active=1,
                        created=time.time(),
                    )
                )
        except IntegrityError:
            raise Conflict("Username already exists") from None

    def login(self, username, password, client):
        # Limit per account and client. Persistent across API restarts.
        now, bucket = time.time(), digest(username + "|" + client)
        with self.store.connect(write=True) as db:
            limit = db.execute(
                select(login_limits).where(login_limits.c.key == bucket).with_for_update()
            ).first()
            if limit and limit.until > now and limit.failures >= 5:
                raise HTTPException(429, "登录尝试过多，请15分钟后重试")
            row = db.execute(select(users).where(users.c.username == username)).first()
            valid = hasher.verify(password, row.password_hash if row else dummy_hash)
            if not valid or not row or not row.active:
                count = limit.failures + 1 if limit and limit.until > now else 1
                if limit:
                    db.execute(
                        update(login_limits)
                        .where(login_limits.c.key == bucket)
                        .values(failures=count, until=now + 900)
                    )
                else:
                    db.execute(insert(login_limits).values(key=bucket, failures=count, until=now + 900))
                # Raising after committing would lose counters if done inside transaction.
                principal = None
            else:
                db.execute(delete(login_limits).where(login_limits.c.key == bucket))
                token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                db.execute(delete(sessions).where(sessions.c.expires < now))
                db.execute(
                    insert(sessions).values(
                        token_hash=digest(token),
                        username=username,
                        csrf_hash=digest(csrf),
                        expires=now + self.s.session_seconds,
                        created=now,
                    )
                )
                principal = {"username": row.username, "role": row.role, "csrf": csrf}
        if principal is None:
            self.store.audit(username, "login_failed")
            raise HTTPException(401, "用户名或密码错误")
        self.store.audit(username, "login")
        return token, principal

    def authenticate(self, request: Request, roles):
        supplied = request.headers.get("x-api-key", "")
        if supplied:
            if hmac.compare_digest(supplied, self.s.reviewer_key.get_secret_value()):
                actor = {"username": "integration-reviewer", "role": "reviewer", "service": True}
            elif hmac.compare_digest(supplied, self.s.api_key.get_secret_value()):
                actor = {"username": "integration-operator", "role": "operator", "service": True}
            else:
                raise HTTPException(401, "Invalid credentials")
        else:
            token = request.cookies.get("tariff_session", "")
            with self.store.connect() as db:
                row = db.execute(
                    select(sessions.c.username, sessions.c.csrf_hash, users.c.role)
                    .join(users, users.c.username == sessions.c.username)
                    .where(
                        sessions.c.token_hash == digest(token),
                        sessions.c.expires > time.time(),
                        users.c.active == 1,
                    )
                ).first()
            if not row:
                raise HTTPException(401, "请先登录")
            actor = {"username": row.username, "role": row.role, "service": False}
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                csrf = request.headers.get("x-csrf-token", "")
                if not csrf or not hmac.compare_digest(digest(csrf), row.csrf_hash):
                    raise HTTPException(403, "CSRF validation failed")
        if actor["role"] not in roles and actor["role"] != "admin":
            raise HTTPException(403, "当前账号没有操作权限")
        request.state.actor = actor
        return actor

    def logout(self, token):
        with self.store.connect() as db:
            db.execute(delete(sessions).where(sessions.c.token_hash == digest(token)))

    def list_users(self):
        with self.store.connect() as db:
            return [
                dict(r._mapping)
                for r in db.execute(select(users.c.username, users.c.role, users.c.active, users.c.created))
            ]
