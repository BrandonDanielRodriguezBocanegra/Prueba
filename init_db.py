# init_db.py
import os
import psycopg2
from werkzeug.security import generate_password_hash

DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    DB_URL = "postgresql://repse_db_user:PASS@HOST/repse_db"  # fallback si lo usas local

conn = psycopg2.connect(DB_URL)
c = conn.cursor()

# --- Usuarios ---
c.execute('''
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
''')

# Agregar columnas nuevas si no existen
def add_column_if_missing(table, column, coltype):
    try:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
    except Exception:
        conn.rollback()
    else:
        conn.commit()

add_column_if_missing("usuarios", "req_mes", "TEXT")
add_column_if_missing("usuarios", "req_fecha", "DATE")

# --- Proyectos (Pedidos) ---
c.execute('''
CREATE TABLE IF NOT EXISTS projects(
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id),
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    completed INTEGER DEFAULT 0
)
''')

# --- Documentos ---
c.execute('''
CREATE TABLE IF NOT EXISTS documentos(
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TIMESTAMP NOT NULL DEFAULT NOW(),
    tipo_documento TEXT,
    project_id INTEGER REFERENCES projects(id)
)
''')

# CREAR ADMIN SI NO EXISTE
c.execute("SELECT id FROM usuarios WHERE usuario=%s", ('admin',))
admin = c.fetchone()

if not admin:
    password_hash = generate_password_hash("admin123")
    c.execute("""
        INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (
        "Administrador Principal",
        "admin",
        "admin@empresa.com",
        password_hash,
        1,
        "aprobado"
    ))
    conn.commit()
    print(">>> Usuario admin creado: admin / admin123")
else:
    print(">>> El usuario admin ya existe")

c.close()
conn.close()

print("Base de datos inicializada correctamente.")
