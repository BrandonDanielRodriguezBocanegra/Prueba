from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'supersecretkey'

# --- CONFIGURACIONES ---
DB_NAME = 'repse_system.db'
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]

# --- CONEXIÓN BASE DE DATOS ---
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# --- LOGIN ---
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        contrasena = request.form['contrasena']

        conn = get_db()
        user = conn.execute('SELECT * FROM usuarios WHERE usuario=?', (usuario,)).fetchone()

        if user and check_password_hash(user['password'], contrasena):
            if user['estado'] == 'pendiente':
                flash('Tu cuenta está pendiente de aprobación.')
                return redirect(url_for('login'))

            session['usuario'] = user['usuario']
            session['rol'] = user['rol']

            if user['rol'] == 1:
                return redirect(url_for('dashboard_admin'))
            else:
                return redirect(url_for('dashboard_proveedor'))
        else:
            flash('Credenciales incorrectas.')
    return render_template('login.html')

# --- REGISTRO ---
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form['nombre']
        usuario = request.form['usuario']
        correo = request.form['correo']
        rol = int(request.form['rol'])
        password = generate_password_hash(request.form['contrasena'])

        conn = get_db()
        conn.execute('INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(?,?,?,?,?,?)',
                     (nombre, usuario, correo, password, rol, 'pendiente'))
        conn.commit()
        flash('Registro exitoso. Espera aprobación del administrador.')
        return redirect(url_for('login'))

    return render_template('registro.html')

# --- DASHBOARD ADMIN ---
@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session['rol'] != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_db()
    pendientes = conn.execute("SELECT * FROM usuarios WHERE estado='pendiente'").fetchall()
    proveedores = conn.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2").fetchall()

    documentos_por_usuario = {}
    for p in proveedores:
        docs = conn.execute("SELECT * FROM documentos WHERE usuario_id=?", (p['id'],)).fetchall()
        documentos_por_usuario[p['id']] = {d['tipo_documento']: d for d in docs}

    return render_template('dashboard_admin.html',
                           pendientes=pendientes,
                           proveedores=proveedores,
                           documentos_por_usuario=documentos_por_usuario,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)

# --- APROBAR / RECHAZAR ---
@app.route('/admin/accion/<int:id>/<accion>')
def accion(id, accion):
    if 'usuario' not in session or session['rol'] != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    estado = 'aprobado' if accion == 'aprobar' else 'rechazado'
    conn = get_db()
    conn.execute('UPDATE usuarios SET estado=? WHERE id=?', (estado, id))
    conn.commit()

    return redirect(url_for('dashboard_admin'))

# --- DESCARGAR DOCUMENTOS ---
@app.route('/uploads/<filename>')
def descargar(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- DASHBOARD PROVEEDOR ---
@app.route('/proveedor/dashboard', methods=['GET', 'POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session['rol'] != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_db()
    user = conn.execute('SELECT * FROM usuarios WHERE usuario=?', (session['usuario'],)).fetchone()

    if user['estado'] != 'aprobado':
        flash('Tu cuenta aún no ha sido aprobada.')
        return redirect(url_for('login'))

    mensaje = ''
    if request.method == 'POST':
        tipo = request.form['tipo_documento']
        if 'documento' in request.files:
            archivo = request.files['documento']
            if archivo.filename != '':
                extension = archivo.filename.rsplit('.', 1)[1].lower()
                if extension not in ['pdf', 'jpg', 'jpeg', 'png']:
                    mensaje = 'Tipo de archivo no permitido.'
                else:
                    ruta = os.path.join(UPLOAD_FOLDER, archivo.filename)
                    archivo.save(ruta)
                    conn.execute('INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida) VALUES(?,?,?,?,datetime("now"))',
                                 (user['id'], archivo.filename, archivo.filename, tipo))
                    conn.commit()
                    mensaje = f'Documento "{tipo}" subido correctamente.'

    docs_subidos = conn.execute('SELECT * FROM documentos WHERE usuario_id=?', (user['id'],)).fetchall()
    docs_dict = {d['tipo_documento']: d for d in docs_subidos}

    return render_template('dashboard_proveedor.html',
                           mensaje=mensaje,
                           documentos_subidos=docs_dict,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)

# --- GESTIÓN DE USUARIOS (ELIMINAR PROVEEDORES) ---
@app.route('/admin/eliminar_usuario', methods=['POST'])
def eliminar_usuario():
    if 'usuario' not in session or session['rol'] != 1:
        return jsonify({"error": "Acceso denegado"}), 403

    data = request.json
    usuario_id = data.get('usuario_id')
    password = data.get('password')

    conn = get_db()
    admin = conn.execute('SELECT * FROM usuarios WHERE usuario=?', (session['usuario'],)).fetchone()

    if not check_password_hash(admin['password'], password):
        return jsonify({"error": "Contraseña incorrecta"}), 401

    # Borrar documentos asociados
    docs = conn.execute('SELECT ruta FROM documentos WHERE usuario_id=?', (usuario_id,)).fetchall()
    for doc in docs:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, doc['ruta']))
        except:
            pass

    conn.execute('DELETE FROM documentos WHERE usuario_id=?', (usuario_id,))
    conn.execute('DELETE FROM usuarios WHERE id=?', (usuario_id,))
    conn.commit()

    return jsonify({"success": True})

# --- LOGOUT ---
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- INICIO APP ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
