"""
Auth-Router — Phase 2b: Register / Login / Me / Logout

Token-Speicherung: httpOnly-Cookie (XSS-sicher, kein JS-Zugriff).
Passwort-Hashing:  bcrypt, cost 12.
JWT:               python-jose, HS256, 7-Tage-Ablauf (konfigurierbar).
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator
from slowapi import Limiter
from app.client_ip import limit_schluessel

from app.config import (
    COOKIE_SECURE, EMAIL_MAX_ZEICHEN, EMAIL_VERIFIKATION_AKTIV,
    EMAIL_VERIFIKATION_GUELTIG_STUNDEN, IS_PRODUCTION, JWT_EXPIRE_DAYS, JWT_SECRET,
    PASSWORT_MAX_BYTES, PASSWORT_MIN_ZEICHEN,
)
from app.database import get_conn
from app.einwilligung import require_agb, record as record_einwilligung, ART_AGB
from app.entitlements import has_dealer_access

router = APIRouter(prefix="/auth", tags=["auth"])
# Eigene Limiter-Instanz wie in chat.py/kaufcheck.py/verkaufscheck.py — dediziertes,
# strengeres Limit für Login/Registrierung (Brute-Force- bzw. Spam-Schutz) zusätzlich
# zum globalen Default-Limit (siehe app/main.py SlowAPIMiddleware).
limiter = Limiter(key_func=limit_schluessel)

_BCRYPT_ROUNDS = 12


def _hash_pw(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt(_BCRYPT_ROUNDS)).decode()


def _verify_pw(password: str, hashed: str) -> bool:
    """Passwortvergleich, der auch unmoegliche Eingaben aushaelt (P2-4).

    bcrypt 5 wirft bei mehr als 72 Byte einen Fehler — beim Login kam das als
    HTTP 500 beim Nutzer an. Ein solches Passwort kann nie gesetzt worden sein
    (die Registrierung lehnt es ab), also ist die richtige Antwort schlicht
    "stimmt nicht" und damit 401 statt 500.
    """
    roh = password.encode("utf-8")
    if len(roh) > PASSWORT_MAX_BYTES:
        return False
    return _bcrypt.checkpw(roh, hashed.encode())

COOKIE_NAME = "auth_token"
COOKIE_OPTS = dict(
    httponly=True,
    samesite="lax",
    # Produktion: nur ueber HTTPS (Secure). Lokal ueber http://localhost False,
    # sonst verwirft der Browser den Cookie. Quelle: app.config.COOKIE_SECURE.
    secure=COOKIE_SECURE,
    path="/",
)


# ── Eingabepruefung (Security Block 3, P2-4) ─────────────────────────────────
#
# bcrypt hasht hoechstens 72 BYTES; bcrypt 5 wirft darueber einen Fehler, der
# vorher als HTTP 500 beim Nutzer ankam. Die Grenze wird deshalb als normale
# Eingabepruefung erzwungen (422), nicht durch einen Wechsel des Verfahrens.
# Gezaehlt werden BYTES in UTF-8: ein Passwort aus Emojis oder Umlauten ist
# deutlich laenger als seine Zeichenzahl.

def _pruefe_passwort(v: str, feld: str = "Passwort") -> str:
    if len(v) < PASSWORT_MIN_ZEICHEN:
        raise ValueError(f"{feld} muss mindestens {PASSWORT_MIN_ZEICHEN} Zeichen haben")
    if len(v.encode("utf-8")) > PASSWORT_MAX_BYTES:
        raise ValueError(
            f"{feld} darf höchstens {PASSWORT_MAX_BYTES} Byte lang sein "
            f"(Umlaute und Emojis zählen mehrfach)"
        )
    return v


def _pruefe_email(v: str) -> str:
    v = v.strip().lower()
    if len(v) > EMAIL_MAX_ZEICHEN:
        raise ValueError(f"E-Mail-Adresse darf höchstens {EMAIL_MAX_ZEICHEN} Zeichen haben")
    parts = v.split("@")
    if len(parts) != 2 or not parts[0] or "." not in parts[1]:
        raise ValueError("Ungültige E-Mail-Adresse")
    return v


# ── Token-Entwertung (Security Block 3, P2-3) ────────────────────────────────
#
# Ein JWT ist zustandslos: Logout, Passwortwechsel und Kontoloeschung konnten es
# bisher nicht zurueckziehen — ein kopierter Token blieb bis zu 7 Tage gueltig,
# selbst fuer ein geloeschtes Konto. Statt einer Session-Datenbank traegt jedes
# Konto eine `auth_version`; sie steht im Token und wird bei jedem
# authentifizierten Request gegen die Datenbank geprueft. Erhoehen entwertet
# alle bisher ausgestellten Token dieses Kontos sofort.
#
# Der Client kann daran nichts drehen: die Version ist Teil der signierten
# Nutzdaten, und der Vergleichswert kommt aus der Datenbank.

def _erhoehe_auth_version(user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET auth_version = auth_version + 1 WHERE id=?", (user_id,))
        conn.commit()


def _auth_version(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT auth_version FROM users WHERE id=?", (user_id,)).fetchone()
    return int(row["auth_version"]) if row else 1


# ── E-Mail-Verifikation (Security Block 3, P2-5) ─────────────────────────────
#
# Registrieren bleibt offen; nur die KOSTENLOSEN, LLM-gestuetzten Kontingente
# haengen an einer bestaetigten Adresse (siehe app/usage_limit.py). Ohne das
# konnte ein Angreifer beliebig viele Wegwerfkonten anlegen und je Konto
# Gemini-/Tavily-Aufrufe verbrauchen.
#
# Gespeichert wird nur der SHA-256-Hash des Tokens — wer die Datenbank liest,
# kann damit kein Konto freischalten. Der Token ist zeitlich begrenzt und genau
# einmal einloesbar.
#
# Der Mailversand selbst gehoert in den spaeteren Provider-/Deployment-Block.
# Bis dahin gilt: in der Entwicklung wird der Token in der Antwort
# zurueckgegeben (bequemer Testweg), in PRODUKTION niemals.

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def erzeuge_verifikationstoken(user_id: int) -> str:
    """Neuer Einmal-Token. Frueher ausgestellte, noch offene Token verfallen."""
    token = secrets.token_urlsafe(32)
    ablauf = (datetime.now(timezone.utc) + timedelta(hours=EMAIL_VERIFIKATION_GUELTIG_STUNDEN)
              ).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE email_verifikation SET eingeloest_at = CURRENT_TIMESTAMP "
            "WHERE user_id=? AND eingeloest_at IS NULL", (user_id,))
        conn.execute(
            "INSERT INTO email_verifikation (token_hash, user_id, laeuft_ab_at) VALUES (?,?,?)",
            (_token_hash(token), user_id, ablauf))
        conn.commit()
    return token


def _loese_verifikation_ein(token: str) -> int | None:
    """Loest einen Token ein. Rueckgabe: user_id bei Erfolg, sonst None.

    Atomar: `WHERE eingeloest_at IS NULL AND laeuft_ab_at > jetzt` — ein zweiter
    Versuch mit demselben Token aendert nichts mehr. Unbekannte, abgelaufene und
    manipulierte Werte sind vom Ergebnis her ununterscheidbar (kein Orakel).
    """
    if not token or len(token) > 512:
        return None
    jetzt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    h = _token_hash(token)
    with get_conn() as conn:
        row = conn.execute("SELECT user_id FROM email_verifikation WHERE token_hash=?", (h,)).fetchone()
        if not row:
            return None
        cur = conn.execute(
            "UPDATE email_verifikation SET eingeloest_at = CURRENT_TIMESTAMP "
            "WHERE token_hash=? AND eingeloest_at IS NULL AND laeuft_ab_at > ?",
            (h, jetzt))
        if cur.rowcount != 1:
            conn.commit()
            return None
        conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (row["user_id"],))
        conn.commit()
    return int(row["user_id"])


# ── Schemas ──────────────────────────────────────────────────────────────────

class RegisterBody(BaseModel):
    email: str
    password: str
    agb_akzeptiert: bool = False   # AGB + Datenschutz — Pflicht bei Registrierung

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _pruefe_email(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _pruefe_passwort(v)


class LoginBody(BaseModel):
    email: str
    password: str


class ChangePwBody(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _new_pw(cls, v: str) -> str:
        return _pruefe_passwort(v, "Neues Passwort")


class DeleteAccountBody(BaseModel):
    password: str


# ── JWT helpers ───────────────────────────────────────────────────────────────

def _make_token(user_id: int, email: str, auth_version: int | None = None) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        # P2-3: Stand der Token-Generation. Passt er nicht mehr zur Datenbank,
        # ist der Token entwertet (Logout/Passwortwechsel/Loeschung).
        "ver": _auth_version(user_id) if auth_version is None else auth_version,
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

def _nicht_eingeloggt() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"fehler": {"code": "unauthorized", "nachricht": "Nicht eingeloggt."}},
    )


def geprueftes_konto(token: str | None) -> sqlite3.Row | None:
    """Vollstaendige Pruefung eines Tokens gegen die Datenbank (P2-3).

    Gueltige Signatur allein reicht NICHT. Es muss ausserdem gelten:
      - das Konto existiert,
      - es ist nicht geloescht (`deleted_at`),
      - die Token-Version stimmt mit `users.auth_version` ueberein.

    Rueckgabe: die Nutzerzeile oder None. EINE Stelle fuer alle Aufrufer —
    Dependency, /me und der Kontingent-Anker lesen denselben Zustand.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, abo_typ, checks_verbleibend, ersatzteil_suchen_verbleibend, "
            "kaufchecks_verbleibend, verkaufschecks_verbleibend, deleted_at, abo_kuendigt_zum, "
            "ist_haendler, auth_version, email_verified, plus_period_end "
            "FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if row is None or row["deleted_at"] is not None:
        return None
    # Aeltere Token ohne "ver" gelten als Version 1 — sie stammen aus der Zeit
    # vor dieser Aenderung und bleiben bis zum naechsten Logout gueltig.
    if int(payload.get("ver", 1)) != int(row["auth_version"]):
        return None
    return row


