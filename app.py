"""REAL-MATH AR — API educativa. Uso académico; configurar HTTPS/SMTP antes de publicar."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
from pathlib import Path
import secrets
import smtplib
import sqlite3
import ssl
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Literal
from urllib.parse import quote, urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ==========================================================
# CONFIGURACIÓN PRINCIPAL
# ==========================================================

ROOT = Path(__file__).resolve().parent

load_dotenv(ROOT / ".env")

DB_PATH = Path(
    os.environ.get("DATABASE_PATH", "data/real_math_ar.db")
)

if not DB_PATH.is_absolute():
    DB_PATH = ROOT / DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

DEV_MODE = (
    os.environ.get("APP_ENV", "development").lower()
    == "development"
)

COOKIE_SECURE = (
    os.environ.get(
        "COOKIE_SECURE",
        "false" if DEV_MODE else "true"
    ).lower()
    == "true"
)

COOKIE_NAME = "rma_session"

SESSION_LIFETIME = 60 * 60 * 24 * 7
RESET_LIFETIME = 60 * 30

BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8000"
).rstrip("/")

EMAIL_GENERIC = (
    "Si el correo existe, recibirás instrucciones "
    "para recuperar la contraseña."
)

KEYS = {
    "geo": [1, 1, 0, 1],
    "alg": [1, 0, 2, 1],
    "calc": [0, 0, 1, 0]
}

KEYS["all"] = (
    KEYS["geo"] + KEYS["alg"] + KEYS["calc"]
)

STUDY_KEYS = [1, 2, 0, 1, 2, 1]

RATE: dict[str, deque] = defaultdict(deque)


# ==========================================================
# BASE DE DATOS
# ==========================================================

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")

    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            csrf_hash TEXT NOT NULL,
            csrf_token TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL,
            used_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            resource TEXT NOT NULL,
            question INTEGER NOT NULL,
            choice INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            timestamp INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS study_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            stage TEXT NOT NULL
                CHECK(stage IN ('pre', 'post')),
            choices TEXT NOT NULL,
            score INTEGER NOT NULL,
            consented_at INTEGER NOT NULL,
            timestamp INTEGER NOT NULL,
            UNIQUE(user_id, stage)
        );

        CREATE TABLE IF NOT EXISTS perceptions (
            user_id INTEGER PRIMARY KEY
                REFERENCES users(id) ON DELETE CASCADE,
            values_json TEXT NOT NULL,
            timestamp INTEGER NOT NULL
        );
        """)


# ==========================================================
# INICIALIZACIÓN DE FASTAPI
# ==========================================================

init_db()

app = FastAPI(
    title="REAL-MATH AR API",
    version="3.0.0",
    docs_url="/api/docs" if DEV_MODE else None,
    redoc_url=None,
    openapi_url=(
        "/api/openapi.json" if DEV_MODE else None
    )
)

app.mount(
    "/assets",
    StaticFiles(directory=ROOT / "static" / "assets"),
    name="assets"
)


@app.middleware("http")
async def default_headers(request: Request, call_next):
    if request.method in (
        "POST", "PUT", "PATCH", "DELETE"
    ):
        origin = request.headers.get("origin")

        if origin:
            o = urlsplit(origin)
            h = request.url.hostname

            origin_port = o.port or (
                443 if o.scheme == "https" else 80
            )

            request_port = request.url.port or (
                443 if request.url.scheme == "https" else 80
            )

            if (
                o.hostname != h
                or origin_port != request_port
            ):
                return Response(
                    "Origen no permitido",
                    status_code=403
                )

    response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(self), microphone=()"
    )
    response.headers["Cache-Control"] = (
        "no-store"
        if request.url.path.startswith("/api/")
        else "no-cache"
    )

    return response


# ==========================================================
# PÁGINAS DE LA PLATAFORMA
# ==========================================================

@app.get("/")
def home():
    return FileResponse(
        ROOT / "static" / "index.html",
        media_type="text/html"
    )


@app.get("/ar")
def augmented_reality_webcam():
    return FileResponse(
        ROOT / "static" / "ar.html",
        media_type="text/html"
    )


@app.get("/marcador")
def print_hiro_marker():
    return FileResponse(
        ROOT / "static" / "marcador.html",
        media_type="text/html"
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "REAL-MATH AR"
    }


# ==========================================================
# SEGURIDAD Y CONTRASEÑAS
# ==========================================================

def now():
    return int(time.time())


def digest(token: str):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def pass_hash(password: str) -> str:
    salt = secrets.token_bytes(16)

    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
        dklen=32
    )

    return (
        f"scrypt$16384$8$1$"
        f"{salt.hex()}${key.hex()}"
    )


def pass_verify(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, expected = stored.split("$")

        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=32
        )

        return hmac.compare_digest(
            actual,
            bytes.fromhex(expected)
        )

    except (ValueError, TypeError, MemoryError):
        return False


def limit(
    request: Request,
    scope: str,
    count: int = 8,
    seconds: int = 900
):
    ip = (
        request.client.host
        if request.client
        else "unknown"
    )

    key = f"{scope}:{ip}"
    timestamps = RATE[key]

    t = time.monotonic()

    while timestamps and timestamps[0] < t - seconds:
        timestamps.popleft()

    if len(timestamps) >= count:
        raise HTTPException(
            429,
            "Demasiados intentos. "
            "Vuelve a intentar más tarde."
        )

    timestamps.append(t)


# ==========================================================
# MODELOS DE DATOS DE AUTENTICACIÓN
# ==========================================================

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(StrictModel):
    name: str = Field(min_length=2, max_length=90)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)


class Login(StrictModel):
    email: str
    password: str


class Forgot(StrictModel):
    email: str


class Reset(StrictModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=10, max_length=128)


class Answer(StrictModel):
    resource: Literal["geo", "alg", "calc", "all"]
    question: int = Field(ge=0, le=99)
    choice: int = Field(ge=0, le=9)


class Study(StrictModel):
    stage: Literal["pre", "post"]
    answers: list[int] = Field(
        min_length=6,
        max_length=6
    )
    consent: bool


class Perception(StrictModel):
    scores: list[int] = Field(
        min_length=4,
        max_length=4
    )


def normalize_email(email: str):
    return email.strip().lower()


def valid_password(password: str):
    if (
        len(password) < 10
        or len(password) > 128
        or not any(x.isalpha() for x in password)
        or not any(x.isdigit() for x in password)
    ):
        raise HTTPException(
            422,
            "La contraseña debe contener "
            "al menos 10 caracteres, letras y números."
        )


# ==========================================================
# SESIONES
# ==========================================================

def session_reply(
    response: Response,
    user: sqlite3.Row
):
    secret = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)

    with db() as con:
        con.execute(
            "DELETE FROM sessions WHERE expires_at < ?",
            (now(),)
        )

        con.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?)",
            (
                digest(secret),
                user["id"],
                digest(csrf),
                csrf,
                now() + SESSION_LIFETIME
            )
        )

    response.set_cookie(
        COOKIE_NAME,
        secret,
        max_age=SESSION_LIFETIME,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/"
    )

    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "bio": user["bio"],
        "csrf": csrf
    }


def current_user(request: Request):
    token = request.cookies.get(
        COOKIE_NAME,
        ""
    )

    if not token:
        raise HTTPException(
            401,
            "Inicia sesión para continuar."
        )

    with db() as con:
        row = con.execute(
            """
            SELECT u.*, s.csrf_token, s.csrf_hash
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
              AND s.expires_at > ?
            """,
            (digest(token), now())
        ).fetchone()

    if not row:
        raise HTTPException(
            401,
            "Tu sesión caducó. "
            "Inicia sesión nuevamente."
        )

    return row


def csrf_check(
    request: Request,
    user=Depends(current_user)
):
    token = request.headers.get(
        "X-CSRF-Token",
        ""
    )

    if (
        not token
        or not hmac.compare_digest(
            digest(token),
            user["csrf_hash"]
        )
    ):
        raise HTTPException(
            403,
            "Token de sesión no válido."
        )

    return user


# ==========================================================
# REGISTRO DE ESTUDIANTES
# ==========================================================

@app.post("/api/register", status_code=201)
def register(
    data: Register,
    request: Request,
    response: Response
):
    limit(request, "register", 10, 3600)
    valid_password(data.password)

    name = " ".join(
        data.name.strip().split()
    )

    email = normalize_email(data.email)

    if (
        "@" not in email
        or " " in email
        or "." not in email.split("@")[-1]
    ):
        raise HTTPException(
            422,
            "Correo electrónico no válido."
        )

    with db() as con:
        try:
            con.execute(
                """
                INSERT INTO users(
                    name, email, password_hash, created_at
                )
                VALUES(?,?,?,?)
                """,
                (
                    name,
                    email,
                    pass_hash(data.password),
                    now()
                )
            )

        except sqlite3.IntegrityError:
            raise HTTPException(
                409,
                "Ya existe una cuenta registrada "
                "con ese correo."
            )

        user = con.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

    return session_reply(response, user)


# ==========================================================
# INICIO DE SESIÓN
# ==========================================================

