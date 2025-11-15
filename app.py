# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
import os
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from flask_mail import Mail, Message
import psycopg2
import psycopg2.extras

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# CONFIG
UPLOAD_FOLDER = os.path.join(os.getcwd(), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# DATABASE CONFIG (PostgreSQL)
DB_HOST = os.environ.get('DB_HOST', 'dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'repse_db')
DB_USER = os.environ.get('DB_USER', 'repse_db_user')
DB_PASSWORD = os.environ.get('DB_PASSWORD', 'DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f')

def get_db():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    return conn

# MAIL CONFIG
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', 'you@example.com')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', 'password')
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

# ----------------- LOGIN -----------------
@app.route('/', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        contrasena = request.form['contrasena']
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute('SELECT * FROM usuarios WHERE usuario=%s', (usuario,))
        user = cur.fetchone()
        conn.close()
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
            flash('Credenciales incorrectas')
    return render_template('login.html')

# ----------------- REGISTRO -----------------
@app.route('/registro', methods=['GET','POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form['nombre']
        usuario = request.form['usuario']
        correo = request.form['correo']
        rol = int(request.form['rol'])
        password = generate_password_hash(request.form['contrasena'])
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(%s,%s,%s,%s,%s,%s)',
            (nombre, usuario, correo, password, rol, 'pendiente')
        )
        conn.commit()
        conn.close()
        flash('Registro exitoso. Espera aprobación del administrador.')
        return redirect(url_for('login'))
    return render_template('registro.html')

# ----------------- DASHBOARD ADMIN -----------------
@app.route('/admin/dashboard', methods=['GET','POST'])
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    pendientes = []
    proveedores = []
    projects = []
    documentos_por_usuario = {}

    try:
        # Pendientes
        cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
        pendientes = cur.fetchall()

        # Proveedores aprobados
        cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
        proveedores = cur.fetchall()

        # Projects
        cur.execute('SELECT * FROM projects ORDER BY created_at DESC')
        projects = cur.fetchall()

        # Documentos por usuario y proyecto
        for p in proveedores:
            cur.execute('SELECT * FROM documentos WHERE usuario_id=%s', (p['id'],))
            docs = cur.fetchall()
            by_project = {}
            for d in docs:
                pid = d['project_id'] or 0
                by_project.setdefault(pid, []).append(d)
            documentos_por_usuario[p['id']] = by_project
    finally:
        conn.close()

    return render_template('dashboard_admin.html',
                           pendientes=pendientes,
                           proveedores=proveedores,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           projects=projects,
                           documentos_por_usuario=documentos_por_usuario)

# ----------------- ACCIONES ADMIN -----------------
@app.route('/admin/accion/<int:id>/<accion>')
def accion(id, accion):
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))
    estado = 'aprobado' if accion=='aprobar' else 'rechazado'
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE usuarios SET estado=%s WHERE id=%s', (estado, id))
    conn.commit()
    conn.close()
    flash('Operación realizada.')
    return redirect(url_for('dashboard_admin'))

# ----------------- ENVIAR RECORDATORIO -----------------
@app.route('/admin/send_reminder', methods=['POST'])
def send_reminder():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'message': 'Acceso denegado'}), 403
    data = request.json
    provider_ids = data.get('provider_ids', [])
    subject = data.get('subject', 'Recordatorio de documentos REPSE')
    message = data.get('message', '')

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    recipients = []
    for pid in provider_ids:
        cur.execute('SELECT * FROM usuarios WHERE id=%s', (pid,))
        u = cur.fetchone()
        if u:
            recipients.append({'email': u['correo'], 'name': u['nombre']})
    conn.close()

    sent = 0
    for r in recipients:
        try:
            msg = Message(subject=subject, recipients=[r['email']], body=message, sender=app.config['MAIL_USERNAME'])
            mail.send(msg)
            sent += 1
        except Exception as e:
            print('Mail error:', e)

    return jsonify({'success': True, 'sent': sent})

# ----------------- DASHBOARD PROVEEDOR -----------------
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT * FROM usuarios WHERE usuario=%s', (session['usuario'],))
    user = cur.fetchone()

    if user['estado'] != 'aprobado':
        flash('Tu cuenta aún no ha sido aprobada.')
        return redirect(url_for('login'))

    mensaje = ''
    documentos_subidos = {}

    # Crear proyecto
    if request.method == 'POST' and request.form.get('action') == 'create_project':
        name = request.form.get('project_name')
        if name:
            cur.execute('INSERT INTO projects(provider_id, name, created_at) VALUES(%s,%s,NOW())',
                        (user['id'], name))
            conn.commit()
            flash('Proyecto creado.')
            return redirect(url_for('dashboard_proveedor'))

    # Subir documento
    if request.method == 'POST' and request.form.get('action') == 'upload_doc':
        project_id = int(request.form.get('project_id'))
        tipo = request.form.get('tipo_documento')
        archivo = request.files.get('documento')
        if archivo and archivo.filename != '':
            extension = archivo.filename.rsplit('.',1)[1].lower()
            if extension not in ['pdf','jpg','jpeg','png']:
                mensaje = 'Tipo de archivo no permitido.'
            else:
                filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"
                ruta = os.path.join(UPLOAD_FOLDER, filename)
                archivo.save(ruta)
                cur.execute('INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id) VALUES(%s,%s,%s,%s,NOW(),%s)',
                            (user['id'], archivo.filename, filename, tipo, project_id))
                conn.commit()
                mensaje = 'Documento subido correctamente.'

    # Obtener proyectos y documentos
    cur.execute('SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC', (user['id'],))
    projects = cur.fetchall()
    cur.execute('SELECT * FROM documentos WHERE usuario_id=%s', (user['id'],))
    docs = cur.fetchall()
    conn.close()

    # Agrupar documentos por proyecto
    docs_by_project = {}
    for d in docs:
        pid = d['project_id'] or 0
        docs_by_project.setdefault(pid, []).append(d)

    for p in projects:
        documentos_subidos[p['id']] = {}
        for doc in DOCUMENTOS_OBLIGATORIOS:
            for d in docs_by_project.get(p['id'], []):
                if d['tipo_documento'] == doc:
                    documentos_subidos[p['id']][doc] = d

    return render_template('dashboard_proveedor.html',
                           mensaje=mensaje,
                           projects=projects,
                           docs_by_project=docs_by_project,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           documentos_subidos=documentos_subidos)

# ----------------- DESCARGAR -----------------
@app.route('/uploads/<filename>')
def descargar(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

# ----------------- LOGOUT -----------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ----------------- MAIN -----------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
