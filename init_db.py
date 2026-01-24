# init_db.py (psycopg v3)
import os
import psycopg
from psycopg import sql
from werkzeug.security import generate_password_hash

def normalize_db_url(url: str) -> str:
    if not url:
        return url
    if "sslmode=" in url:
        return url
    joiner = "&" if "?" in url else "?"
    return url + f"{joiner}sslmode=require"

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://repse_db_user:TU_PASSWORD@TU_HOST/repse_db"
)
DB_URL = normalize_db_url(DB_URL)

conn = psycopg.connect(DB_URL)
cur = conn.cursor()

# ------------------------------------------------------------
# TABLA USUARIOS
# ------------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS usuarios(
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    usuario TEXT UNIQUE NOT NULL,
    correo TEXT NOT NULL,
    password TEXT NOT NULL,
    rol INTEGER NOT NULL,
    estado TEXT NOT NULL,
    mail_password TEXT,

    -- NUEVOS CAMPOS PROVEEDOR (si ya existían, no pasa nada)
    telefono TEXT,
    razon_social TEXT,
    rfc TEXT,
    domicilio TEXT,
    representante_legal TEXT,
    repse TEXT
)
""")

# ------------------------------------------------------------
# TABLA PROJECTS (PEDIDOS)
# ------------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS projects(
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed INTEGER DEFAULT 0
)
""")

# Campos nuevos para pedidos / periodo
cur.execute("""
ALTER TABLE projects
ADD COLUMN IF NOT EXISTS pedido_no TEXT,
ADD COLUMN IF NOT EXISTS periodo_year INT,
ADD COLUMN IF NOT EXISTS periodo_month INT
""")

# ------------------------------------------------------------
# TABLA DOCUMENTOS
# ------------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS documentos(
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TIMESTAMP NOT NULL DEFAULT NOW(),
    tipo_documento TEXT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL
)
""")

# Campos para documentos globales por periodo
cur.execute("""
ALTER TABLE documentos
ADD COLUMN IF NOT EXISTS periodo_year INT,
ADD COLUMN IF NOT EXISTS periodo_month INT
""")

# ------------------------------------------------------------
# MESES HABILITADOS POR ADMIN PARA CADA PROVEEDOR
# ------------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS provider_enabled_months(
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    year INT NOT NULL,
    month INT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(provider_id, year, month)
)
""")

# ------------------------------------------------------------
# DOCS APLICABLES A CADA PEDIDO
# ------------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS project_required_docs(
    id SERIAL PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tipo_documento TEXT NOT NULL,
    UNIQUE(project_id, tipo_documento)
)
""")

# ------------------------------------------------------------
# CREAR ADMIN SI NO EXISTE
# ------------------------------------------------------------
print("Verificando existencia del usuario administrador...")

cur.execute("SELECT id FROM usuarios WHERE usuario=%s", ("admin",))
admin = cur.fetchone()

if not admin:
    password_hash = generate_password_hash("admin123")
    cur.execute("""
        INSERT INTO usuarios (nombre, usuario, correo, password, rol, estado)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (
        "Administrador Principal",
        "admin",
        "admin@empresa.com",
        password_hash,
        1,
        "aprobado"
    ))
    print(">>> Usuario admin creado correctamente")
    print(">>> Usuario: admin")
    print(">>> Password: admin123")
else:
    print(">>> El usuario admin ya existe")

conn.commit()
cur.close()
conn.close()

print("Base de datos inicializada correctamente.")
