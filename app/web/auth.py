"""Auth do admin: Argon2id + sessão cookie + CSRF + rate limit."""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import make_session_cookie, parse_session_cookie
from app.core.audit import write_event
from app.core.rate_limit import limiter
from app.core.telemetry import emit, hash_id
from app.db.models import Usuario

ph = PasswordHasher()


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(stored: str, candidate: str) -> bool:
    if not stored:
        return False
    try:
        ph.verify(stored, candidate)
        return True
    except VerifyMismatchError:
        return False


def login(s: Session, email: str, password: str, ip: str) -> str:
    """Retorna cookie de sessão se ok; lança 401 caso contrário."""
    if not limiter.allow(f"login:ip:{ip}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="muitas tentativas")
    if not limiter.allow(f"login:email:{email}"):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="muitas tentativas")
    u = s.query(Usuario).filter(Usuario.email == email, Usuario.ativo == 1).one_or_none()
    if u is None or not verify_password(u.password_hash or "", password):
        emit(
            "auth.login",
            email_hash=hash_id(email),
            ip=ip,
            sucesso=False,
            motivo_falha="credenciais",
        )
        write_event(s, acao="login_failure", payload={"email": email[:160]})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="credenciais inválidas")
    emit("auth.login", email_hash=hash_id(email), ip=ip, sucesso=True)
    write_event(s, usuario_id=u.id, acao="login_success", payload={"ip": ip})
    return make_session_cookie(u.id)


def session_user(s: Session, request: Request) -> Usuario | None:
    cookie = request.cookies.get("logfree_session")
    if not cookie:
        return None
    uid = parse_session_cookie(cookie)
    if uid is None:
        return None
    u = s.get(Usuario, uid)
    if u is None or not u.ativo or u.tombstoned:
        return None
    return u


def issue_csrf(session_uid: int) -> str:
    return secrets.token_urlsafe(24)


def check_csrf(request: Request, expected: str) -> bool:
    actual = request.headers.get("HX-CSRF-Token") or request.headers.get("X-CSRF-Token")
    if not actual or not expected:
        return False
    return secrets.compare_digest(actual, expected)