@app.post("/api/login")
def login(
    data: Login,
    request: Request,
    response: Response
):
    limit(request, "login", 10, 900)

    email = normalize_email(data.email)

    with db() as con:
        user = con.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        ).fetchone()

    if user is None:
        pass_hash(data.password)

    if (
        user is None
        or not pass_verify(
            data.password,
            user["password_hash"]
        )
    ):
        raise HTTPException(
            401,
            "Correo o contraseña incorrectos."
        )

    return session_reply(response, user)


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "bio": user["bio"],
        "csrf": user["csrf_token"]
    }


@app.post("/api/logout")
def logout(
    request: Request,
    response: Response,
    user=Depends(csrf_check)
):
    with db() as con:
        con.execute(
            "DELETE FROM sessions WHERE token_hash=?",
            (
                digest(
                    request.cookies.get(COOKIE_NAME, "")
                ),
            )
        )

    response.delete_cookie(
        COOKIE_NAME,
        path="/"
    )

    return {"ok": True}


# ==========================================================
# RECUPERACIÓN DE CONTRASEÑAS
# ==========================================================

def deliver_reset(email: str, url: str):
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM")

    if host and sender:
        msg = EmailMessage()

        msg["Subject"] = (
            "Recuperación de contraseña — REAL-MATH AR"
        )

        msg["From"] = sender
        msg["To"] = email

        msg.set_content(
            "Has solicitado restablecer la contraseña.\n\n"
            "Abre este enlace (caduca en 30 minutos "
            "y se puede usar una sola vez):\n"
            + url +
            "\n\nSi no realizaste la solicitud, "
            "ignora este mensaje."
        )

        port = int(
            os.environ.get("SMTP_PORT", "587")
        )

        if (
            os.environ.get(
                "SMTP_STARTTLS",
                "true"
            ).lower() == "true"
        ):
            with smtplib.SMTP(
                host,
                port,
                timeout=10
            ) as smtp:
                smtp.starttls(
                    context=ssl.create_default_context()
                )

                if user:
                    smtp.login(user, password or "")

                smtp.send_message(msg)

        else:
            with smtplib.SMTP_SSL(
                host,
                port,
                timeout=10
            ) as smtp:
                if user:
                    smtp.login(user, password or "")

                smtp.send_message(msg)

    elif DEV_MODE:
        log = ROOT / "data" / "dev_mail.log"
        log.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        with log.open(
            "a",
            encoding="utf8"
        ) as file:
            file.write(
                f"{datetime.now(timezone.utc).isoformat()}"
                f" | {email} | {url}\n"
            )

        try:
            os.chmod(log, 0o600)
        except OSError:
            pass

    else:
        raise RuntimeError(
            "SMTP no configurado en producción"
        )


@app.post("/api/forgot")
def forgot(
    data: Forgot,
    request: Request
):
    limit(request, "forgot", 5, 3600)

    user = None

    with db() as con:
        user = con.execute(
            "SELECT id,email FROM users WHERE email=?",
            (normalize_email(data.email),)
        ).fetchone()

        if user:
            token = secrets.token_urlsafe(40)

            con.execute(
                "DELETE FROM password_resets WHERE user_id=?",
                (user["id"],)
            )

            con.execute(
                "INSERT INTO password_resets VALUES(?,?,?,NULL)",
                (
                    digest(token),
                    user["id"],
                    now() + RESET_LIFETIME
                )
            )

    if user:
        link = (
            BASE_URL
            + "/?reset_token="
            + quote(token)
        )

        try:
            deliver_reset(user["email"], link)

        except (
            OSError,
            smtplib.SMTPException,
            RuntimeError
        ):
            import logging

            logging.getLogger(
                "real_math_ar"
            ).exception(
                "Falló el envío SMTP de recuperación"
            )

    return {
        "message": EMAIL_GENERIC
    }


@app.post("/api/reset-password")
def reset_password(
    data: Reset,
    request: Request
):
    limit(request, "reset", 10, 900)
    valid_password(data.password)

    with db() as con:
        r = con.execute(
            """
            SELECT *
            FROM password_resets
            WHERE token_hash=?
              AND used_at IS NULL
              AND expires_at>?
            """,
            (digest(data.token), now())
        ).fetchone()

        if not r:
            raise HTTPException(
                400,
                "El enlace es inválido o ha caducado."
            )

        con.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (
                pass_hash(data.password),
                r["user_id"]
            )
        )

        con.execute(
            """
            UPDATE password_resets
            SET used_at=?
            WHERE token_hash=?
            """,
            (
                now(),
                digest(data.token)
            )
        )

        con.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (r["user_id"],)
        )

    return {
        "message": (
            "Contraseña actualizada. "
            "Inicia sesión nuevamente."
        )
    }


# ==========================================================
# RESPUESTAS Y PROGRESO BÁSICO
# ==========================================================

@app.get("/api/progress")
def get_progress(user=Depends(current_user)):
    with db() as con:
        rows = con.execute(
            """
            SELECT
                resource,
                COUNT(*) AS answered,
                SUM(correct) AS correct
            FROM answers
            WHERE user_id=?
            GROUP BY resource
            """,
            (user["id"],)
        ).fetchall()

    by = {
        k: {
            "answered": 0,
            "correct": 0
        }
        for k in KEYS
    }

    for r in rows:
        by[r["resource"]] = {
            "answered": r["answered"],
            "correct": r["correct"]
        }

    return {
        "answered": sum(
            x["answered"] for x in by.values()
        ),
        "correct": sum(
            x["correct"] for x in by.values()
        ),
        "byResource": by
    }


@app.post("/api/answers")
def answer(
    data: Answer,
    user=Depends(csrf_check)
):
    keys = KEYS[data.resource]

    if (
        data.question >= len(keys)
        or data.choice >= 3
    ):
        raise HTTPException(
            422,
            "Pregunta o respuesta fuera de rango."
        )

    correct = (
        data.choice == keys[data.question]
    )

    with db() as con:
        con.execute(
            """
            INSERT INTO answers(
                user_id, resource, question,
                choice, correct, timestamp
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                user["id"],
                data.resource,
                data.question,
                data.choice,
                int(correct),
                now()
            )
        )

    return {
        "correct": correct,
        "correct_choice": keys[data.question]
    }


@app.post("/api/progress/reset")
def reset_progress(user=Depends(csrf_check)):
    with db() as con:
        con.execute(
            "DELETE FROM answers WHERE user_id=?",
            (user["id"],)
        )

    return {"ok": True}


# ==========================================================
# INVESTIGACIÓN: PRETEST Y POSTEST
# ==========================================================

@app.post("/api/study/submit")
def study_submit(
    data: Study,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    if not data.consent:
        raise HTTPException(
            422,
            "Es necesario aceptar "
            "el consentimiento informado."
        )

    if any(
        not isinstance(x, int) or x < 0 or x > 2
        for x in data.answers
    ):
        raise HTTPException(
            422,
            "Respuestas inválidas."
        )

    score = sum(
        int(a == b)
        for a, b in zip(
            data.answers,
            STUDY_KEYS
        )
    )

    with db() as con:
        if data.stage == "post":
            pre = con.execute(
                """
                SELECT id FROM study_submissions
                WHERE user_id=? AND stage=?
                """,
                (user["id"], "pre")
            ).fetchone()

            if not pre:
                raise HTTPException(
                    409,
                    "Realiza primero el pretest."
                )

        try:
            con.execute(
                """
                INSERT INTO study_submissions(
                    user_id, stage, choices, score,
                    consented_at, timestamp
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    user["id"],
                    data.stage,
                    json.dumps(data.answers),
                    score,
                    now(),
                    now()
                )
            )

        except sqlite3.IntegrityError:
            raise HTTPException(
                409,
                "Ya respondiste esta fase del estudio."
            )

    return {
        "score": score,
        "total": len(STUDY_KEYS)
    }


@app.get("/api/study/status")
def study_status(user=Depends(current_user)):
    require_role(user, "student")

    with db() as con:
        stages = con.execute(
            """
            SELECT stage, score, timestamp
            FROM study_submissions
            WHERE user_id=?
            """,
            (user["id"],)
        ).fetchall()

        survey = con.execute(
            """
            SELECT 1
            FROM perceptions
            WHERE user_id=?
            """,
            (user["id"],)
        ).fetchone()

    return {
        "stages": {
            r["stage"]: {
                "score": r["score"],
                "submitted_at": r["timestamp"]
            }
            for r in stages
        },
        "survey_done": bool(survey)
    }


