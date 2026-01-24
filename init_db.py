# init_db.py
import os
import psycopg2
from werkzeug.security import generate_password_hash

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://repse_db_user:TU_PASSWORD@TU_HOST/repse_db"
)

def normalize_db_url(url: str) -> str:
    # Render normalmente requiere sslmode=require
    if not url:
        return url
    if "sslmode=" in url:
        return url
    joiner = "&" if "?" in url else "?"
    return url + f"{joiner}sslmode=require"

DB_URL = normalize_db_url(DB_URL)

conn = psycopg2.connect(DB_URL)
c = conn.cursor()

# ------------------------------------------------------------
# TABLA USUARIOS
# ------------------------------------------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS usuarios(
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    usuario TEXT UNIQUE NOT NULL,
    correo TEXT NOT NULL,
    password TEXT NOT NULL,
    rol INTEGER NOT NULL,
    estado TEXT NOT NULL,
    mail_password TEXT
)
""")

# ------------------------------------------------------------
# TABLA PROJECTS (PEDIDOS)
# ------------------------------------------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS projects(
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed INTEGER DEFAULT 0
)
""")

# Campos nuevos para pedidos / periodo
c.execute("""
ALTER TABLE projects
ADD COLUMN IF NOT EXISTS pedido_no TEXT,
ADD COLUMN IF NOT EXISTS periodo_year INT,
ADD COLUMN IF NOT EXISTS periodo_month INT
""")

# ------------------------------------------------------------
# TABLA DOCUMENTOS
# ------------------------------------------------------------
c.execute("""
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
c.execute("""
ALTER TABLE documentos
ADD COLUMN IF NOT EXISTS periodo_year INT,
ADD COLUMN IF NOT EXISTS periodo_month INT
""")

# ------------------------------------------------------------
# MESES HABILITADOS POR ADMIN PARA CADA PROVEEDOR
# ------------------------------------------------------------
c.execute("""
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
# DOCS APLICABLES A CADA PEDIDO (project_required_docs)
# ------------------------------------------------------------
c.execute("""
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

c.execute("SELECT id FROM usuarios WHERE usuario=%s", ("admin",))
admin = c.fetchone()

if not admin:
    password_hash = generate_password_hash("admin123")
    c.execute("""
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
c.close()
conn.close()

print("Base de datos inicializada correctamente.")
