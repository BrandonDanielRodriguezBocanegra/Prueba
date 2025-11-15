# init_db.py
import sqlite3, os
DB_NAME = 'repse_system.db'
conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

# usuarios table
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

# projects table
c.execute('''
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    FOREIGN KEY(provider_id) REFERENCES usuarios(id)
)
''')

# documentos table
c.execute('''
CREATE TABLE IF NOT EXISTS documentos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TEXT NOT NULL,
    tipo_documento TEXT,
    project_id INTEGER,
    FOREIGN KEY(usuario_id) REFERENCES usuarios(id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
)
''')

# create admin if not exists
c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
if not c.fetchone():
    from werkzeug.security import generate_password_hash
    password_hash = generate_password_hash('admin123')
    c.execute("INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(?,?,?,?,?,?)",
              ('Administrador Principal', 'admin', 'admin@empresa.com', password_hash, 1, 'aprobado'))

conn.commit()
conn.close()
print('DB initialized/updated.')