@app.post("/api/study/perception")
def perception(
    data: Perception,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    if any(
        type(x) is not int or x < 1 or x > 5
        for x in data.scores
    ):
        raise HTTPException(
            422,
            "Respuestas de percepción inválidas."
        )

    with db() as con:
        post = con.execute(
            """
            SELECT id
            FROM study_submissions
            WHERE user_id=? AND stage=?
            """,
            (user["id"], "post")
        ).fetchone()

        if not post:
            raise HTTPException(
                409,
                "Completa primero el postest."
            )

        con.execute(
            """
            INSERT INTO perceptions(
                user_id, values_json, timestamp
            )
            VALUES(?,?,?)
            ON CONFLICT(user_id)
            DO UPDATE SET
                values_json=excluded.values_json,
                timestamp=excluded.timestamp
            """,
            (
                user["id"],
                json.dumps(data.scores),
                now()
            )
        )

    return {"ok": True}


@app.get("/api/admin/export.csv")
def export_study(user=Depends(current_user)):
    admins = [
        normalize_email(x)
        for x in os.environ.get(
            "ADMIN_EMAILS",
            ""
        ).split(",")
        if x.strip()
    ]

    if (
        not admins
        or user["email"] not in admins
    ):
        raise HTTPException(
            403,
            "No autorizado."
        )

    with db() as con:
        rows = con.execute(
            """
            SELECT
                s.user_id,
                s.stage,
                s.choices,
                s.score,
                s.timestamp,
                p.values_json
            FROM study_submissions s
            LEFT JOIN perceptions p
              ON p.user_id=s.user_id
            ORDER BY s.user_id,s.stage
            """
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "participante_anonimo",
        "etapa",
        "respuestas",
        "puntaje",
        "fecha_unix",
        "percepcion"
    ])

    for r in rows:
        writer.writerow([
            f'P{r["user_id"]:05}',
            r["stage"],
            r["choices"],
            r["score"],
            r["timestamp"],
            r["values_json"] or ""
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; "
                "filename=real_math_ar_estudio.csv"
            )
        }
    )


# ==========================================================
# V3: ROLES, CURSOS, CONTENIDOS Y EVALUACIONES
# ==========================================================

MODELS = {
    "pitagoras": "pitagoras.glb",
    "identidad_algebraica": "identidad_algebraica.glb",
    **{
        f"calculo_{k}": f"calculo_{k}.glb"
        for k in (-2, -1, 0, 1, 2)
    }
}

SUBJECTS = {
    "geometria",
    "algebra",
    "calculo",
    "otras"
}

# Códigos docentes independientes de los códigos privados por curso.
TEACHER_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_teacher_code(con):
    """Genera un código docente único y no reservado por ningún curso."""
    for _ in range(100):
        code = "".join(secrets.choice(TEACHER_CODE_CHARS) for _ in range(8))
        used_by_teacher = con.execute(
            "SELECT 1 FROM users WHERE teacher_code=?", (code,)
        ).fetchone()
        used_by_course = con.execute(
            "SELECT 1 FROM courses WHERE access_code=?", (code,)
        ).fetchone()
        if not used_by_teacher and not used_by_course:
            return code
    raise RuntimeError("No se pudo generar un código docente único.")


# ==========================================================
# CREACIÓN Y ACTUALIZACIÓN DE TABLAS V3
# ==========================================================

def init_v3():
    with db() as con:
        cols = {
            r["name"]
            for r in con.execute(
                "PRAGMA table_info(users)"
            )
        }

        if "role" not in cols:
            con.execute(
                """
                ALTER TABLE users
                ADD COLUMN role TEXT
                NOT NULL DEFAULT 'student'
                """
            )

        if "bio" not in cols:
            con.execute(
                """
                ALTER TABLE users
                ADD COLUMN bio TEXT
                NOT NULL DEFAULT ''
                """
            )

        con.executescript("""
        CREATE TABLE IF NOT EXISTS teacher_invites(
            token_hash TEXT PRIMARY KEY,
            expires_at INTEGER NOT NULL,
            used_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS courses(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            teacher_id INTEGER
                REFERENCES users(id) ON DELETE CASCADE,
            subject TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            published INTEGER NOT NULL DEFAULT 0,
            access_code TEXT UNIQUE,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS enrollments(
            student_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            course_id INTEGER NOT NULL
                REFERENCES courses(id) ON DELETE CASCADE,
            joined_at INTEGER NOT NULL,
            PRIMARY KEY(student_id,course_id)
        );

        CREATE TABLE IF NOT EXISTS lessons(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL
                REFERENCES courses(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            ar_model TEXT NOT NULL DEFAULT 'none',
            support_url TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            published INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lesson_progress(
            student_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            lesson_id INTEGER NOT NULL
                REFERENCES lessons(id) ON DELETE CASCADE,
            completed_at INTEGER NOT NULL,
            PRIMARY KEY(student_id,lesson_id)
        );

        CREATE TABLE IF NOT EXISTS quizzes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL
                REFERENCES courses(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL
                CHECK(kind IN ('practice','exam')),
            time_limit INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            published INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS quiz_questions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL
                REFERENCES quizzes(id) ON DELETE CASCADE,
            prompt TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer_index INTEGER NOT NULL,
            explanation TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS quiz_attempts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quiz_id INTEGER NOT NULL
                REFERENCES quizzes(id) ON DELETE CASCADE,
            student_id INTEGER NOT NULL
                REFERENCES users(id) ON DELETE CASCADE,
            started_at INTEGER NOT NULL,
            submitted_at INTEGER,
            choices_json TEXT,
            score INTEGER,
            total INTEGER
        );

        CREATE INDEX IF NOT EXISTS ix_quiz_attempt_user
            ON quiz_attempts(student_id,quiz_id);

        CREATE INDEX IF NOT EXISTS ix_lessons_course
            ON lessons(course_id);
        """)

        # Migración no destructiva: añade código a profesores nuevos y antiguos.
        # Las tablas de cursos, lecciones y exámenes no se modifican.
        user_columns = {
            column["name"] for column in con.execute("PRAGMA table_info(users)")
        }
        if "teacher_code" not in user_columns:
            con.execute("ALTER TABLE users ADD COLUMN teacher_code TEXT")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_teacher_code "
            "ON users(teacher_code)"
        )
        previous_teachers = con.execute(
            "SELECT id FROM users WHERE role='teacher' "
            "AND (teacher_code IS NULL OR teacher_code='')"
        ).fetchall()
        for teacher in previous_teachers:
            con.execute(
                "UPDATE users SET teacher_code=? WHERE id=?",
                (new_teacher_code(con), teacher["id"])
            )

        result = con.execute(
            "SELECT COUNT(*) FROM courses"
        ).fetchone()

        if result[0] == 0:
            seed_courses(con)


# ==========================================================
# CURSOS INICIALES DE DEMOSTRACIÓN
# ==========================================================

