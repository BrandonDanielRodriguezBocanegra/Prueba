# init_db.py
import psycopg2
from psycopg2 import sql
from werkzeug.security import generate_password_hash

DB_URL = "postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db"

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

# --- Proyectos ---
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

# Crear admin si no existe
c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
if not c.fetchone():
    password_hash = generate_password_hash('admin123')
    c.execute(
        "INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(%s,%s,%s,%s,%s,%s)",
        ('Administrador Principal', 'admin', 'admin@empresa.com', password_hash, 1, 'aprobado')
    )

conn.commit()
conn.close()
print("DB initialized/updated.")
