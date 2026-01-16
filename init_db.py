# init_db.py (psycopg v3)
import os
import psycopg
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no está configurada.")

def main():
    conn = psycopg.connect(DATABASE_URL)
    cur = conn.cursor()

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

    # Agregar columnas REPSE (si no existen)
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_numero TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_folio TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_aviso TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_fecha_aviso DATE;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_vigencia DATE;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rfc TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS regimen_patronal TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS objeto_servicio TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS contacto_nombre TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS contacto_telefono TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS contacto_correo TEXT;")

    # tablas restantes
    cur.execute("""
    CREATE TABLE IF NOT EXISTS projects(
        id SERIAL PRIMARY KEY,
        provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed INTEGER DEFAULT 0
    )
    """)

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

    # admin default
    cur.execute("SELECT 1 FROM usuarios WHERE usuario=%s", ("admin",))
    if not cur.fetchone():
        cur.execute("""
            INSERT INTO usuarios (nombre, usuario, correo, password, rol, estado)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (
            "Administrador Principal",
            "admin",
            "admin@empresa.com",
            generate_password_hash("admin123"),
            1,
            "aprobado"
        ))

    conn.commit()
    cur.close()
    conn.close()
    print("DB lista.")

if __name__ == "__main__":
    main()
