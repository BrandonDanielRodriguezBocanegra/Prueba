# app.py (archivo completo, reemplazar el actual)
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
import sqlite3, os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from flask_mail import Mail, Message

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# Config
DB_NAME = os.path.join(os.getcwd(), 'repse_system.db')
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Flask-Mail config (set env vars in production)
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
mail = Mail(app)

DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

# ---------------------------
# AUTH
# ---------------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        contrasena = request.form.get('contrasena')
        conn = get_db()
        user = conn.execute('SELECT * FROM usuarios WHERE usuario=?', (usuario,)).fetchone()
        if user and check_password_hash(user['password'], contrasena):
            if user['estado'] == 'pendiente':
                flash('Tu cuenta está pendiente de aprobación.')
                return redirect(url_for('login'))
            session['id'] = user['id']
            session['usuario'] = user['usuario']
            session['rol'] = user['rol']
            return redirect(url_for('dashboard_admin' if user['rol']==1 else 'dashboard_proveedor'))
        flash('Credenciales incorrectas')
    return render_template('login.html')

@app.route('/registro', methods=['GET','POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        usuario = request.form.get('usuario')
        correo = request.form.get('correo')
        contrasena = request.form.get('contrasena')
        rol = int(request.form.get('rol') or 2)
        pw_hash = generate_password_hash(contrasena)
        conn = get_db()
        try:
            conn.execute('INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES (?,?,?,?,?,?)',
                         (nombre, usuario, correo, pw_hash, rol, 'pendiente'))
            conn.commit()
            flash('Registro exitoso. Espera aprobación del administrador.')
            return redirect(url_for('login'))
        except Exception as e:
            flash('Error al registrar: ' + str(e))
    return render_template('registro.html')

# ---------------------------
# ADMIN DASHBOARD
# ---------------------------
@app.route('/admin/dashboard')
def dashboard_admin():
    if 'id' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_db()
    pendientes = conn.execute("SELECT * FROM usuarios WHERE estado='pendiente'").fetchall()
    proveedores = conn.execute("SELECT * FROM usuarios WHERE rol=2 AND estado='aprobado'").fetchall()

    # projects with provider info
    projects = conn.execute("""
        SELECT p.*, u.nombre as proveedor_nombre, u.correo as proveedor_correo
        FROM projects p
        LEFT JOIN usuarios u ON u.id = p.provider_id
        ORDER BY p.created_at DESC
    """).fetchall()

    # Build documentos_por_usuario: uid -> project_id -> [docs]
    docs = conn.execute('SELECT * FROM documentos').fetchall()
    documentos_por_usuario = {}
    for prov in proveedores:
        documentos_por_usuario[prov['id']] = {}
    for d in docs:
        uid = d['usuario_id']
        pid = d['project_id'] or 0
        documentos_por_usuario.setdefault(uid, {}).setdefault(pid, []).append(d)

    # También construimos proyectos_por_usuario para la vista sencilla (nombre_proyecto, fecha, archivos)
    proyectos_por_usuario = {}
    for proj in projects:
        pid = proj['id']
        proyectos_por_usuario.setdefault(proj['provider_id'], []).append({
            'id': pid,
            'name': proj['name'],
            'created_at': proj['created_at']
        })

    return render_template('dashboard_admin.html',
                           pendientes=pendientes,
                           proveedores=proveedores,
                           projects=projects,
                           documentos_por_usuario=documentos_por_usuario,
                           proyectos_por_usuario=proyectos_por_usuario,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)

# approve/reject
@app.route('/admin/accion/<int:uid>/<accion>')
def accion(uid, accion):
    if 'id' not in session or session.get('rol') != 1:
        flash('Acceso denegado'); return redirect(url_for('login'))
    estado = 'aprobado' if accion == 'aprobar' else 'rechazado'
    conn = get_db()
    conn.execute('UPDATE usuarios SET estado=? WHERE id=?', (estado, uid))
    conn.commit()
    flash('Operación realizada.')
    return redirect(url_for('dashboard_admin'))

# ---------------------------
# ELIMINAR PROVEEDOR (GET muestra confirm; POST realiza)
# ---------------------------
@app.route('/eliminar_proveedor/<int:provider_id>', methods=['GET','POST'])
def eliminar_proveedor(provider_id):
    if 'id' not in session or session.get('rol') != 1:
        flash('Acceso denegado'); return redirect(url_for('login'))
    conn = get_db()
    prov = conn.execute('SELECT * FROM usuarios WHERE id=?', (provider_id,)).fetchone()
    if not prov:
        flash('Proveedor no encontrado'); return redirect(url_for('dashboard_admin'))

    if request.method == 'POST':
        password = request.form.get('password','')
        admin = conn.execute('SELECT * FROM usuarios WHERE id=?', (session['id'],)).fetchone()
        if not check_password_hash(admin['password'], password):
            flash('Contraseña incorrecta'); return redirect(url_for('eliminar_proveedor', provider_id=provider_id))
        # delete files
        docs = conn.execute('SELECT ruta FROM documentos WHERE usuario_id=?', (provider_id,)).fetchall()
        for r in docs:
            path = os.path.join(UPLOAD_FOLDER, r['ruta'])
            try: os.remove(path)
            except: pass
        conn.execute('DELETE FROM documentos WHERE usuario_id=?', (provider_id,))
        conn.execute('DELETE FROM projects WHERE provider_id=?', (provider_id,))
        conn.execute('DELETE FROM usuarios WHERE id=?', (provider_id,))
        conn.commit()
        flash('Proveedor eliminado correctamente.')
        return redirect(url_for('dashboard_admin'))

    # GET -> render simple confirmation page (template minimal)
    return render_template('confirm_delete_provider.html', provider=prov)

# ---------------------------
# ELIMINAR PROYECTO (GET confirm; POST delete)
# ---------------------------
@app.route('/eliminar_proyecto/<int:project_id>', methods=['GET','POST'])
def eliminar_proyecto(project_id):
    if 'id' not in session or session.get('rol') != 1:
        flash('Acceso denegado'); return redirect(url_for('login'))
    conn = get_db()
    proj = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if not proj:
        flash('Proyecto no encontrado'); return redirect(url_for('dashboard_admin'))

    if request.method == 'POST':
        password = request.form.get('password','')
        admin = conn.execute('SELECT * FROM usuarios WHERE id=?', (session['id'],)).fetchone()
        if not check_password_hash(admin['password'], password):
            flash('Contraseña incorrecta'); return redirect(url_for('eliminar_proyecto', project_id=project_id))
        # delete files
        docs = conn.execute('SELECT ruta FROM documentos WHERE project_id=?', (project_id,)).fetchall()
        for r in docs:
            path = os.path.join(UPLOAD_FOLDER, r['ruta'])
            try: os.remove(path)
            except: pass
        conn.execute('DELETE FROM documentos WHERE project_id=?', (project_id,))
        conn.execute('DELETE FROM projects WHERE id=?', (project_id,))
        conn.commit()
        flash('Proyecto eliminado correctamente.')
        return redirect(url_for('dashboard_admin'))

    return render_template('confirm_delete_project.html', project=proj)

# ---------------------------
# ENVIAR RECORDATORIO (form POST)
# ---------------------------
@app.route('/enviar_recordatorio', methods=['POST'])
def enviar_recordatorio():
    if 'id' not in session or session.get('rol') != 1:
        flash('Acceso denegado'); return redirect(url_for('login'))
    provider_ids = request.form.getlist('proveedores')
    mensaje = request.form.get('mensaje','')
    sent = 0
    conn = get_db()
    for pid in provider_ids:
        u = conn.execute('SELECT * FROM usuarios WHERE id=?', (int(pid),)).fetchone()
        if u and u['correo']:
            try:
                if app.config.get('MAIL_USERNAME'):  # only if mail configured
                    msg = Message(subject='Recordatorio REPSE', recipients=[u['correo']],
                                  body=mensaje, sender=app.config.get('MAIL_USERNAME'))
                    mail.send(msg)
                else:
                    app.logger.info('Mail not configured; would send to %s: %s', u['correo'], mensaje)
                sent += 1
            except Exception as e:
                app.logger.error('Error sending mail: %s', e)
    flash(f'Recordatorios enviados: {sent}')
    return redirect(url_for('dashboard_admin'))

# ---------------------------
# DASHBOARD PROVEEDOR (projects + uploads)
# ---------------------------
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'id' not in session or session.get('rol') != 2:
        flash('Acceso denegado'); return redirect(url_for('login'))
    conn = get_db()
    user = conn.execute('SELECT * FROM usuarios WHERE id=?', (session['id'],)).fetchone()
    if not user or user['estado'] != 'aprobado':
        flash('Tu cuenta no está aprobada'); return redirect(url_for('login'))

    mensaje = ''
    # crear proyecto
    if request.method == 'POST' and request.form.get('action') == 'create_project':
        name = request.form.get('project_name')
        if name:
            conn.execute('INSERT INTO projects(provider_id, name, created_at) VALUES (?,?,datetime("now"))', (user['id'], name))
            conn.commit()
            flash('Proyecto creado.')
            return redirect(url_for('dashboard_proveedor'))

    # subir documento
    if request.method == 'POST' and request.form.get('action') == 'upload_doc':
        project_id = int(request.form.get('project_id') or 0)
        tipo = request.form.get('tipo_documento')
        archivo = request.files.get('documento')
        if archivo and archivo.filename:
            ext = archivo.filename.rsplit('.',1)[-1].lower()
            if ext not in ['pdf','jpg','jpeg','png']:
                mensaje = 'Tipo de archivo no permitido.'
            else:
                filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"
                ruta = os.path.join(UPLOAD_FOLDER, filename)
                archivo.save(ruta)
                conn.execute('INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id) VALUES (?,?,?,?,datetime("now"),?)',
                             (user['id'], archivo.filename, filename, tipo, project_id))
                conn.commit()
                mensaje = 'Documento subido correctamente.'

    projects = conn.execute('SELECT * FROM projects WHERE provider_id=? ORDER BY created_at DESC', (user['id'],)).fetchall()
    docs = conn.execute('SELECT * FROM documentos WHERE usuario_id=?', (user['id'],)).fetchall()
    docs_by_project = {}
    for d in docs:
        pid = d['project_id'] or 0
        docs_by_project.setdefault(pid, []).append(d)

    return render_template('dashboard_proveedor.html',
                           mensaje=mensaje,
                           projects=projects,
                           docs_by_project=docs_by_project,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)

# ---------------------------
# DOWNLOAD
# ---------------------------
@app.route('/uploads/<path:filename>')
def descargar(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

# ---------------------------
# LOGOUT
# ---------------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------------------------
if __name__ == '__main__':
    # ensure DB exists (init if needed)
    if not os.path.exists(DB_NAME):
        try:
            import init_db
        except Exception as e:
            print('init_db import failed:', e)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
