# init_db.py
import psycopg2
from psycopg2 import sql
from werkzeug.security import generate_password_hash

# Datos de conexión a PostgreSQL
DB_HOST = "dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com"
DB_NAME = "repse_db"
DB_USER = "repse_db_user"
DB_PASS = "DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f"
DB_PORT = "5432"

conn = psycopg2.connect(
    host=DB_HOST,
    database=DB_NAME,
    user=DB_USER,
    password=DB_PASS,
    port=DB_PORT
)

c = conn.cursor()

# Tabla usuarios
c.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL,
    usuario TEXT UNIQUE NOT NULL,
    correo TEXT NOT NULL,
    password TEXT NOT NULL,
    rol INTEGER NOT NULL,
    estado TEXT NOT NULL
);
""")

# Tabla projects
c.execute("""
CREATE TABLE IF NOT EXISTS projects (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed BOOLEAN DEFAULT FALSE
);
""")

# Tabla documentos
c.execute("""
CREATE TABLE IF NOT EXISTS documentos (
    id SERIAL PRIMARY KEY,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tipo_documento TEXT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE
);
""")

# Crear admin si no existe
c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
if not c.fetchone():
    password_hash = generate_password_hash('admin123')
    c.execute("""
    INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
    VALUES (%s,%s,%s,%s,%s,%s)
    """, ('Administrador Principal', 'admin', 'admin@empresa.com', password_hash, 1, 'aprobado'))

conn.commit()
conn.close()
print("Base de datos inicializada correctamente en PostgreSQL.")
