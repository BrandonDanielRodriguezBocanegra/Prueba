# init_db.py
import os
import psycopg
from werkzeug.security import generate_password_hash

DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://repse_db_user:YOURPASS@YOURHOST/repse_db"
)

conn = psycopg.connect(DB_URL)
c = conn.cursor()

# ----------------------------
# TABLA usuarios (con campos REPSE + contacto)
# ----------------------------
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

    -- REPSE
    repse_numero TEXT,
    repse_folio TEXT,
    repse_aviso TEXT,
    repse_fecha_aviso TEXT,
    repse_vigencia TEXT,
    repse_rfc TEXT,
    repse_regimen TEXT,
    repse_objeto TEXT,

    -- CONTACTO
    contacto_nombre TEXT,
    contacto_tel TEXT,
    contacto_correo TEXT
)
""")

# ----------------------------
# TABLA projects (base)
# ----------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS projects(
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id),
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed INTEGER DEFAULT 0
)
""")

# ----------------------------
# AGREGAR COLUMNAS NUEVAS A projects si no existen
# ----------------------------
c.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS month INTEGER;")
c.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS year INTEGER;")
c.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS pedido_num TEXT;")

# ----------------------------
# UNIQUE para evitar duplicados de pedido por proveedor/mes/año
# ----------------------------
c.execute("""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_provider_month_year_pedido'
    ) THEN
        ALTER TABLE projects
        ADD CONSTRAINT uq_provider_month_year_pedido
        UNIQUE (provider_id, month, year, pedido_num);
    END IF;
END$$;
""")

# ----------------------------
# TABLA documentos
# ----------------------------
c.execute("""
CREATE TABLE IF NOT EXISTS documentos(
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TIMESTAMP NOT NULL DEFAULT NOW(),
    tipo_documento TEXT,
    project_id INTEGER REFERENCES projects(id)
)
""")

# ----------------------------
# CREAR ADMIN SI NO EXISTE
# ----------------------------
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
