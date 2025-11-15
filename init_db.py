# init_db.py
import sqlite3
from werkzeug.security import generate_password_hash

DB_NAME = 'repse_system.db'
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

# tabla usuarios
c.execute('''
CREATE TABLE IF NOT EXISTS usuarios(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    usuario TEXT UNIQUE NOT NULL,
    correo TEXT NOT NULL,
    password TEXT NOT NULL,
    rol INTEGER NOT NULL,
    estado TEXT NOT NULL
)
''')

# tabla proyectos
c.execute('''
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(provider_id) REFERENCES usuarios(id)
)
''')

# tabla documentos
c.execute('''
CREATE TABLE IF NOT EXISTS documentos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    tipo_documento TEXT,
    fecha_subida TEXT NOT NULL,
    FOREIGN KEY(usuario_id) REFERENCES usuarios(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
)
''')

# crear admin si no existe
c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
if not c.fetchone():
    password_hash = generate_password_hash('admin123')
    c.execute("INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(?,?,?,?,?,?)",
              ('Administrador Principal', 'admin', 'admin@empresa.com', password_hash, 1, 'aprobado'))

conn.commit()
conn.close()
print('DB initialized/updated.')
