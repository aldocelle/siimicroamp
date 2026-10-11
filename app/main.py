"""SII Hub MVP. Operaciones locales: NO se conecta al SII."""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .dte import normalize_rut, parse_dte_xml, valid_rut

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("SII_DB_PATH", str(ROOT / "data" / "sii_hub.sqlite3")))
MAX_BYTES = 10 * 1024 * 1024
SESSION_TTL = 7 * 24 * 3600
COOKIE_SECURE = os.getenv("SII_COOKIE_SECURE", "0") == "1"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def database():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, rut TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organizations(id),
            email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organizations(id),
            tipo_dte TEXT NOT NULL, folio TEXT NOT NULL,
            fecha_emision TEXT NOT NULL DEFAULT '',
            rut_emisor TEXT NOT NULL DEFAULT '', razon_emisor TEXT NOT NULL DEFAULT '',
            rut_receptor TEXT NOT NULL DEFAULT '', razon_receptor TEXT NOT NULL DEFAULT '',
            monto_neto INTEGER, monto_exento INTEGER, iva INTEGER, monto_total INTEGER,
            direccion TEXT NOT NULL DEFAULT 'sin_clasificar',
            estado TEXT NOT NULL DEFAULT 'extraido',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            filename TEXT NOT NULL, source_sha256 TEXT NOT NULL, original BLOB NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_dte_identity
            ON documents(org_id, rut_emisor, tipo_dte, folio);
        CREATE INDEX IF NOT EXISTS ix_documents_org_date ON documents(org_id, created_at);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES organizations(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            document_id INTEGER NOT NULL REFERENCES documents(id),
            action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """)


initialize()
app = FastAPI(title="SII Hub MVP", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + digest.hex()


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def hash_token(value):
    return hashlib.sha256(value.encode()).hexdigest()


def set_session(response, user_id):
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    with database() as db:
        db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
        db.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires) VALUES(?,?,?,?)",
                   (hash_token(token), user_id, csrf, int(time.time()) + SESSION_TTL))
    response.set_cookie("sii_session", token, max_age=SESSION_TTL, httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", path="/")
    return csrf


def current_user(request: Request):
    token = request.cookies.get("sii_session", "")
    if not token:
        raise HTTPException(401, "Inicia sesión para continuar.")
    with database() as db:
        row = db.execute("""
            SELECT u.id, u.email, u.org_id, o.name AS org_name, o.rut AS org_rut, s.csrf
            FROM sessions s JOIN users u ON u.id=s.user_id
            JOIN organizations o ON o.id=u.org_id
            WHERE s.token_hash=? AND s.expires>?
        """, (hash_token(token), int(time.time()))).fetchone()
    if row is None:
        raise HTTPException(401, "Sesión vencida. Vuelve a ingresar.")
    return dict(row)


def check_csrf(request: Request, user):
    provided = request.headers.get("X-CSRF-Token", "")
    if not provided or not hmac.compare_digest(provided, user["csrf"]):
        raise HTTPException(403, "Token de seguridad ausente o incorrecto.")


class Register(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    organization: str = Field(min_length=2, max_length=120)
    rut: str = Field(default="", max_length=24)


class Login(BaseModel):
    email: str
    password: str


class Review(BaseModel):
    tipo_dte: str = Field(min_length=1, max_length=5)
    folio: str = Field(min_length=1, max_length=30)
    fecha_emision: str = Field(default="", max_length=20)
    rut_emisor: str = Field(default="", max_length=24)
    razon_emisor: str = Field(default="", max_length=200)
    rut_receptor: str = Field(default="", max_length=24)
    razon_receptor: str = Field(default="", max_length=200)
    monto_neto: int | None = None
    monto_exento: int | None = None
    iva: int | None = None
    monto_total: int | None = None
    direccion: str = Field(default="sin_clasificar")


def public_document(row):
    data = dict(row)
    for key in ("original", "source_sha256", "org_id"):
        data.pop(key, None)
    data["warnings"] = json.loads(data.pop("warnings_json", "[]"))
    return data


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "sii_connected": False}


@app.post("/api/register", status_code=201)
def register(body: Register, response: Response):
    email = body.email.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise HTTPException(422, "Correo electrónico inválido.")
    rut = normalize_rut(body.rut) if body.rut.strip() else ""
    if rut and not valid_rut(rut):
        raise HTTPException(422, "El RUT de la organización no es válido.")
    with database() as db:
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "El correo ya está registrado.")
        cursor = db.execute("INSERT INTO organizations(name,rut) VALUES(?,?)",
                            (body.organization.strip(), rut))
        try:
            user = db.execute("INSERT INTO users(org_id,email,password_hash) VALUES(?,?,?)",
                              (cursor.lastrowid, email, password_hash(body.password)))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "El correo ya está registrado.") from exc
        user_id = user.lastrowid
    csrf = set_session(response, user_id)
    return {"csrf": csrf, "email": email, "organization": body.organization.strip()}


@app.post("/api/login")
def login(body: Login, response: Response):
    with database() as db:
        row = db.execute("SELECT id,password_hash FROM users WHERE email=?",
                         (body.email.strip().lower(),)).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(401, "Correo o contraseña incorrectos.")
    csrf = set_session(response, row["id"])
    return {"csrf": csrf}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"email": user["email"], "organization": user["org_name"],
            "organization_rut": user["org_rut"], "csrf": user["csrf"]}


@app.post("/api/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    check_csrf(request, user)
    with database() as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?",
                   (hash_token(request.cookies.get("sii_session", "")),))
    response.delete_cookie("sii_session", path="/")
    return {"ok": True}


@app.post("/api/documents/import")
async def import_xml(request: Request, file: UploadFile = File(...), user=Depends(current_user)):
    check_csrf(request, user)
    if not (file.filename or "").lower().endswith(".xml"):
        raise HTTPException(415, "Por ahora solo se admiten archivos XML DTE.")
    payload = await file.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise HTTPException(413, "El archivo supera el máximo de 10 MB.")
    try:
        parsed = parse_dte_xml(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    sha = hashlib.sha256(payload).hexdigest()
    inserted, duplicates = [], []
    with database() as db:
        for item in parsed:
            direction = "sin_clasificar"
            if user["org_rut"]:
                if item["rut_emisor"] == user["org_rut"]:
                    direction = "venta"
                elif item["rut_receptor"] == user["org_rut"]:
                    direction = "compra"
            try:
                cursor = db.execute("""
                    INSERT INTO documents (
                        org_id,tipo_dte,folio,fecha_emision,rut_emisor,razon_emisor,
                        rut_receptor,razon_receptor,monto_neto,monto_exento,iva,monto_total,
                        direccion,warnings_json,filename,source_sha256,original
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    user["org_id"], item["tipo_dte"], item["folio"], item["fecha_emision"],
                    item["rut_emisor"], item["razon_emisor"], item["rut_receptor"],
                    item["razon_receptor"], item["monto_neto"], item["monto_exento"],
                    item["iva"], item["monto_total"], direction,
                    json.dumps(item["warnings"], ensure_ascii=False),
                    (file.filename or "documento.xml")[:200], sha, sqlite3.Binary(payload)
                ))
                inserted.append(cursor.lastrowid)
            except sqlite3.IntegrityError:
                duplicates.append({"tipo_dte": item["tipo_dte"], "folio": item["folio"],
                                   "rut_emisor": item["rut_emisor"]})
    return {"imported": len(inserted), "ids": inserted, "duplicates": duplicates,
            "notice": "Extracción y validaciones locales. No verificado por SII."}