def seed_courses(con):
    """
    Material inicial demostrativo.
    No constituye resultado de investigación.
    """

    catalogs = [
        (
            "geometria",
            "Geometría interactiva",
            "Teoremas, visualización espacial "
            "y problemas con figuras.",
            [
                (
                    "Teorema de Pitágoras y sus hipótesis",
                    "Objetivo: reconocer la condición "
                    "de ángulo recto.\n\n"
                    "En todo triángulo rectángulo, "
                    "el cuadrado de la hipotenusa "
                    "equivale a la suma de los cuadrados "
                    "de los catetos: a²+b²=c². "
                    "La hipotenusa es el lado opuesto "
                    "a los 90°.\n\n"
                    "Ejemplo: para catetos 3 y 4, "
                    "c=√(9+16)=5.\n\n"
                    "Explora el modelo 3D para comparar áreas.",
                    "pitagoras"
                ),
                (
                    "Demostración geométrica por áreas",
                    "Idea: construye un cuadrado de lado a+b "
                    "y distribuye cuatro triángulos congruentes. "
                    "Comparar dos formas de calcular su área "
                    "permite obtener a²+b²=c².\n\n"
                    "Distingue el enunciado, las hipótesis "
                    "y el encadenamiento lógico "
                    "de cada igualdad.",
                    "pitagoras"
                ),
                (
                    "Semejanza de triángulos",
                    "Dos triángulos son semejantes cuando "
                    "sus ángulos correspondientes son iguales "
                    "y los lados guardan proporción.\n\n"
                    "Ejemplo: si el factor de escala es 2, "
                    "un lado de longitud 3 pasa a medir 6.",
                    "none"
                ),
                (
                    "Vectores en el plano",
                    "Un vector puede describirse por "
                    "componentes (x,y). "
                    "Su módulo es √(x²+y²).\n\n"
                    "Ejemplo: |(3,4)|=5. "
                    "Compara esta norma con la "
                    "relación de Pitágoras.",
                    "pitagoras"
                )
            ]
        ),
        (
            "algebra",
            "Álgebra y demostraciones",
            "Identidades, transformación de expresiones "
            "y razonamiento lógico.",
            [
                (
                    "Identidad del cuadrado de un binomio",
                    "Objetivo: demostrar "
                    "(a+b)²=a²+2ab+b² mediante áreas.\n\n"
                    "Paso 1: (a+b)²=(a+b)(a+b).\n"
                    "Paso 2: aplicar distributividad: "
                    "a²+ab+ba+b².\n"
                    "Paso 3: usando conmutatividad, "
                    "ab+ba=2ab.\n"
                    "Conclusión: a²+2ab+b².\n\n"
                    "El modelo 3D exhibe cuatro regiones.",
                    "identidad_algebraica"
                ),
                (
                    "Factorización por diferencia de cuadrados",
                    "Identidad: a²−b²=(a−b)(a+b).\n\n"
                    "Demostración: "
                    "(a−b)(a+b)=a²+ab−ab−b²=a²−b².\n\n"
                    "Practica la justificación "
                    "de la cancelación de términos.",
                    "identidad_algebraica"
                ),
                (
                    "Ecuaciones lineales",
                    "Para resolver 2x+3=11, resta 3 "
                    "a ambos lados y divide por 2: x=4.\n\n"
                    "Cada transformación debe conservar "
                    "la igualdad; explica la propiedad utilizada.",
                    "none"
                ),
                (
                    "Transformaciones lineales",
                    "Una transformación T es lineal si "
                    "T(u+v)=T(u)+T(v) "
                    "y T(αu)=αT(u).\n\n"
                    "Ejemplo: T(x,y)=(2x,2y) es lineal; "
                    "T(x,y)=(x+1,y) no lo es.",
                    "none"
                )
            ]
        ),
        (
            "calculo",
            "Cálculo en 3D",
            "Funciones, derivadas, superficies "
            "y su interpretación visual.",
            [
                (
                    "Parábolas y parámetros",
                    "Analiza f(x)=ax². "
                    "Cuando a>0 abre hacia arriba; "
                    "si a<0 abre hacia abajo; "
                    "si a=0 se convierte en la función cero.\n\n"
                    "Explora además z=a(x²+y²), "
                    "una superficie paraboloide "
                    "representada por modelos discretos.",
                    "calculo_1"
                ),
                (
                    "Derivada como tasa de cambio",
                    "La derivada f′(x) es el límite de "
                    "la pendiente de las rectas secantes "
                    "cuando la separación tiende a cero.\n\n"
                    "Para f(x)=x², f′(x)=2x; "
                    "en x=3, la pendiente es 6.",
                    "none"
                ),
                (
                    "Superficies en tres dimensiones",
                    "La función z=x²+y² tiene un mínimo "
                    "en (0,0,0) y crece alejándose del origen. "
                    "Sus curvas de nivel son circunferencias.",
                    "calculo_1"
                ),
                (
                    "Gradiente y puntos críticos",
                    "Para f(x,y)=x²+y², ∇f=(2x,2y). "
                    "El gradiente apunta en la dirección "
                    "de incremento más rápido.\n\n"
                    "En (0,0) el gradiente se anula.",
                    "calculo_1"
                )
            ]
        )
    ]

    qs = [
        [
            (
                "¿Cuándo se aplica directamente el teorema "
                "de Pitágoras?",
                [
                    "En un triángulo rectángulo",
                    "En cualquier triángulo",
                    "Solo en un cuadrado",
                    "Solo en un círculo"
                ],
                0,
                "El ángulo recto es indispensable."
            ),
            (
                "Catetos 5 y 12: ¿cuál es la hipotenusa?",
                ["12", "13", "17", "25"],
                1,
                "√(25+144)=13."
            ),
            (
                "Si dos triángulos son semejantes, "
                "sus lados correspondientes son...",
                [
                    "iguales siempre",
                    "perpendiculares",
                    "proporcionales",
                    "todos distintos"
                ],
                2,
                "La semejanza preserva ángulos y razones."
            )
        ],
        [
            (
                "¿Cuál es el desarrollo de (a+b)²?",
                [
                    "a²+b²",
                    "a²+2ab+b²",
                    "2a+2b",
                    "a²+ab+b²"
                ],
                1,
                "Hay dos productos cruzados ab."
            ),
            (
                "¿Cuánto vale (x−3)(x+3)?",
                [
                    "x²−9",
                    "x²+9",
                    "x²−6",
                    "2x²"
                ],
                0,
                "Es una diferencia de cuadrados."
            ),
            (
                "¿Qué propiedad transforma "
                "a(b+c) en ab+ac?",
                [
                    "Asociativa",
                    "Conmutativa",
                    "Distributiva",
                    "Idempotente"
                ],
                2,
                "Se distribuye la multiplicación "
                "sobre la suma."
            )
        ],
        [
            (
                "Si a es negativo en f(x)=ax², "
                "la gráfica...",
                [
                    "abre hacia abajo",
                    "abre hacia arriba",
                    "es una recta siempre",
                    "no existe"
                ],
                0,
                "El signo de a invierte la orientación."
            ),
            (
                "Derivada de f(x)=x²:",
                ["x", "2x", "x³", "2"],
                1,
                "Regla de la potencia: 2x."
            ),
            (
                "∇(x²+y²) en (1,2) es:",
                ["(1,2)", "(0,0)", "(2,4)", "(4,2)"],
                2,
                "Deriva parcialmente: (2x,2y)."
            )
        ]
    ]

    for i, (
        subject,
        title,
        description,
        lessons
    ) in enumerate(catalogs):

        cur = con.execute(
            """
            INSERT INTO courses(
                teacher_id,
                subject,
                title,
                description,
                published,
                access_code,
                created_at,
                updated_at
            )
            VALUES(NULL,?,?,?,?,NULL,?,?)
            """,
            (
                subject,
                title,
                description,
                1,
                now(),
                now()
            )
        )

        course_id = cur.lastrowid

        for pos, (
            lesson_title,
            body,
            model
        ) in enumerate(lessons, 1):

            con.execute(
                """
                INSERT INTO lessons(
                    course_id,
                    title,
                    body,
                    ar_model,
                    position,
                    published,
                    created_at
                )
                VALUES(?,?,?,?,?,1,?)
                """,
                (
                    course_id,
                    lesson_title,
                    body,
                    model,
                    pos,
                    now()
                )
            )

        for kind in ("practice", "exam"):
            quiz_title = (
                "Práctica guiada"
                if kind == "practice"
                else "Examen de repaso"
            )

            cur = con.execute(
                """
                INSERT INTO quizzes(
                    course_id,
                    title,
                    instructions,
                    kind,
                    time_limit,
                    max_attempts,
                    published,
                    created_at
                )
                VALUES(?,?,?,?,?,?,1,?)
                """,
                (
                    course_id,
                    quiz_title,
                    "Responde las preguntas para "
                    "evaluar tus conocimientos.",
                    kind,
                    0 if kind == "practice" else 20,
                    5 if kind == "practice" else 2,
                    now()
                )
            )

            quiz_id = cur.lastrowid

            for idx, (
                prompt,
                options,
                correct,
                explanation
            ) in enumerate(qs[i], 1):

                con.execute(
                    """
                    INSERT INTO quiz_questions(
                        quiz_id,
                        prompt,
                        options_json,
                        answer_index,
                        explanation,
                        position
                    )
                    VALUES(?,?,?,?,?,?)
                    """,
                    (
                        quiz_id,
                        prompt,
                        json.dumps(
                            options,
                            ensure_ascii=False
                        ),
                        correct,
                        explanation,
                        idx
                    )
                )


# ==========================================================
# MODELOS V3
# ==========================================================

class TeacherRegistration(StrictModel):
    name: str = Field(min_length=2, max_length=90)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)

    # Opcional para compatibilidad.
    # En desarrollo no se requiere.
    invitation: str | None = Field(
        default=None,
        max_length=150
    )


class ProfileUpdate(StrictModel):
    name: str = Field(min_length=2, max_length=90)
    bio: str = Field(default="", max_length=750)


class EmailUpdate(StrictModel):
    email: EmailStr
    current_password: str


class ChangePassword(StrictModel):
    current_password: str
    new_password: str = Field(
        min_length=10,
        max_length=128
    )


class DeleteAccount(StrictModel):
    password: str
    confirmation: Literal["ELIMINAR"]


class CourseCreate(StrictModel):
    title: str = Field(min_length=4, max_length=110)
    description: str = Field(default="", max_length=1800)
    subject: Literal[
        "geometria",
        "algebra",
        "calculo",
        "otras"
    ]
    published: bool = False
    private: bool = False


class CourseEdit(StrictModel):
    title: str = Field(min_length=4, max_length=110)
    description: str = Field(default="", max_length=1800)
    subject: Literal[
        "geometria",
        "algebra",
        "calculo",
        "otras"
    ]
    published: bool = False


class Enrollment(StrictModel):
    code: str = Field(default="", max_length=64)
    course_id: int | None = Field(default=None, ge=1)


class TeacherCodeEdit(StrictModel):
    code: str = Field(min_length=6, max_length=16)


class LessonEdit(StrictModel):
    title: str = Field(min_length=4, max_length=110)
    body: str = Field(min_length=10, max_length=10000)
    ar_model: str = Field(default="none", max_length=100)
    support_url: str = Field(default="", max_length=500)
    position: int = Field(default=0, ge=0, le=999)
    published: bool = True


class QuizEdit(StrictModel):
    title: str = Field(min_length=4, max_length=110)
    instructions: str = Field(default="", max_length=1500)
    kind: Literal["practice", "exam"] = "practice"
    time_limit: int = Field(default=0, ge=0, le=180)
    max_attempts: int = Field(default=3, ge=1, le=20)
    published: bool = False


class QuizQuestion(StrictModel):
    prompt: str = Field(min_length=5, max_length=1000)
    options: list[str] = Field(min_length=2, max_length=4)
    answer_index: int = Field(ge=0, le=3)
    explanation: str = Field(default="", max_length=1000)
    position: int = Field(default=0, ge=0, le=999)


class SubmitQuiz(StrictModel):
    choices: dict[str, int]


