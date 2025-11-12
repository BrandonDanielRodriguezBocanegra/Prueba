import sqlite3

DB_NAME = 'repse_system.db'

conn = sqlite3.connect(DB_NAME)
c = conn.cursor()

# Tabla de usuarios
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

# Tabla de documentos
c.execute('''
CREATE TABLE IF NOT EXISTS documentos(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL,
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    fecha_subida TEXT NOT NULL,
    FOREIGN KEY(usuario_id) REFERENCES usuarios(id)
)
''')

# Crear admin inicial aprobado
c.execute("SELECT * FROM usuarios WHERE usuario='admin'")
if not c.fetchone():
    from werkzeug.security import generate_password_hash
    password_hash = generate_password_hash('admin123')
    c.execute("INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(?,?,?,?,?,?)",
              ('Administrador Principal', 'admin', 'admin@empresa.com', password_hash, 1, 'aprobado'))

conn.commit()
conn.close()
print("Base de datos inicializada correctamente.")
