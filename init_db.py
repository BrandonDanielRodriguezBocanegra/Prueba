# init_db.py
import psycopg
from werkzeug.security import generate_password_hash

DATABASE_URL = "postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db"

with psycopg.connect(DATABASE_URL) as conn:
    with conn.cursor() as c:

        # usuarios table
        c.execute('''
        CREATE TABLE IF NOT EXISTS usuarios(
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            usuario TEXT UNIQUE NOT NULL,
            correo TEXT NOT NULL,
            password TEXT NOT NULL,
            rol INTEGER NOT NULL,
            estado TEXT NOT NULL
        )
        ''')

        # projects table
        c.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            provider_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            completed BOOLEAN DEFAULT FALSE,
            FOREIGN KEY(provider_id) REFERENCES usuarios(id)
        )
        ''')

        # documentos table
        c.execute('''
        CREATE TABLE IF NOT EXISTS documentos(
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER NOT NULL,
            nombre_archivo TEXT NOT NULL,
            ruta TEXT NOT NULL,
            fecha_subida TIMESTAMP NOT NULL,
            tipo_documento TEXT,
            project_id INTEGER,
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id),
            FOREIGN KEY(project_id) REFERENCES projects(id)
        )
        ''')

        # crear admin si no existe
        c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
        if not c.fetchone():
            password_hash = generate_password_hash('admin123')
            c.execute("""
            INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
            VALUES (%s,%s,%s,%s,%s,%s)
            """, ('Administrador Principal', 'admin', 'admin@empresa.com', password_hash, 1, 'aprobado'))

        print("DB initialized/updated.")