# ==========================================================
# CONTROL DE ROLES Y ACCESO A CURSOS
# ==========================================================

def require_role(user, role):
    if user["role"] != role:
        label = (
            "profesor"
            if role == "teacher"
            else "estudiante"
        )

        raise HTTPException(
            403,
            "Esta acción requiere el rol de "
            + label + "."
        )


def own_course(con, user, course_id):
    require_role(user, "teacher")

    row = con.execute(
        """
        SELECT *
        FROM courses
        WHERE id=? AND teacher_id=?
        """,
        (course_id, user["id"])
    ).fetchone()

    if row is None:
        raise HTTPException(
            404,
            "No se encontró el curso "
            "o no tienes permisos."
        )

    return row


def course_access(con, user, course_id):
    row = con.execute(
        "SELECT * FROM courses WHERE id=?",
        (course_id,)
    ).fetchone()

    if not row:
        raise HTTPException(
            404,
            "Curso inexistente."
        )

    if user["role"] == "teacher":
        if row["teacher_id"] != user["id"]:
            raise HTTPException(
                403,
                "No puedes consultar un curso ajeno."
            )

    else:
        enrolled = con.execute(
            """
            SELECT 1
            FROM enrollments
            WHERE course_id=? AND student_id=?
            """,
            (course_id, user["id"])
        ).fetchone()

        if not row["published"] or not enrolled:
            raise HTTPException(
                403,
                "Debes inscribirte "
                "en el curso publicado."
            )

    return row


def validate_lesson(data: LessonEdit):
    if (
        data.ar_model not in MODELS
        and data.ar_model != "none"
    ):
        raise HTTPException(
            422,
            "Selecciona uno de los modelos "
            "3D incorporados."
        )

    if data.support_url:
        url = urlsplit(data.support_url)

        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
        ):
            raise HTTPException(
                422,
                "El recurso externo debe ser "
                "un enlace HTTPS válido."
            )


def valid_question(data: QuizQuestion):
    if (
        data.answer_index >= len(data.options)
        or any(
            len(x.strip()) < 1 or len(x) > 220
            for x in data.options
        )
    ):
        raise HTTPException(
            422,
            "Las opciones y la respuesta correcta "
            "no son válidas."
        )


# ==========================================================
# REGISTRO DIRECTO DE PROFESORES
# ==========================================================

@app.post("/api/register-teacher", status_code=201)
def register_teacher(
    data: TeacherRegistration,
    request: Request,
    response: Response
):
    """
    En desarrollo local:
        registro directo del profesor.

    En producción:
        se exige invitación válida para proteger
        los permisos docentes.
    """

    limit(
        request,
        "teacher-register",
        10 if DEV_MODE else 5,
        3600
    )

    valid_password(data.password)

    name = " ".join(data.name.split())

    if len(name) < 2:
        raise HTTPException(
            422,
            "Escribe tu nombre completo."
        )

    email = normalize_email(data.email)

    if (
        "@" not in email
        or " " in email
        or "." not in email.split("@")[-1]
    ):
        raise HTTPException(
            422,
            "Correo inválido."
        )

    with db() as con:

        # En modo local no se necesita invitación.
        if not DEV_MODE:
            if not data.invitation:
                raise HTTPException(
                    403,
                    "En producción se requiere "
                    "invitación docente."
                )

            invite = con.execute(
                """
                SELECT *
                FROM teacher_invites
                WHERE token_hash=?
                  AND used_at IS NULL
                  AND expires_at>?
                """,
                (
                    digest(data.invitation),
                    now()
                )
            ).fetchone()

            if not invite:
                raise HTTPException(
                    400,
                    "Invitación inválida, "
                    "vencida o utilizada."
                )

        try:
            teacher_code = new_teacher_code(con)
            cur = con.execute(
                """
                INSERT INTO users(
                    name,
                    email,
                    password_hash,
                    created_at,
                    role,
                    bio,
                    teacher_code
                )
                VALUES(?,?,?,?, 'teacher','',?)
                """,
                (
                    name,
                    email,
                    pass_hash(data.password),
                    now(),
                    teacher_code
                )
            )

        except sqlite3.IntegrityError:
            raise HTTPException(
                409,
                "Ya existe una cuenta con ese correo."
            )

        if not DEV_MODE:
            con.execute(
                """
                UPDATE teacher_invites
                SET used_at=?
                WHERE token_hash=?
                  AND used_at IS NULL
                """,
                (
                    now(),
                    digest(data.invitation)
                )
            )

        user = con.execute(
            "SELECT * FROM users WHERE id=?",
            (cur.lastrowid,)
        ).fetchone()

    return session_reply(response, user)


# ==========================================================
# CÓDIGO ÚNICO DE INSCRIPCIÓN DEL PROFESOR
# ==========================================================

@app.get("/api/teacher/join-code")
def read_teacher_join_code(user=Depends(current_user)):
    require_role(user, "teacher")
    return {"code": user["teacher_code"]}


@app.patch("/api/teacher/join-code")
def update_teacher_join_code(
    data: TeacherCodeEdit,
    user=Depends(csrf_check)
):
    require_role(user, "teacher")
    code = data.code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{6,16}", code):
        raise HTTPException(
            422,
            "Usa de 6 a 16 letras mayúsculas o números, sin espacios."
        )
    with db() as con:
        if con.execute(
            "SELECT 1 FROM courses WHERE access_code=?", (code,)
        ).fetchone():
            raise HTTPException(409, "El código ya pertenece a un curso.")
        if con.execute(
            "SELECT 1 FROM users WHERE teacher_code=? AND id<>?",
            (code, user["id"])
        ).fetchone():
            raise HTTPException(409, "El código ya pertenece a otro profesor.")
        con.execute(
            "UPDATE users SET teacher_code=? WHERE id=?",
            (code, user["id"])
        )
    return {"ok": True, "code": code}


@app.post("/api/teacher/join-code/rotate")
def rotate_teacher_join_code(user=Depends(csrf_check)):
    require_role(user, "teacher")
    with db() as con:
        new_code = new_teacher_code(con)
        con.execute(
            "UPDATE users SET teacher_code=? WHERE id=?",
            (new_code, user["id"])
        )
    return {"ok": True, "code": new_code}


# ==========================================================
# ACTUALIZACIÓN DEL PERFIL
# ==========================================================

@app.patch("/api/profile")
def update_profile(
    data: ProfileUpdate,
    user=Depends(csrf_check)
):
    with db() as con:
        con.execute(
            "UPDATE users SET name=?,bio=? WHERE id=?",
            (
                " ".join(data.name.split()),
                data.bio.strip(),
                user["id"]
            )
        )

    return {"ok": True}


@app.patch("/api/profile/email")
def change_email(
    data: EmailUpdate,
    response: Response,
    user=Depends(csrf_check)
):
    if not pass_verify(
        data.current_password,
        user["password_hash"]
    ):
        raise HTTPException(
            401,
            "Contraseña incorrecta."
        )

    new = normalize_email(str(data.email))

    with db() as con:
        try:
            con.execute(
                "UPDATE users SET email=? WHERE id=?",
                (new, user["id"])
            )
        except sqlite3.IntegrityError:
            raise HTTPException(
                409,
                "Ese correo ya está registrado."
            )

        con.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (user["id"],)
        )

    response.delete_cookie(
        COOKIE_NAME,
        path="/"
    )

    return {
        "message": (
            "Correo actualizado. "
            "Inicia sesión de nuevo."
        )
    }


@app.post("/api/profile/password")
def change_password(
    data: ChangePassword,
    response: Response,
    user=Depends(csrf_check)
):
    valid_password(data.new_password)

    if not pass_verify(
        data.current_password,
        user["password_hash"]
    ):
        raise HTTPException(
            401,
            "Contraseña incorrecta."
        )

    with db() as con:
        con.execute(
            """
            UPDATE users
            SET password_hash=?
            WHERE id=?
            """,
            (
                pass_hash(data.new_password),
                user["id"]
            )
        )

        con.execute(
            "DELETE FROM sessions WHERE user_id=?",
            (user["id"],)
        )

        con.execute(
            "DELETE FROM password_resets WHERE user_id=?",
            (user["id"],)
        )

    response.delete_cookie(
        COOKIE_NAME,
        path="/"
    )

    return {
        "message": (
            "Contraseña actualizada. "
            "Vuelve a iniciar sesión."
        )
    }


@app.delete("/api/profile")
def delete_profile(
    data: DeleteAccount,
    response: Response,
    user=Depends(csrf_check)
):
    if not pass_verify(
        data.password,
        user["password_hash"]
    ):
        raise HTTPException(
            401,
            "Contraseña incorrecta."
        )

    with db() as con:
        con.execute(
            "DELETE FROM users WHERE id=?",
            (user["id"],)
        )

    response.delete_cookie(
        COOKIE_NAME,
        path="/"
    )

    return {
        "message": "Cuenta y datos asociados eliminados."
    }


# ==========================================================
# CATÁLOGO DE CURSOS
# ==========================================================