@app.get("/api/documents")
def list_documents(q: str = "", direction: str = "", user=Depends(current_user)):
    where = ["org_id=?"]
    params = [user["org_id"]]
    if q.strip():
        where.append("(folio LIKE ? OR rut_emisor LIKE ? OR razon_emisor LIKE ? OR rut_receptor LIKE ?)")
        params.extend(["%" + q.strip()[:100] + "%"] * 4)
    if direction in ("compra", "venta", "sin_clasificar"):
        where.append("direccion=?")
        params.append(direction)
    with database() as db:
        rows = db.execute("SELECT * FROM documents WHERE " + " AND ".join(where) +
                          " ORDER BY id DESC LIMIT 300", params).fetchall()
        totals = db.execute("""
            SELECT COUNT(*) AS count,
                COALESCE(SUM(CASE WHEN direccion='compra' THEN monto_total ELSE 0 END),0) AS compras,
                COALESCE(SUM(CASE WHEN direccion='venta' THEN monto_total ELSE 0 END),0) AS ventas,
                SUM(CASE WHEN estado='extraido' THEN 1 ELSE 0 END) AS pendientes
            FROM documents WHERE org_id=?
        """, (user["org_id"],)).fetchone()
    return {"documents": [public_document(row) for row in rows], "summary": dict(totals),
            "notice": "Montos basados en documentos importados; no son datos oficiales del SII."}


