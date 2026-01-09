# init_db.py  (psycopg v3)
import os
import psycopg
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada en el entorno.")

def main():
    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()

    # --- Usuarios ---
    cur.execute("""
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

    # --- Proyectos ---
    cur.execute("""
    CREATE TABLE IF NOT EXISTS projects(
        id SERIAL PRIMARY KEY,
        provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed INTEGER DEFAULT 0
    )
    """)

    # --- Documentos ---
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

    # --- Crear admin si no existe ---
    print("Verificando existencia del usuario administrador...")

    cur.execute("SELECT 1 FROM usuarios WHERE usuario = %s", ("admin",))
    exists = cur.fetchone()

    if not exists:
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

if __name__ == "__main__":
    main()