@app.get("/api/catalog")
def catalog(user=Depends(current_user)):
    with db() as con:
        if user["role"] == "teacher":
            rows = con.execute(
                """
                SELECT
                    c.*,
                    u.name AS teacher_name,
                    (
                        SELECT COUNT(*)
                        FROM enrollments e
                        WHERE e.course_id=c.id
                    ) AS students,
                    (
                        SELECT COUNT(*)
                        FROM lessons l
                        WHERE l.course_id=c.id
                    ) AS lesson_count
                FROM courses c
                LEFT JOIN users u ON u.id=c.teacher_id
                WHERE c.teacher_id=?
                ORDER BY c.updated_at DESC
                """,
                (user["id"],)
            ).fetchall()

        else:
            rows = con.execute(
                """
                SELECT
                    c.*,
                    u.name AS teacher_name,
                    CASE
                        WHEN e.student_id IS NOT NULL
                        THEN 1
                        ELSE 0
                    END AS enrolled,
                    (
                        SELECT COUNT(*)
                        FROM lessons l
                        WHERE l.course_id=c.id
                          AND l.published=1
                    ) AS lesson_count
                FROM courses c
                LEFT JOIN users u
                  ON c.teacher_id=u.id
                LEFT JOIN enrollments e
                  ON e.course_id=c.id
                 AND e.student_id=?
                WHERE c.published=1
                  AND (
                      c.access_code IS NULL
                      OR e.student_id IS NOT NULL
                  )
                ORDER BY c.subject,c.id
                """,
                (user["id"],)
            ).fetchall()

        data = []

        for r in rows:
            item = dict(r)

            if user["role"] == "student":
                item.pop("access_code", None)

            data.append(item)

    return data


# ==========================================================
# INSCRIPCIÓN A CURSOS
# ==========================================================

@app.post("/api/enroll")
def enroll(
    data: Enrollment,
    request: Request,
    user=Depends(csrf_check)
):
    limit(request, "enroll-code", 10, 900)
    require_role(user, "student")
    code = data.code.strip().upper()
    if not code:
        raise HTTPException(422, "Escribe el código del profesor o curso.")

    with db() as con:
        # Se mantiene la inscripción mediante códigos privados anteriores.
        course = con.execute(
            "SELECT id FROM courses WHERE access_code=? AND published=1",
            (code,)
        ).fetchone()
        if course:
            if data.course_id is not None and data.course_id != course["id"]:
                raise HTTPException(422, "El código no corresponde a ese curso.")
            selected_course_id = course["id"]
        else:
            teacher = con.execute(
                "SELECT id,name FROM users WHERE role='teacher' AND teacher_code=?",
                (code,)
            ).fetchone()
            if not teacher:
                raise HTTPException(404, "Código incorrecto o curso sin publicar.")

            available = con.execute(
                "SELECT id,title,subject FROM courses "
                "WHERE teacher_id=? AND published=1 ORDER BY title,id",
                (teacher["id"],)
            ).fetchall()
            if not available:
                raise HTTPException(404, "El profesor aún no tiene cursos publicados.")

            if data.course_id is None and len(available) > 1:
                return {
                    "choose_course": True,
                    "teacher_name": teacher["name"],
                    "courses": [dict(c) for c in available]
                }

            selected_course_id = (
                data.course_id if data.course_id is not None
                else available[0]["id"]
            )
            if not any(c["id"] == selected_course_id for c in available):
                raise HTTPException(
                    404,
                    "El curso seleccionado no pertenece a este profesor "
                    "o todavía no está publicado."
                )

        con.execute(
            "INSERT OR IGNORE INTO enrollments VALUES(?,?,?)",
            (user["id"], selected_course_id, now())
        )
    return {"ok": True, "course_id": selected_course_id}


