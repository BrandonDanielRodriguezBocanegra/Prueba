# init_db.py
import os
import psycopg

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en variables de entorno.")

def main():
    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()

    # ---------- TABLA USUARIOS ----------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        nombre TEXT NOT NULL,
        usuario TEXT UNIQUE NOT NULL,
        correo TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        rol INT NOT NULL DEFAULT 2,
        estado TEXT NOT NULL DEFAULT 'pendiente'
    );
    """)

    # ---------- TABLA PROJECTS ----------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id SERIAL PRIMARY KEY,
        provider_id INT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    # ---------- AGREGAR COLUMNAS NUEVAS A PROJECTS ----------
    cur.execute("""
    ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS pedido_no TEXT,
    ADD COLUMN IF NOT EXISTS periodo_year INT,
    ADD COLUMN IF NOT EXISTS periodo_month INT;
    """)

    # ---------- TABLA DOCUMENTOS ----------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS documentos (
        id SERIAL PRIMARY KEY,
        usuario_id INT NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        project_id INT REFERENCES projects(id) ON DELETE CASCADE,
        nombre_archivo TEXT NOT NULL,
        ruta TEXT NOT NULL,
        tipo_documento TEXT NOT NULL,
        fecha_subida TIMESTAMP NOT NULL DEFAULT NOW()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("✅ Base de datos inicializada / actualizada correctamente.")

if __name__ == "__main__":
    main()
