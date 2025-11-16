# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg2
import psycopg2.extras
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# ---------- DATABASE CONFIG ----------
# Use DATABASE_URL env var (Render) or fallback to the hardcoded one
DATABASE_URL = os.environ.get('DATABASE_URL',
    'postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db')

def get_conn():
    # returns a new connection (close it after use)
    return psycopg2.connect(DATABASE_URL)

# ---------- UPLOAD CONFIG ----------
BASE_DIR = os.getcwd()
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------- MAIL FALLBACK (optional) ----------
# If admin hasn't stored mail credentials in DB, app can fallback to env vars.
FALLBACK_MAIL_USER = os.environ.get('MAIL_USERNAME')
FALLBACK_MAIL_PASS = os.environ.get('MAIL_PASSWORD')

# ---------- CONSTANTS ----------
DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]

# -----------------------
# Helpers
# -----------------------
def row_to_dicts(rows, cursor):
    """Convert cursor.fetchall() to list of dicts when using RealDictCursor this is not needed,
    but kept for compatibility if normal cursor is used elsewhere."""
    if isinstance(cursor, psycopg2.extras.RealDictCursor):
        return rows
    keys = [desc[0] for desc in cursor.description]
    return [dict(zip(keys, r)) for r in rows]

def send_email_via_smtp(remitente, remitente_password, destinatarios, asunto, mensaje, smtp_server='smtp.office365.com', smtp_port=587):
    """Send email using SMTP (Outlook/Office365 by default)."""
    msg = EmailMessage()
    msg['From'] = remitente
    msg['To'] = ', '.join(destinatarios)
    msg['Subject'] = asunto
    msg.set_content(mensaje)

    with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(remitente, remitente_password)
        smtp.send_message(msg)

# -----------------------
# Routes - Auth
# -----------------------
@app.route('/', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        contrasena = request.form.get('contrasena')
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur.execute('SELECT * FROM usuarios WHERE usuario=%s', (usuario,))
            user = cur.fetchone()
        finally:
            cur.close()
            conn.close()

        if user and check_password_hash(user['password'], contrasena):
            if user['estado'] == 'pendiente':
                flash('Tu cuenta está pendiente de aprobación.')
                return redirect(url_for('login'))
            session['usuario'] = user['usuario']
            session['rol'] = user['rol']
            session['user_id'] = user['id']
            if user['rol'] == 1:
                return redirect(url_for('dashboard_admin'))
            else:
                return redirect(url_for('dashboard_proveedor'))
        else:
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
        password_hash = generate_password_hash(contrasena)

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute('INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(%s,%s,%s,%s,%s,%s)',
                        (nombre, usuario, correo, password_hash, rol, 'pendiente'))
            conn.commit()
            flash('Registro exitoso. Espera aprobación del administrador.')
            return redirect(url_for('login'))
        except psycopg2.IntegrityError:
            conn.rollback()
            flash('El usuario ya existe. Elige otro usuario.')
        except Exception as e:
            conn.rollback()
            flash('Error en el registro: ' + str(e))
        finally:
            cur.close()
            conn.close()
    return render_template('registro.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# -----------------------
# Admin Dashboard
# -----------------------
@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
        pendientes = cur.fetchall()

        cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
        proveedores = cur.fetchall()

        cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
        projects = cur.fetchall()

        # documentos_por_usuario[usuario_id] = { project_id: [doc,...], ... }
        documentos_por_usuario = {}
        for p in proveedores:
            cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (p['id'],))
            docs = cur.fetchall()
            by_project = {}
            for d in docs:
                pid = d['project_id'] or 0
                by_project.setdefault(pid, []).append(d)
            documentos_por_usuario[p['id']] = by_project
    finally:
        cur.close()
        conn.close()

    return render_template('dashboard_admin.html',
                           pendientes=pendientes,
                           proveedores=proveedores,
                           documentos_por_usuario=documentos_por_usuario,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           projects=projects)

@app.route('/admin/accion/<int:id>/<accion>')
def accion(id, accion):
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))
    conn = get_conn()
    cur = conn.cursor()
    try:
        if accion == 'aprobar':
            cur.execute('UPDATE usuarios SET estado=%s WHERE id=%s', ('aprobado', id))
        else:
            # rechazado -> delete user and related data (ON DELETE CASCADE handles some if configured)
            cur.execute('DELETE FROM usuarios WHERE id=%s', (id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()
    flash('Operación realizada.')
    return redirect(url_for('dashboard_admin'))

# AJAX endpoint used in the admin template earlier (sends JSON)
@app.route('/admin/send_reminder', methods=['POST'])
def send_reminder():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'message': 'Acceso denegado'}), 403
    data = request.get_json() or {}
    provider_ids = data.get('provider_ids', [])
    subject = data.get('subject', 'Recordatorio de documentos REPSE')
    message = data.get('message', '')

    if not provider_ids:
        return jsonify({'success': False, 'message': 'No providers selected'}), 400

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    recipients = []
    try:
        for pid in provider_ids:
            cur.execute('SELECT correo FROM usuarios WHERE id=%s AND estado=%s', (pid, 'aprobado'))
            u = cur.fetchone()
            if u:
                recipients.append(u['correo'])

        # Get admin email and stored mail password if any
        cur.execute('SELECT correo, mail_password FROM usuarios WHERE usuario=%s', (session['usuario'],))
        admin_row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if not recipients:
        return jsonify({'success': False, 'message': 'No recipient emails found'}), 400

    if admin_row and admin_row.get('mail_password'):
        mail_user = admin_row['correo']
        mail_pass = admin_row['mail_password']
    else:
        # fallback to env variables if admin didn't store credentials
        mail_user = FALLBACK_MAIL_USER
        mail_pass = FALLBACK_MAIL_PASS

    sent = 0
    errors = []
    if not mail_user or not mail_pass:
        return jsonify({'success': False, 'message': 'No mail credentials available for admin'}), 400

    for r in recipients:
        try:
            send_email_via_smtp(mail_user, mail_pass, [r], subject, message, smtp_server='smtp.office365.com', smtp_port=587)
            sent += 1
        except Exception as e:
            errors.append({'to': r, 'error': str(e)})

    return jsonify({'success': True, 'sent': sent, 'errors': errors})

