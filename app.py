# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import psycopg.errors
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# ---------- DATABASE CONFIG ----------
DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db'
)

def get_conn():
    return psycopg.connect(DATABASE_URL)

# ---------- UPLOAD CONFIG ----------
BASE_DIR = os.getcwd()
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

# ----------------------- LOGIN -----------------------
@app.route('/', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        contrasena = request.form.get('contrasena')

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute('SELECT * FROM usuarios WHERE usuario=%s', (usuario,))
        user = cur.fetchone()
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


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ----------------------- ADMIN DASHBOARD -----------------------
@app.route('/admin/dashboard', methods=["GET", "POST"])
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    selected = request.args.getlist("proveedores")

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
    proveedores = cur.fetchall()

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()

    documentos_por_usuario = {}
    for p in proveedores:
        cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (p['id'],))
        docs = cur.fetchall()
        by_project = {}
        for d in docs:
            pid = d['project_id'] or 0
            by_project.setdefault(pid, []).append(d)
        documentos_por_usuario[p['id']] = by_project

    cur.close()
    conn.close()

    if selected:
        proveedores = [p for p in proveedores if str(p['id']) in selected]

    return render_template(
        'dashboard_admin.html',
        pendientes=pendientes,
        proveedores=proveedores,
        documentos_por_usuario=documentos_por_usuario,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        projects=projects,
        proveedores_all=proveedores
    )

# ----------------------- APROBAR / RECHAZAR -----------------------
@app.route('/admin/accion/<int:id>/<accion>')
def accion(id, accion):
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor()
    if accion == 'aprobar':
        cur.execute("UPDATE usuarios SET estado='aprobado' WHERE id=%s", (id,))
    else:
        cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()

    flash('Operación realizada.')
    return redirect(url_for('dashboard_admin'))


# ----------------------- PROVEEDOR DASHBOARD -----------------------
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
    user = cur.fetchone()

    if not user or user['estado'] != 'aprobado':
        flash('Tu cuenta aún no ha sido aprobada.')
        return redirect(url_for('login'))

    if request.method == 'POST':
        if request.form.get('action') == 'create_project':
            name = request.form.get('project_name')
            cur.execute("INSERT INTO projects(provider_id, name, created_at) VALUES(%s,%s,NOW())",
                        (user['id'], name))
            conn.commit()
            flash('Proyecto creado.')
            return redirect(url_for('dashboard_proveedor'))

        if request.form.get('action') == 'upload_doc':
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')

            if archivo and archivo.filename != '':
                ext = archivo.filename.rsplit('.', 1)[-1].lower()
                if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
                    flash('Tipo de archivo no permitido.')
                else:
                    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s",
                                (user['id'], project_id, tipo))
                    old = cur.fetchone()
                    if old:
                        old_path = os.path.join(UPLOAD_FOLDER, old['ruta'])
                        if os.path.exists(old_path):
                            os.remove(old_path)
                        cur.execute("DELETE FROM documentos WHERE id=%s", (old['id'],))
                        conn.commit()

                    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{archivo.filename}"
                    path = os.path.join(UPLOAD_FOLDER, filename)
                    archivo.save(path)

                    cur.execute("""
                        INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id)
                        VALUES(%s,%s,%s,%s,NOW(),%s)
                    """, (user['id'], archivo.filename, filename, tipo, project_id))
                    conn.commit()
                    flash('Documento actualizado correctamente.')

    cur.execute("SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC", (user['id'],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (user['id'],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_project = {}
    for d in docs:
        pid = d['project_id'] or 0
        docs_by_project.setdefault(pid, []).append(d)

    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p['id']] = {}
        for d in docs_by_project.get(p['id'], []):
            documentos_subidos[p['id']][d['tipo_documento']] = d

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        docs_by_project=docs_by_project,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_subidos=documentos_subidos
    )


# ----------------------- DESCARGA -----------------------
@app.route('/uploads/<path:filename>')
def descargar(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# ----------------------- RUN -----------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true')
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