def user_id_aus_token(token: str | None) -> int | None:
    """user_id eines gueltigen Tokens, sonst None (ohne Login zu erzwingen)."""
    row = geprueftes_konto(token)
    return int(row["id"]) if row else None


def get_current_user_id(auth_token: str | None = Cookie(default=None)) -> int:
    """FastAPI-Dependency: liest Auth-Cookie, gibt user_id zurück oder 401."""
    if not auth_token:
        raise _nicht_eingeloggt()
    row = geprueftes_konto(auth_token)
    if row is None:
        # Abgelaufen, entwertet (Logout/Passwortwechsel) oder Konto geloescht.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Sitzung ungültig. Bitte neu anmelden."}},
        )
    return int(row["id"])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
@limiter.limit("10/minute")
def register(body: RegisterBody, response: Response, request: Request):
    """Neuen Nutzer anlegen. Gibt User-Daten + setzt Auth-Cookie."""
    require_agb(body.agb_akzeptiert)
    hashed = _hash_pw(body.password)
    try:
        with get_conn() as conn:
            # Consumer Pricing V1: KEIN automatischer Gratis-Check mehr.
            # Kauf- und VerkaufsCheck sind getrennt bepreiste Einmalprodukte
            # (5,99 / 8,99 EUR); ein pauschal verschenkter generischer Check
            # passte in keines von beiden und widerspraeche der Preisseite,
            # die keinen Gratis-Check bewirbt. Die typgebundenen Spalten
            # starten ohnehin per Schema-Default auf 0.
            #
            # WICHTIG: Das aendert ausschliesslich NEUE Konten. Bestehende
            # generische Guthaben bleiben unangetastet — es gibt bewusst KEINE
            # Migration, die vorhandene `checks_verbleibend` zurueksetzt.
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, checks_verbleibend, ersatzteil_suchen_verbleibend) "
                "VALUES (?, ?, 0, 1)",
                (body.email, hashed),
            )
            conn.commit()
            user_id: int = cursor.lastrowid  # type: ignore[assignment]
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "email_exists", "nachricht": "Diese E-Mail ist bereits registriert."}},
        )

    record_einwilligung(user_id, ART_AGB, "registrierung")
    _set_auth_cookie(response, _make_token(user_id, body.email, auth_version=1))

    # P2-5: Das Konto startet UNBESTAETIGT. Anmelden, Kaufen und alles Bezahlte
    # funktioniert sofort; nur die kostenlosen LLM-Kontingente warten auf die
    # bestaetigte Adresse (siehe app/usage_limit.py). Der Mailversand kommt im
    # spaeteren Provider-Block — bis dahin gibt NUR die Entwicklung den Token
    # direkt zurueck, damit der Weg testbar bleibt.
    token = erzeuge_verifikationstoken(user_id) if EMAIL_VERIFIKATION_AKTIV else None
    antwort = {
        "id": user_id, "email": body.email, "abo_typ": "none",
        "checks_verbleibend": 0, "ersatzteil_suchen_verbleibend": 1,
        "kaufchecks_verbleibend": 0, "verkaufschecks_verbleibend": 0,
        "ist_haendler": False,
        "dealer_access": has_dealer_access("none", False),
        "email_verified": not EMAIL_VERIFIKATION_AKTIV,
    }
    if token and not IS_PRODUCTION:
        antwort["verifikationstoken_dev"] = token
    return antwort


