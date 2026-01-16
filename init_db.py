# init_db.py
import os
import psycopg2
from werkzeug.security import generate_password_hash

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db"
)

conn = psycopg2.connect(DB_URL)
c = conn.cursor()

# -------------------------
# TABLA USUARIOS
# -------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS usuarios(
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    usuario TEXT UNIQUE NOT NULL,
    correo TEXT NOT NULL,
    password TEXT NOT NULL,
    rol INTEGER NOT NULL,
    estado TEXT NOT NULL,
    mail_password TEXT,

    -- NUEVOS CAMPOS REPSE (proveedor)
    repse_numero TEXT,
    repse_folio TEXT,
    repse_aviso TEXT,
    repse_fecha_aviso DATE,
    repse_vigencia DATE,
    repse_rfc TEXT,
    repse_regimen TEXT,
    repse_objeto TEXT,
    contacto_nombre TEXT,
    contacto_tel TEXT,
    contacto_correo TEXT
)
""")

# Si la tabla ya existía antes, asegura columnas con ALTER TABLE (no falla si ya existen)
alter_cols = [
    "repse_numero TEXT",
    "repse_folio TEXT",
    "repse_aviso TEXT",
    "repse_fecha_aviso DATE",
    "repse_vigencia DATE",
    "repse_rfc TEXT",
    "repse_regimen TEXT",
    "repse_objeto TEXT",
    "contacto_nombre TEXT",
    "contacto_tel TEXT",
    "contacto_correo TEXT",
    "mail_password TEXT"
]

for coldef in alter_cols:
    colname = coldef.split()[0]
    c.execute(f"ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS {coldef};")

# -------------------------
# TABLA PROYECTOS
# -------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS projects(
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed INTEGER DEFAULT 0
)
""")

# -------------------------
# TABLA DOCUMENTOS
# -------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS documentos(
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TIMESTAMP NOT NULL DEFAULT NOW(),
    tipo_documento TEXT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE
)
""")

# -------------------------
# CREAR ADMIN SI NO EXISTE
# -------------------------
print("Verificando existencia del usuario administrador...")

c.execute("SELECT 1 FROM usuarios WHERE usuario = %s", ('admin',))
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
