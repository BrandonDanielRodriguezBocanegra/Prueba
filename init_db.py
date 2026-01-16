# init_db.py
import os
import psycopg
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada en variables de entorno.")
    # Render suele requerir sslmode=require
    if "sslmode=" not in DATABASE_URL:
        url = DATABASE_URL + ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"
    else:
        url = DATABASE_URL
    return psycopg.connect(url)

def main():
    conn = get_conn()
    cur = conn.cursor()

    # ---------------- USUARIOS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usuarios(
        id SERIAL PRIMARY KEY,
        nombre TEXT NOT NULL,
        usuario TEXT UNIQUE NOT NULL,
        correo TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        rol INTEGER NOT NULL,
        estado TEXT NOT NULL,
        mail_password TEXT,

        -- Datos extra proveedor (opcionales)
        repse_registro TEXT,
        repse_folio TEXT,
        repse_aviso_num TEXT,
        repse_aviso_fecha DATE,
        repse_vigencia DATE,
        repse_rfc TEXT,
        repse_regimen_patronal TEXT,
        repse_objeto_servicio TEXT,

        contacto_nombre TEXT,
        contacto_telefono TEXT,
        contacto_correo TEXT
    );
    """)

    # Por si existía ya la tabla sin columnas nuevas
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS mail_password TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_registro TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_folio TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_aviso_num TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_aviso_fecha DATE;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_vigencia DATE;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_rfc TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_regimen_patronal TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS repse_objeto_servicio TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS contacto_nombre TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS contacto_telefono TEXT;")
    cur.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS contacto_correo TEXT;")

    # ---------------- PROJECTS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS projects(
        id SERIAL PRIMARY KEY,
        provider_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed INTEGER DEFAULT 0,

        pedido_no TEXT,
        periodo_year INT,
        periodo_month INT
    );
    """)

    # Tu ALTER solicitado (por si ya existía)
    cur.execute("""
    ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS pedido_no TEXT,
    ADD COLUMN IF NOT EXISTS periodo_year INT,
    ADD COLUMN IF NOT EXISTS periodo_month INT;
    """)

    # ---------------- DOCUMENTOS ----------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS documentos(
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        nombre_archivo TEXT NOT NULL,
        ruta TEXT NOT NULL,
        fecha_subida TIMESTAMP NOT NULL DEFAULT NOW(),
        tipo_documento TEXT,
        project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE
    );
    """)

    # ---------------- CREAR ADMIN ----------------
    cur.execute("SELECT id FROM usuarios WHERE usuario=%s", ("admin",))
    admin = cur.fetchone()
    if not admin:
        password_hash = generate_password_hash("admin123")
        cur.execute("""
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
        print(">>> Admin creado: usuario=admin password=admin123")
    else:
        print(">>> Admin ya existe")

    conn.commit()
    cur.close()
    conn.close()
    print("Base de datos inicializada correctamente.")

if __name__ == "__main__":
    main()