@app.post("/api/courses/{course_id}/enroll")
def enroll_public(
    course_id: int,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    with db() as con:
        c = con.execute(
            """
            SELECT *
            FROM courses
            WHERE id=?
              AND published=1
              AND access_code IS NULL
            """,
            (course_id,)
        ).fetchone()

        if not c:
            raise HTTPException(
                404,
                "Este curso no tiene "
                "inscripción abierta."
            )

        con.execute(
            """
            INSERT OR IGNORE INTO enrollments
            VALUES(?,?,?)
            """,
            (
                user["id"],
                course_id,
                now()
            )
        )

    return {"ok": True}


@app.delete("/api/courses/{course_id}/enroll")
def unenroll(
    course_id: int,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    with db() as con:
        con.execute(
            """
            DELETE FROM enrollments
            WHERE student_id=? AND course_id=?
            """,
            (user["id"], course_id)
        )

    return {"ok": True}


# ==========================================================
# CREACIÓN Y EDICIÓN DE CURSOS
# ==========================================================

@app.post("/api/teacher/courses", status_code=201)
def new_course(
    data: CourseCreate,
    user=Depends(csrf_check)
):
    require_role(user, "teacher")

    code = (
        secrets.token_hex(12).upper()
        if data.private
        else None
    )

    with db() as con:
        cur = con.execute(
            """
            INSERT INTO courses(
                teacher_id,
                subject,
                title,
                description,
                published,
                access_code,
                created_at,
                updated_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                user["id"],
                data.subject,
                data.title.strip(),
                data.description.strip(),
                int(data.published),
                code,
                now(),
                now()
            )
        )

    return {
        "id": cur.lastrowid,
        "access_code": code
    }


@app.patch("/api/teacher/courses/{course_id}")
def edit_course(
    course_id: int,
    data: CourseEdit,
    user=Depends(csrf_check)
):
    with db() as con:
        own_course(con, user, course_id)

        con.execute(
            """
            UPDATE courses
            SET
                subject=?,
                title=?,
                description=?,
                published=?,
                updated_at=?
            WHERE id=?
            """,
            (
                data.subject,
                data.title.strip(),
                data.description.strip(),
                int(data.published),
                now(),
                course_id
            )
        )

    return {"ok": True}


@app.delete("/api/teacher/courses/{course_id}")
def remove_course(
    course_id: int,
    user=Depends(csrf_check)
):
    with db() as con:
        own_course(con, user, course_id)

        con.execute(
            "DELETE FROM courses WHERE id=?",
            (course_id,)
        )

    return {"ok": True}


@app.get("/api/courses/{course_id}")
def course_detail(
    course_id: int,
    user=Depends(current_user)
):
    with db() as con:
        c = course_access(
            con,
            user,
            course_id
        )

        teacher = (
            user["role"] == "teacher"
        )

        lesson_sql = (
            "SELECT * FROM lessons WHERE course_id=? "
            + (
                ""
                if teacher
                else "AND published=1 "
            )
            + "ORDER BY position,id"
        )

        ls = con.execute(
            lesson_sql,
            (course_id,)
        ).fetchall()

        quiz_sql = (
            """
            SELECT
                q.*,
                (
                    SELECT COUNT(*)
                    FROM quiz_questions qq
                    WHERE qq.quiz_id=q.id
                ) AS question_count
            FROM quizzes q
            WHERE q.course_id=?
            """
            + (
                ""
                if teacher
                else "AND q.published=1 "
            )
            + "ORDER BY q.id"
        )

        qzs = con.execute(
            quiz_sql,
            (course_id,)
        ).fetchall()

        completed = set()

        if not teacher:
            completed = {
                r["lesson_id"]
                for r in con.execute(
                    """
                    SELECT lp.lesson_id
                    FROM lesson_progress lp
                    JOIN lessons l ON l.id=lp.lesson_id
                    WHERE l.course_id=?
                      AND lp.student_id=?
                    """,
                    (course_id, user["id"])
                )
            }

        out = dict(c)
        owner = con.execute(
            "SELECT name FROM users WHERE id=?", (c["teacher_id"],)
        ).fetchone() if c["teacher_id"] is not None else None
        out["teacher_name"] = owner["name"] if owner else None

        if not teacher:
            out.pop("access_code", None)

        out["lessons"] = [
            {
                **dict(l),
                "completed": l["id"] in completed
            }
            for l in ls
        ]

        out["quizzes"] = [
            dict(q)
            for q in qzs
        ]

    return out


# ==========================================================
# ADMINISTRACIÓN DE LECCIONES
# ==========================================================

@app.post(
    "/api/teacher/courses/{course_id}/lessons",
    status_code=201
)
def add_lesson(
    course_id: int,
    data: LessonEdit,
    user=Depends(csrf_check)
):
    validate_lesson(data)

    with db() as con:
        own_course(con, user, course_id)

        cur = con.execute(
            """
            INSERT INTO lessons(
                course_id,
                title,
                body,
                ar_model,
                support_url,
                position,
                published,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                course_id,
                data.title.strip(),
                data.body.strip(),
                data.ar_model,
                data.support_url.strip(),
                data.position,
                int(data.published),
                now()
            )
        )

    return {
        "id": cur.lastrowid
    }


@app.patch("/api/teacher/lessons/{lesson_id}")
def edit_lesson(
    lesson_id: int,
    data: LessonEdit,
    user=Depends(csrf_check)
):
    validate_lesson(data)

    with db() as con:
        row = con.execute(
            """
            SELECT course_id
            FROM lessons
            WHERE id=?
            """,
            (lesson_id,)
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Lección no encontrada."
            )

        own_course(
            con,
            user,
            row["course_id"]
        )

        con.execute(
            """
            UPDATE lessons
            SET
                title=?,
                body=?,
                ar_model=?,
                support_url=?,
                position=?,
                published=?
            WHERE id=?
            """,
            (
                data.title.strip(),
                data.body.strip(),
                data.ar_model,
                data.support_url.strip(),
                data.position,
                int(data.published),
                lesson_id
            )
        )

    return {"ok": True}


@app.delete("/api/teacher/lessons/{lesson_id}")
def delete_lesson(
    lesson_id: int,
    user=Depends(csrf_check)
):
    with db() as con:
        row = con.execute(
            """
            SELECT course_id
            FROM lessons
            WHERE id=?
            """,
            (lesson_id,)
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Lección no encontrada."
            )

        own_course(
            con,
            user,
            row["course_id"]
        )

        con.execute(
            "DELETE FROM lessons WHERE id=?",
            (lesson_id,)
        )

    return {"ok": True}


@app.post("/api/lessons/{lesson_id}/complete")
def complete_lesson(
    lesson_id: int,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    with db() as con:
        r = con.execute(
            """
            SELECT course_id,published
            FROM lessons
            WHERE id=?
            """,
            (lesson_id,)
        ).fetchone()

        if not r or not r["published"]:
            raise HTTPException(
                404,
                "Lección no encontrada."
            )

        course_access(
            con,
            user,
            r["course_id"]
        )

        con.execute(
            """
            INSERT OR IGNORE INTO lesson_progress
            VALUES(?,?,?)
            """,
            (
                user["id"],
                lesson_id,
                now()
            )
        )

    return {"ok": True}


# ==========================================================
# CREACIÓN Y EDICIÓN DE EVALUACIONES
# ==========================================================

@app.post(
    "/api/teacher/courses/{course_id}/quizzes",
    status_code=201
)
def add_quiz(
    course_id: int,
    data: QuizEdit,
    user=Depends(csrf_check)
):
    with db() as con:
        own_course(
            con,
            user,
            course_id
        )

        cur = con.execute(
            """
            INSERT INTO quizzes(
                course_id,
                title,
                instructions,
                kind,
                time_limit,
                max_attempts,
                published,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                course_id,
                data.title.strip(),
                data.instructions.strip(),
                data.kind,
                data.time_limit,
                data.max_attempts,
                int(data.published),
                now()
            )
        )

    return {
        "id": cur.lastrowid
    }


@app.patch("/api/teacher/quizzes/{quiz_id}")
def edit_quiz(
    quiz_id: int,
    data: QuizEdit,
    user=Depends(csrf_check)
):
    with db() as con:
        q = con.execute(
            """
            SELECT course_id
            FROM quizzes
            WHERE id=?
            """,
            (quiz_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Cuestionario no encontrado."
            )

        own_course(
            con,
            user,
            q["course_id"]
        )

        con.execute(
            """
            UPDATE quizzes
            SET
                title=?,
                instructions=?,
                kind=?,
                time_limit=?,
                max_attempts=?,
                published=?
            WHERE id=?
            """,
            (
                data.title.strip(),
                data.instructions.strip(),
                data.kind,
                data.time_limit,
                data.max_attempts,
                int(data.published),
                quiz_id
            )
        )

    return {"ok": True}


@app.delete("/api/teacher/quizzes/{quiz_id}")
def delete_quiz(
    quiz_id: int,
    user=Depends(csrf_check)
):
    with db() as con:
        q = con.execute(
            """
            SELECT course_id
            FROM quizzes
            WHERE id=?
            """,
            (quiz_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Cuestionario no encontrado."
            )

        own_course(
            con,
            user,
            q["course_id"]
        )

        con.execute(
            "DELETE FROM quizzes WHERE id=?",
            (quiz_id,)
        )

    return {"ok": True}


@app.get("/api/teacher/quizzes/{quiz_id}")
def teacher_quiz(
    quiz_id: int,
    user=Depends(current_user)
):
    with db() as con:
        q = con.execute(
            """
            SELECT *
            FROM quizzes
            WHERE id=?
            """,
            (quiz_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Cuestionario no encontrado."
            )

        own_course(
            con,
            user,
            q["course_id"]
        )

        questions = con.execute(
            """
            SELECT *
            FROM quiz_questions
            WHERE quiz_id=?
            ORDER BY position,id
            """,
            (quiz_id,)
        ).fetchall()

    return {
        **dict(q),
        "questions": [
            {
                **dict(x),
                "options": json.loads(
                    x["options_json"]
                )
            }
            for x in questions
        ]
    }


# ==========================================================
# ADMINISTRACIÓN DE PREGUNTAS
# ==========================================================

@app.post(
    "/api/teacher/quizzes/{quiz_id}/questions",
    status_code=201
)
def add_question(
    quiz_id: int,
    data: QuizQuestion,
    user=Depends(csrf_check)
):
    valid_question(data)

    with db() as con:
        q = con.execute(
            """
            SELECT course_id
            FROM quizzes
            WHERE id=?
            """,
            (quiz_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Evaluación no encontrada."
            )

        own_course(
            con,
            user,
            q["course_id"]
        )

        cur = con.execute(
            """
            INSERT INTO quiz_questions(
                quiz_id,
                prompt,
                options_json,
                answer_index,
                explanation,
                position
            )
            VALUES(?,?,?,?,?,?)
            """,
            (
                quiz_id,
                data.prompt.strip(),
                json.dumps(
                    [
                        o.strip()
                        for o in data.options
                    ],
                    ensure_ascii=False
                ),
                data.answer_index,
                data.explanation.strip(),
                data.position
            )
        )

    return {
        "id": cur.lastrowid
    }


@app.patch("/api/teacher/questions/{question_id}")
def edit_question(
    question_id: int,
    data: QuizQuestion,
    user=Depends(csrf_check)
):
    valid_question(data)

    with db() as con:
        q = con.execute(
            """
            SELECT quizzes.course_id
            FROM quiz_questions
            JOIN quizzes
              ON quizzes.id=quiz_questions.quiz_id
            WHERE quiz_questions.id=?
            """,
            (question_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Pregunta no encontrada."
            )

        own_course(
            con,
            user,
            q["course_id"]
        )

        con.execute(
            """
            UPDATE quiz_questions
            SET
                prompt=?,
                options_json=?,
                answer_index=?,
                explanation=?,
                position=?
            WHERE id=?
            """,
            (
                data.prompt.strip(),
                json.dumps(
                    [
                        o.strip()
                        for o in data.options
                    ],
                    ensure_ascii=False
                ),
                data.answer_index,
                data.explanation.strip(),
                data.position,
                question_id
            )
        )

    return {"ok": True}


@app.delete("/api/teacher/questions/{question_id}")
def delete_question(
    question_id: int,
    user=Depends(csrf_check)
):
    with db() as con:
        q = con.execute(
            """
            SELECT quizzes.course_id
            FROM quiz_questions
            JOIN quizzes
              ON quizzes.id=quiz_questions.quiz_id
            WHERE quiz_questions.id=?
            """,
            (question_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Pregunta no encontrada."
            )

        own_course(
            con,
            user,
            q["course_id"]
        )

        con.execute(
            """
            DELETE FROM quiz_questions
            WHERE id=?
            """,
            (question_id,)
        )

    return {"ok": True}


# ==========================================================
# PRESENTACIÓN DE EVALUACIONES
# ==========================================================

@app.post("/api/quizzes/{quiz_id}/start")
def start_quiz(
    quiz_id: int,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    with db() as con:
        q = con.execute(
            """
            SELECT *
            FROM quizzes
            WHERE id=? AND published=1
            """,
            (quiz_id,)
        ).fetchone()

        if not q:
            raise HTTPException(
                404,
                "Evaluación no publicada."
            )

        course_access(
            con,
            user,
            q["course_id"]
        )

        questions = con.execute(
            """
            SELECT id,prompt,options_json
            FROM quiz_questions
            WHERE quiz_id=?
            ORDER BY position,id
            """,
            (quiz_id,)
        ).fetchall()

        if not questions:
            raise HTTPException(
                409,
                "La evaluación no tiene preguntas."
            )

        existing = con.execute(
            """
            SELECT *
            FROM quiz_attempts
            WHERE quiz_id=?
              AND student_id=?
              AND submitted_at IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                quiz_id,
                user["id"]
            )
        ).fetchone()

        if existing:
            attempt = existing

        else:
            total = con.execute(
                """
                SELECT COUNT(*)
                FROM quiz_attempts
                WHERE quiz_id=? AND student_id=?
                """,
                (
                    quiz_id,
                    user["id"]
                )
            ).fetchone()[0]

            if total >= q["max_attempts"]:
                raise HTTPException(
                    409,
                    "Alcanzaste el límite de intentos."
                )

            cur = con.execute(
                """
                INSERT INTO quiz_attempts(
                    quiz_id,
                    student_id,
                    started_at
                )
                VALUES(?,?,?)
                """,
                (
                    quiz_id,
                    user["id"],
                    now()
                )
            )

            attempt = con.execute(
                """
                SELECT *
                FROM quiz_attempts
                WHERE id=?
                """,
                (cur.lastrowid,)
            ).fetchone()

        elapsed = (
            now() - attempt["started_at"]
        )

        seconds_left = (
            max(
                0,
                q["time_limit"] * 60 - elapsed
            )
            if q["time_limit"]
            else None
        )

    return {
        "attempt_id": attempt["id"],
        "quiz_id": quiz_id,
        "title": q["title"],
        "instructions": q["instructions"],
        "kind": q["kind"],
        "remaining_seconds": seconds_left,
        "questions": [
            {
                "id": r["id"],
                "prompt": r["prompt"],
                "options": json.loads(
                    r["options_json"]
                )
            }
            for r in questions
        ]
    }


@app.post("/api/attempts/{attempt_id}/submit")
def submit_quiz(
    attempt_id: int,
    data: SubmitQuiz,
    user=Depends(csrf_check)
):
    require_role(user, "student")

    with db() as con:
        a = con.execute(
            """
            SELECT *
            FROM quiz_attempts
            WHERE id=? AND student_id=?
            """,
            (
                attempt_id,
                user["id"]
            )
        ).fetchone()

        if not a:
            raise HTTPException(
                404,
                "Intento inexistente."
            )

        if a["submitted_at"]:
            raise HTTPException(
                409,
                "El intento ya fue enviado."
            )

        q = con.execute(
            """
            SELECT *
            FROM quizzes
            WHERE id=?
            """,
            (a["quiz_id"],)
        ).fetchone()

        expired = bool(
            q["time_limit"]
            and now() > (
                a["started_at"]
                + q["time_limit"] * 60
                + 5
            )
        )

        questions = con.execute(
            """
            SELECT *
            FROM quiz_questions
            WHERE quiz_id=?
            ORDER BY position,id
            """,
            (a["quiz_id"],)
        ).fetchall()

        valid_ids = {
            str(r["id"])
            for r in questions
        }

        if (
            set(data.choices) - valid_ids
            or any(
                type(x) is not int
                or x < 0
                or x > 3
                for x in data.choices.values()
            )
        ):
            raise HTTPException(
                422,
                "Respuestas no válidas."
            )

        score = (
            0
            if expired
            else sum(
                int(
                    data.choices.get(
                        str(r["id"]),
                        -1
                    )
                    == r["answer_index"]
                )
                for r in questions
            )
        )

        con.execute(
            """
            UPDATE quiz_attempts
            SET
                submitted_at=?,
                choices_json=?,
                score=?,
                total=?
            WHERE id=?
            """,
            (
                now(),
                json.dumps(data.choices),
                score,
                len(questions),
                attempt_id
            )
        )

        details = [
            {
                "prompt": r["prompt"],
                "correct_index": r["answer_index"],
                "choice": data.choices.get(
                    str(r["id"])
                ),
                "explanation": r["explanation"],
                "correct": (
                    not expired
                    and data.choices.get(str(r["id"]))
                    == r["answer_index"]
                )
            }
            for r in questions
        ]

    return {
        "score": score,
        "total": len(questions),
        "expired": expired,
        "details": details
    }


@app.get("/api/attempts")
def my_attempts(
    user=Depends(current_user)
):
    require_role(user, "student")

    with db() as con:
        rows = con.execute(
            """
            SELECT
                a.id,
                a.started_at,
                a.submitted_at,
                a.score,
                a.total,
                q.title AS quiz_title,
                q.kind,
                q.id AS quiz_id,
                c.title AS course_title,
                c.id AS course_id
            FROM quiz_attempts a
            JOIN quizzes q
              ON q.id=a.quiz_id
            JOIN courses c
              ON c.id=q.course_id
            WHERE a.student_id=?
            ORDER BY a.started_at DESC
            LIMIT 200
            """,
            (user["id"],)
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]


# ==========================================================
# RESUMEN DEL ESTUDIANTE
# ==========================================================

@app.get("/api/student/overview")
def student_overview(
    user=Depends(current_user)
):
    require_role(user, "student")

    with db() as con:
        course_count = con.execute(
            """
            SELECT COUNT(*)
            FROM enrollments
            WHERE student_id=?
            """,
            (user["id"],)
        ).fetchone()[0]

        completed = con.execute(
            """
            SELECT COUNT(*)
            FROM lesson_progress
            WHERE student_id=?
            """,
            (user["id"],)
        ).fetchone()[0]

        total = con.execute(
            """
            SELECT COUNT(*)
            FROM lessons l
            JOIN enrollments e
              ON e.course_id=l.course_id
            WHERE e.student_id=?
              AND l.published=1
            """,
            (user["id"],)
        ).fetchone()[0]

        taken = con.execute(
            """
            SELECT COUNT(*)
            FROM quiz_attempts
            WHERE student_id=?
              AND submitted_at IS NOT NULL
            """,
            (user["id"],)
        ).fetchone()[0]

        best = con.execute(
            """
            SELECT AVG(100.0*score/total)
            FROM quiz_attempts
            WHERE student_id=?
              AND submitted_at IS NOT NULL
              AND total>0
            """,
            (user["id"],)
        ).fetchone()[0]

    return {
        "courses": course_count,
        "lessons_completed": completed,
        "lessons_total": total,
        "exams_taken": taken,
        "average_percent": round(
            best or 0,
            1
        )
    }


# ==========================================================
# REPORTES DEL PROFESOR
# ==========================================================

@app.get("/api/teacher/courses/{course_id}/report")
def teacher_report(
    course_id: int,
    user=Depends(current_user)
):
    with db() as con:
        own_course(
            con,
            user,
            course_id
        )

        students = con.execute(
            """
            SELECT
                u.id,
                u.name,
                u.email,
                e.joined_at,
                (
                    SELECT COUNT(*)
                    FROM lesson_progress lp
                    JOIN lessons l ON l.id=lp.lesson_id
                    WHERE lp.student_id=u.id
                      AND l.course_id=?
                ) AS completed,
                (
                    SELECT COUNT(*)
                    FROM lessons l
                    WHERE l.course_id=?
                      AND l.published=1
                ) AS total,
                (
                    SELECT COUNT(*)
                    FROM quiz_attempts qa
                    JOIN quizzes q ON q.id=qa.quiz_id
                    WHERE qa.student_id=u.id
                      AND q.course_id=?
                      AND qa.submitted_at IS NOT NULL
                ) AS attempts
            FROM enrollments e
            JOIN users u
              ON u.id=e.student_id
            WHERE e.course_id=?
            ORDER BY u.name
            """,
            (
                course_id,
                course_id,
                course_id,
                course_id
            )
        ).fetchall()

        results = con.execute(
            """
            SELECT
                u.name,
                q.title,
                qa.score,
                qa.total,
                qa.submitted_at
            FROM quiz_attempts qa
            JOIN users u
              ON qa.student_id=u.id
            JOIN quizzes q
              ON q.id=qa.quiz_id
            WHERE q.course_id=?
              AND qa.submitted_at IS NOT NULL
            ORDER BY qa.submitted_at DESC
            LIMIT 300
            """,
            (course_id,)
        ).fetchall()

    return {
        "students": [
            dict(r)
            for r in students
        ],
        "results": [
            dict(r)
            for r in results
        ]
    }


# ==========================================================
# INICIALIZACIÓN DE LAS TABLAS V3
# ==========================================================

init_v3()


# ==========================================================
# PROGRESO POR ASIGNATURA
# ==========================================================

@app.get("/api/student/progress-courses")
def progress_by_course(
    user=Depends(current_user)
):
    require_role(user, "student")

    with db() as con:
        rows = con.execute(
            """
            SELECT
                c.id,
                c.title,
                c.subject,

                (
                    SELECT COUNT(*)
                    FROM lessons l
                    WHERE l.course_id=c.id
                      AND l.published=1
                ) AS total_lessons,

                (
                    SELECT COUNT(*)
                    FROM lesson_progress lp
                    JOIN lessons l
                      ON l.id=lp.lesson_id
                    WHERE l.course_id=c.id
                      AND l.published=1
                      AND lp.student_id=?
                ) AS completed_lessons,

                (
                    SELECT COUNT(*)
                    FROM quiz_attempts qa
                    JOIN quizzes q
                      ON qa.quiz_id=q.id
                    WHERE q.course_id=c.id
                      AND qa.student_id=?
                      AND qa.submitted_at IS NOT NULL
                ) AS exam_attempts,

                (
                    SELECT ROUND(
                        AVG(
                            100.0*qa.score/qa.total
                        ),
                        1
                    )
                    FROM quiz_attempts qa
                    JOIN quizzes q
                      ON qa.quiz_id=q.id
                    WHERE q.course_id=c.id
                      AND qa.student_id=?
                      AND qa.submitted_at IS NOT NULL
                      AND qa.total>0
                ) AS average_percent

            FROM courses c
            JOIN enrollments e
              ON e.course_id=c.id
            WHERE e.student_id=?
              AND c.published=1
            ORDER BY c.subject,c.title
            """,
            (
                user["id"],
                user["id"],
                user["id"],
                user["id"]
            )
        ).fetchall()

    return [
        dict(r)
        for r in rows
    ]