@router.post("/login")
@limiter.limit("10/minute")
def login(body: LoginBody, response: Response, request: Request):
    """Einloggen. Gibt User-Daten + setzt Auth-Cookie."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, abo_typ, checks_verbleibend, "
            "kaufchecks_verbleibend, verkaufschecks_verbleibend, "
            "ersatzteil_suchen_verbleibend, deleted_at, ist_haendler, "
            "auth_version, email_verified "
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

    _set_auth_cookie(response, _make_token(row["id"], row["email"], row["auth_version"]))
    return {
        "id": row["id"],
        "email": row["email"],
        "email_verified": bool(row["email_verified"]),
        "abo_typ": row["abo_typ"],
        "checks_verbleibend": row["checks_verbleibend"],
        "kaufchecks_verbleibend": row["kaufchecks_verbleibend"],
        "verkaufschecks_verbleibend": row["verkaufschecks_verbleibend"],
        "ersatzteil_suchen_verbleibend": row["ersatzteil_suchen_verbleibend"],
        "abo_kuendigt_zum": None,
        "ist_haendler": bool(row["ist_haendler"]),
        "dealer_access": has_dealer_access(row["abo_typ"], row["ist_haendler"]),
    }


@router.get("/me")
def me(auth_token: str | None = Cookie(default=None)):
    """Gibt Daten des eingeloggten Nutzers zurück (liest Cookie).

    P2-3: Prueft wie jeder andere geschuetzte Endpunkt ueber `geprueftes_konto`
    — Existenz, `deleted_at` UND Token-Version. Ein entwerteter Token ist hier
    damit genauso wertlos wie ueberall sonst.
    """
    if not auth_token:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Nicht eingeloggt."}},
        )
    row = geprueftes_konto(auth_token)
    if row is None:
        raise HTTPException(
            status_code=401,
            detail={"fehler": {"code": "unauthorized", "nachricht": "Sitzung ungültig. Bitte neu anmelden."}},
        )
    user_id = int(row["id"])
    return {
        "email_verified": bool(row["email_verified"]),
        "id": row["id"],
        "email": row["email"],
        "abo_typ": row["abo_typ"],
        "checks_verbleibend": row["checks_verbleibend"],
        "kaufchecks_verbleibend": row["kaufchecks_verbleibend"],
        "verkaufschecks_verbleibend": row["verkaufschecks_verbleibend"],
        "ersatzteil_suchen_verbleibend": row["ersatzteil_suchen_verbleibend"],
        "abo_kuendigt_zum": row["abo_kuendigt_zum"],
        "ist_haendler": bool(row["ist_haendler"]),
        "dealer_access": has_dealer_access(row["abo_typ"], row["ist_haendler"]),
        # Plus-Status und Monatsverbrauch: das Frontend zeigt daraus die
        # Kontingentanzeige und entscheidet, ob eine Plus-CTA sinnvoll ist.
        **_plus_und_nutzung(user_id),
    }


def _plus_und_nutzung(user_id: int) -> dict:
    """Plus-Status + Monatsverbrauch. Lokaler Import gegen Zirkelbezug
    (usage_limit liest seinerseits den Token-Decoder aus diesem Modul)."""
    from app import plus as plus_modul
    from app.usage_limit import nutzung
    try:
        return {**plus_modul.status(user_id), **nutzung(user_id)}
    except Exception:
        return {}


@router.post("/logout")
def logout(response: Response, auth_token: str | None = Cookie(default=None)):
    """Meldet ab und ENTWERTET den Token serverseitig (P2-3).

    Den Cookie zu loeschen genuegte nicht: wer ihn vorher kopiert hatte (fremdes
    Geraet, Logfile, Backup), konnte ihn bis zu 7 Tage weiterverwenden. Das
    Erhoehen der `auth_version` macht JEDEN bisher ausgestellten Token dieses
    Kontos sofort ungueltig — auch auf anderen Geraeten.
    """
    user_id = user_id_aus_token(auth_token)
    if user_id is not None:
        _erhoehe_auth_version(user_id)
    response.delete_cookie(COOKIE_NAME, **COOKIE_OPTS)
    return {"ok": True}


@router.post("/change-password")
def change_password(
    body: ChangePwBody,
    response: Response,
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

    # P2-3: Ein Passwortwechsel soll gestohlene Sitzungen beenden — dafuer
    # muessen ALLE bisherigen Token fallen. Das eigene Geraet bekommt sofort
    # einen frischen Token, bleibt also angemeldet.
    _erhoehe_auth_version(user_id)
    with get_conn() as conn:
        zeile = conn.execute("SELECT email, auth_version FROM users WHERE id=?", (user_id,)).fetchone()
    _set_auth_cookie(response, _make_token(user_id, zeile["email"], zeile["auth_version"]))
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
            "UPDATE users SET deleted_at=CURRENT_TIMESTAMP, auth_version = auth_version + 1 "
            "WHERE id=?", (user_id,)
        )
        conn.commit()

    # P2-3: `deleted_at` allein reichte nicht — die Auth-Dependency las es frueher
    # gar nicht, ein alter Token kam also weiter durch. Jetzt greift beides:
    # geloeschtes Konto UND entwertete Token-Version.
    response.delete_cookie(COOKIE_NAME, **COOKIE_OPTS)
    return {"ok": True}


# ── E-Mail-Verifikation (P2-5) ───────────────────────────────────────────────

class VerifyEmailBody(BaseModel):
    token: str


@router.post("/verify-email")
@limiter.limit("10/minute")
def verify_email(body: VerifyEmailBody, request: Request):
    """Bestaetigt eine E-Mail-Adresse per Einmal-Token.

    Bewusst OHNE Login: der Link aus der Mail soll auch in einem anderen Browser
    funktionieren. Der Token ist das Geheimnis; er ist einmal einloesbar,
    zeitlich begrenzt und nur als Hash gespeichert.
    """
    user_id = _loese_verifikation_ein(body.token)
    if user_id is None:
        raise HTTPException(
            status_code=400,
            detail={"fehler": {"code": "token_ungueltig",
                               "nachricht": "Dieser Bestätigungslink ist ungültig oder abgelaufen."}},
        )
    return {"ok": True, "email_verified": True}


@router.post("/resend-verification")
@limiter.limit("5/minute")
def resend_verification(request: Request, user_id: int = Depends(get_current_user_id)):
    """Stellt einen neuen Bestaetigungs-Token aus (der alte verfaellt dabei)."""
    with get_conn() as conn:
        row = conn.execute("SELECT email_verified FROM users WHERE id=?", (user_id,)).fetchone()
    if row and row["email_verified"]:
        return {"ok": True, "email_verified": True}
    token = erzeuge_verifikationstoken(user_id)
    antwort = {"ok": True, "email_verified": False}
    # Nur in der Entwicklung: ohne angeschlossenen Mailversand waere der Weg
    # sonst nicht testbar. In Produktion verlaesst der Token den Server nur
    # per Mail (Provider-Block).
    if not IS_PRODUCTION:
        antwort["verifikationstoken_dev"] = token
    return antwort