@app.get("/api/documents/{document_id}")
def get_document(document_id: int, user=Depends(current_user)):
    with database() as db:
        row = db.execute("SELECT * FROM documents WHERE id=? AND org_id=?",
                         (document_id, user["org_id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Documento no encontrado.")
    return public_document(row)


@app.patch("/api/documents/{document_id}")
def review_document(document_id: int, body: Review, request: Request, user=Depends(current_user)):
    check_csrf(request, user)
    data = body.model_dump()
    if data["direccion"] not in ("compra", "venta", "sin_clasificar"):
        raise HTTPException(422, "Clasificación inválida.")
    data["rut_emisor"] = normalize_rut(data["rut_emisor"])
    data["rut_receptor"] = normalize_rut(data["rut_receptor"])
    warnings = []
    for key, label in (("rut_emisor", "emisor"), ("rut_receptor", "receptor")):
        if not valid_rut(data[key]):
            warnings.append(f"RUT de {label} ausente o inválido.")
    if data["monto_total"] is None:
        warnings.append("No se ha indicado monto total.")
    elif any(data[k] is not None for k in ("monto_neto", "monto_exento", "iva")):
        if sum(data[k] or 0 for k in ("monto_neto", "monto_exento", "iva")) != data["monto_total"]:
            warnings.append("La suma neto + exento + IVA difiere del total; revisar otros impuestos.")
    with database() as db:
        old = db.execute("SELECT * FROM documents WHERE id=? AND org_id=?",
                         (document_id, user["org_id"])).fetchone()
        if old is None:
            raise HTTPException(404, "Documento no encontrado.")
        before = public_document(old)
        keys = list(data)
        try:
            db.execute("UPDATE documents SET " +
                       ", ".join(f"{key}=?" for key in keys) +
                       ", warnings_json=?, estado='revisado', updated_at=CURRENT_TIMESTAMP WHERE id=? AND org_id=?",
                       [data[key] for key in keys] + [json.dumps(warnings, ensure_ascii=False),
                                                       document_id, user["org_id"]])
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "Ya existe un DTE con ese emisor, tipo y folio.") from exc
        new = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        after = public_document(new)
        db.execute("""INSERT INTO audit(org_id,user_id,document_id,action,before_json,after_json)
                      VALUES(?,?,?,?,?,?)""",
                   (user["org_id"], user["id"], document_id, "revision",
                    json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False)))
    return after


@app.get("/api/documents/{document_id}/original")
def original(document_id: int, user=Depends(current_user)):
    with database() as db:
        row = db.execute("SELECT original FROM documents WHERE id=? AND org_id=?",
                         (document_id, user["org_id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Documento no encontrado.")
    return Response(content=bytes(row["original"]), media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="dte-{document_id}.xml"'})


def csv_safe(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")) and not re.fullmatch(r"-?\d+", text):
        return "'" + text
    return text


@app.get("/api/export.csv")
def export_csv(user=Depends(current_user)):
    fields = ["id", "tipo_dte", "folio", "fecha_emision", "rut_emisor", "razon_emisor",
              "rut_receptor", "razon_receptor", "monto_neto", "monto_exento", "iva",
              "monto_total", "direccion", "estado"]
    with database() as db:
        rows = db.execute("SELECT " + ",".join(fields) +
                          " FROM documents WHERE org_id=? ORDER BY id DESC LIMIT 10000",
                          (user["org_id"],)).fetchall()
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=";")
    writer.writerow(fields)
    for row in rows:
        writer.writerow([csv_safe(row[key]) for key in fields])
    return Response(content="\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="sii-hub-registro-interno.csv"'})