# Also support form POST (older template variant)
@app.route('/admin/enviar_recordatorio', methods=['POST'])
def enviar_recordatorio_form():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))
    destinatarios = request.form.getlist('destinatarios')  # expecting list of emails
    asunto = request.form.get('asunto')
    cuerpo = request.form.get('cuerpo')

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('SELECT correo, mail_password FROM usuarios WHERE usuario=%s', (session['usuario'],))
        admin_row = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if admin_row and admin_row[1]:
        mail_user = admin_row[0]
        mail_pass = admin_row[1]
    else:
        mail_user = FALLBACK_MAIL_USER
        mail_pass = FALLBACK_MAIL_PASS

    if not mail_user or not mail_pass:
        flash('No hay credenciales de correo configuradas para el administrador.')
        return redirect(url_for('dashboard_admin'))

    try:
        send_email_via_smtp(mail_user, mail_pass, destinatarios, asunto, cuerpo, smtp_server='smtp.office365.com', smtp_port=587)
        flash('Recordatorio enviado correctamente')
    except Exception as e:
        flash('Error al enviar: ' + str(e))

    return redirect(url_for('dashboard_admin'))

# -----------------------
# Provider Dashboard
# -----------------------
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    user = None
    try:
        cur.execute('SELECT * FROM usuarios WHERE usuario=%s', (session['usuario'],))
        user = cur.fetchone()
        if not user or user['estado'] != 'aprobado':
            flash('Tu cuenta aún no ha sido aprobada.')
            return redirect(url_for('login'))

        mensaje = ''
        # Handle create project or upload doc by using hidden field 'action'
        if request.method == 'POST' and request.form.get('action') == 'create_project':
            name = request.form.get('project_name')
            if name:
                cur.execute('INSERT INTO projects(provider_id, name, created_at) VALUES(%s,%s,NOW())', (user['id'], name))
                conn.commit()
                flash('Proyecto creado.')
                return redirect(url_for('dashboard_proveedor'))

        if request.method == 'POST' and request.form.get('action') == 'upload_doc':
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')
            if archivo and archivo.filename != '':
                extension = archivo.filename.rsplit('.', 1)[-1].lower()
                if extension not in ['pdf', 'jpg', 'jpeg', 'png']:
                    flash('Tipo de archivo no permitido.')
                else:
                    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"
                    filepath = os.path.join(UPLOAD_FOLDER, filename)
                    archivo.save(filepath)
                    cur.execute('INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id) VALUES(%s,%s,%s,%s,NOW(),%s)',
                                (user['id'], archivo.filename, filename, tipo, project_id))
                    conn.commit()
                    flash('Documento subido correctamente.')

        cur.execute('SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC', (user['id'],))
        projects = cur.fetchall()
        cur.execute('SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC', (user['id'],))
        docs = cur.fetchall()
    finally:
        cur.close()
        conn.close()

    # group docs by project
    docs_by_project = {}
    for d in docs:
        pid = d['project_id'] or 0
        docs_by_project.setdefault(pid, []).append(d)

    # build documentos_subidos map for template convenience
    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p['id']] = {}
        for doc in DOCUMENTOS_OBLIGATORIOS:
            for d in docs_by_project.get(p['id'], []):
                if d['tipo_documento'] == doc:
                    documentos_subidos[p['id']][doc] = d

    return render_template('dashboard_proveedor.html',
                           mensaje='',
                           projects=projects,
                           docs_by_project=docs_by_project,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           documentos_subidos=documentos_subidos)

# -----------------------
# Download uploaded files
# -----------------------
@app.route('/uploads/<path:filename>')
def descargar(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

# -----------------------
# Delete provider (admin) - JSON with password check
# -----------------------
@app.route('/admin/delete_provider', methods=['POST'])
def delete_provider():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'message': 'Acceso denegado'}), 403

    data = request.get_json() or {}
    provider_id = data.get('provider_id')
    password = data.get('password')

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cur.execute('SELECT * FROM usuarios WHERE usuario=%s', (session['usuario'],))
        admin = cur.fetchone()
        if not admin or not check_password_hash(admin['password'], password):
            return jsonify({'success': False, 'message': 'Contraseña incorrecta'}), 401

        # delete archivos from uploads folder
        cur.execute('SELECT ruta FROM documentos WHERE usuario_id=%s', (provider_id,))
        rows = cur.fetchall()
        for r in rows:
            try:
                os.remove(os.path.join(UPLOAD_FOLDER, r['ruta']))
            except Exception:
                pass

        cur.execute('DELETE FROM documentos WHERE usuario_id=%s', (provider_id,))
        cur.execute('DELETE FROM projects WHERE provider_id=%s', (provider_id,))
        cur.execute('DELETE FROM usuarios WHERE id=%s', (provider_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()

    return jsonify({'success': True})

# -----------------------
# Run (Render requires host 0.0.0.0 and PORT env)
# -----------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # debug should be False in production; Render's log earlier showed debug True — set by env if needed
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
