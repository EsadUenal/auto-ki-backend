"""
Auth-Router — Phase 2b: Register / Login / Me / Logout

Token-Speicherung: httpOnly-Cookie (XSS-sicher, kein JS-Zugriff).
Passwort-Hashing:  bcrypt, cost 12.
JWT:               python-jose, HS256, 7-Tage-Ablauf (konfigurierbar).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator

from app.config import JWT_EXPIRE_DAYS, JWT_SECRET
from app.database import get_conn

router = APIRouter(prefix="/auth", tags=["auth"])

_BCRYPT_ROUNDS = 12


def _hash_pw(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(_BCRYPT_ROUNDS)).decode()


def _verify_pw(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hashed.encode())

COOKIE_NAME = "auth_token"
COOKIE_OPTS = dict(
    httponly=True,
    samesite="lax",
    secure=False,   # True setzen sobald HTTPS aktiv ist
    path="/",
)


# ── Schemas ──────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        parts = v.split("@")
        if len(parts) != 2 or "." not in parts[1]:
            raise ValueError("Ungültige E-Mail-Adresse")
        return v

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Passwort muss mindestens 8 Zeichen haben")
        return v


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePwBody(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_pw(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Neues Passwort muss mindestens 8 Zeichen haben")
        return v


class DeleteAccountBody(BaseModel):
    password: str


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _make_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Token ungültig oder abgelaufen."}},
        )


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=JWT_EXPIRE_DAYS * 86_400,
        **COOKIE_OPTS,
    )


# ── Shared Auth-Dependency (für andere Router) ────────────────────────────────

def get_current_user_id(auth_token: str | None = Cookie(default=None)) -> int:
    """FastAPI-Dependency: liest Auth-Cookie, gibt user_id zurück oder 401."""
    if not auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nicht eingeloggt."}},
        )
    payload = _decode_token(auth_token)
    return int(payload["sub"])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
def register(body: RegisterBody, response: Response):
    """Neuen Nutzer anlegen. Gibt User-Daten + setzt Auth-Cookie."""
    hashed = _hash_pw(body.password)
    try:
        with get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, checks_verbleibend, ersatzteil_suchen_verbleibend) "
                "VALUES (?, ?, 1, 1)",
                (body.email, hashed),
            )
            conn.commit()
            user_id: int = cursor.lastrowid  # type: ignore[assignment]
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "email_exists", "nachricht": "Diese E-Mail ist bereits registriert."}},
        )

    _set_auth_cookie(response, _make_token(user_id, body.email))
    return {
        "id": user_id, "email": body.email, "abo_typ": "none",
        "checks_verbleibend": 1, "ersatzteil_suchen_verbleibend": 1,
    }


@router.post("/login")
def login(body: LoginBody, response: Response):
    """Einloggen. Gibt User-Daten + setzt Auth-Cookie."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, abo_typ, checks_verbleibend, "
            "ersatzteil_suchen_verbleibend, deleted_at "
            "FROM users WHERE email = ?",
            (body.email.strip().lower(),),
        ).fetchone()

    # Gleiche Fehlermeldung für "nicht gefunden" und "falsches PW" → kein User-Enumeration
    if row is None or not _verify_pw(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "invalid_credentials", "nachricht": "E-Mail oder Passwort falsch."}},
        )

    if row["deleted_at"] is not None:
        raise HTTPException(
            status_code=403,
            detail={"fehler": {"code": "account_deactivated", "nachricht": "Dieses Konto wurde deaktiviert."}},
        )

    _set_auth_cookie(response, _make_token(row["id"], row["email"]))
    return {
        "id": row["id"],
        "email": row["email"],
        "abo_typ": row["abo_typ"],
        "checks_verbleibend": row["checks_verbleibend"],
        "ersatzteil_suchen_verbleibend": row["ersatzteil_suchen_verbleibend"],
        "abo_kuendigt_zum": None,
    }


@router.get("/me")
def me(auth_token: str | None = Cookie(default=None)):
    """Gibt Daten des eingeloggten Nutzers zurück (liest Cookie)."""
    if not auth_token:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nicht eingeloggt."}},
        )
    payload = _decode_token(auth_token)
    user_id = int(payload["sub"])

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, abo_typ, checks_verbleibend, ersatzteil_suchen_verbleibend, "
            "deleted_at, abo_kuendigt_zum "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if row is None or row["deleted_at"] is not None:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nutzer nicht gefunden."}},
        )
    return {
        "id": row["id"],
        "email": row["email"],
        "abo_typ": row["abo_typ"],
        "checks_verbleibend": row["checks_verbleibend"],
        "ersatzteil_suchen_verbleibend": row["ersatzteil_suchen_verbleibend"],
        "abo_kuendigt_zum": row["abo_kuendigt_zum"],
    }


@router.post("/logout")
def logout(response: Response):
    """Löscht den Auth-Cookie (clientseitig ausgeloggt)."""
    response.delete_cookie(COOKIE_NAME, **COOKIE_OPTS)
    return {"ok": True}


@router.post("/change-password")
def change_password(
    body: ChangePwBody,
    user_id: int = Depends(get_current_user_id),
):
    """Passwort ändern — altes PW prüfen, neues hashen und speichern."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)
        ).fetchone()

    if not row or not _verify_pw(body.old_password, row["password_hash"]):
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "wrong_password", "nachricht": "Aktuelles Passwort ist falsch."}},
        )

    new_hash = _hash_pw(body.new_password)
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user_id))
        conn.commit()

    return {"ok": True}


@router.delete("/delete-account")
def delete_account(
    body: DeleteAccountBody,
    response: Response,
    user_id: int = Depends(get_current_user_id),
):
    """
    Konto deaktivieren (Soft Delete).
    Setzt deleted_at — Login gesperrt, Daten bleiben erhalten (Geschäftsdaten).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)
        ).fetchone()

    if not row or not _verify_pw(body.password, row["password_hash"]):
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "wrong_password", "nachricht": "Passwort ist falsch."}},
        )

    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (user_id,)
        )
        conn.commit()

    response.delete_cookie(COOKIE_NAME, **COOKIE_OPTS)
    return {"ok": True}


