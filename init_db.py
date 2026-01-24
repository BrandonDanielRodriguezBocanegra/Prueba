# init_db.py  (psycopg v3)
import os
import psycopg
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    if "sslmode=" in url:
        return url
    joiner = "&" if "?" in url else "?"
    return url + f"{joiner}sslmode=require"

def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está definido en variables de entorno.")

    conn = psycopg.connect(_normalize_db_url(DATABASE_URL))
    cur = conn.cursor()

    # ------------------ TABLA USUARIOS ------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usuarios(
        id SERIAL PRIMARY KEY,
        nombre TEXT NOT NULL,
        usuario TEXT UNIQUE NOT NULL,
        correo TEXT NOT NULL,
        password TEXT NOT NULL,
        rol INTEGER NOT NULL,
        estado TEXT NOT NULL,
        mail_password TEXT,

        -- columnas legacy (si ya las usabas antes, se quedan)
        empresa TEXT,
        rfc TEXT,
        repse TEXT,
        domicilio TEXT,
        telefono TEXT,
        representante_legal TEXT,

        -- ===== NUEVAS columnas para datos REPSE/Contacto del registro.html =====
        repse_numero TEXT,
        repse_folio TEXT,
        repse_aviso TEXT,
        repse_fecha_aviso DATE,
        repse_vigencia DATE,
        repse_regimen TEXT,
        repse_objeto TEXT,

        contacto_nombre TEXT,
        contacto_tel TEXT,
        contacto_correo TEXT
    )
    """)

    # Si tu tabla ya existía, aseguramos columnas con ALTER (no rompe nada)
    cur.execute("""
    ALTER TABLE usuarios
        ADD COLUMN IF NOT EXISTS repse_numero TEXT,
        ADD COLUMN IF NOT EXISTS repse_folio TEXT,
        ADD COLUMN IF NOT EXISTS repse_aviso TEXT,
        ADD COLUMN IF NOT EXISTS repse_fecha_aviso DATE,
        ADD COLUMN IF NOT EXISTS repse_vigencia DATE,
        ADD COLUMN IF NOT EXISTS repse_regimen TEXT,
        ADD COLUMN IF NOT EXISTS repse_objeto TEXT,
        ADD COLUMN IF NOT EXISTS contacto_nombre TEXT,
        ADD COLUMN IF NOT EXISTS contacto_tel TEXT,
        ADD COLUMN IF NOT EXISTS contacto_correo TEXT
    """)

    # NOTA: tu formulario manda RFC como "repse_rfc"
    # y en DB ya tienes columna "rfc" (legacy). La usaremos para guardar ese valor.

    # ------------------ TABLA PROJECTS ------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS projects(
        id SERIAL PRIMARY KEY,
        provider_id INTEGER NOT NULL REFERENCES usuarios(id),
        name TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        completed INTEGER DEFAULT 0
    )
    """)

    # ---- ALTER para nuevos campos (pedido_no, periodo_year, periodo_month) ----
    cur.execute("""
    ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS pedido_no TEXT,
    ADD COLUMN IF NOT EXISTS periodo_year INT,
    ADD COLUMN IF NOT EXISTS periodo_month INT
    """)

    # ------------------ TABLA DOCUMENTOS ------------------
    cur.execute("""
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

    # ------------------ NUEVA: enabled_periods ------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS enabled_periods(
        id SERIAL PRIMARY KEY,
        provider_id INTEGER NOT NULL REFERENCES usuarios(id),
        periodo_year INT NOT NULL,
        periodo_month INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        UNIQUE(provider_id, periodo_year, periodo_month)
    )
    """)

    # ------------------ NUEVA: project_docs ------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS project_docs(
        id SERIAL PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id),
        tipo_documento TEXT NOT NULL,
        aplica BOOLEAN NOT NULL DEFAULT FALSE,
        completed BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE(project_id, tipo_documento)
    )
    """)

    # ------------------ CREAR ADMIN SI NO EXISTE ------------------
    print("Verificando existencia del usuario administrador...")

    cur.execute("SELECT id FROM usuarios WHERE usuario=%s", ("admin",))
    admin = cur.fetchone()

    if not admin:
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
