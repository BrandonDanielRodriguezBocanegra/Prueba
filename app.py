import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import psycopg.errors
import boto3
from botocore.exceptions import NoCredentialsError

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# ---------- DATABASE CONFIG ----------
DATABASE_URL = os.environ.get(
    'DATABASE_URL',
    'postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db'
)

def get_conn():
    return psycopg.connect(DATABASE_URL)

# ---------- AWS S3 CONFIG ----------
USE_S3 = True  # << aquí habilitas/deshabilitas uso de S3

S3_BUCKET = 'repse-documento'
S3_REGION = 'us-east-2'
S3 = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name=S3_REGION
)

# ---------- LOCAL FALLBACK ----------
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

# ---------- HELPERS ----------
def upload_to_s3(ruta_local, nombre_s3):
    try:
        S3.upload_file(ruta_local, S3_BUCKET, nombre_s3)
        return True
    except Exception as e:
        print("Error S3 upload:", e)
        return False


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


# ----------------------- REGISTRO -----------------------
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
            cur.execute(
                'INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(%s,%s,%s,%s,%s,%s)',
                (nombre, usuario, correo, password_hash, rol, 'pendiente')
            )
            conn.commit()
            flash('Registro exitoso. Espera aprobación del administrador.')
            return redirect(url_for('login'))
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash('El usuario ya existe.')
        finally:
            cur.close()
            conn.close()

    return render_template('registro.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ----------------------- ADMIN DASHBOARD -----------------------
@app.route('/admin/dashboard', methods=["GET","POST"])
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # Filtro seleccionados
    filtro_ids = request.form.getlist('proveedores')
    if filtro_ids:
        cur.execute("SELECT * FROM usuarios WHERE id=ANY(%s)", (filtro_ids,))
        proveedores = cur.fetchall()
    else:
        cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
        proveedores = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()

    documentos_por_usuario = {}

    for p in proveedores:
        cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (p['id'],))
        docs = cur.fetchall()

        by_project = {}
        for d in docs:
            pid = d['project_id'] or 0
            tipo = d['tipo_documento']
            if pid not in by_project:
                by_project[pid] = {}

            # guardar último para obligatorios
            if tipo in DOCUMENTOS_OBLIGATORIOS:
                by_project[pid][tipo] = d
            else:
                by_project[pid]["OTRO"] = d

        documentos_por_usuario[p['id']] = by_project

    cur.close()
    conn.close()

    return render_template(
        'dashboard_admin.html',
        pendientes=pendientes,
        proveedores_select=proveedores,
        proveedores_all=get_all_proveedores(),
        documentos_por_usuario=documentos_por_usuario,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        projects=projects
    )


def get_all_proveedores():
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
    r = cur.fetchall()
    cur.close()
    conn.close()
    return r


# ----------------------- ELIMINAR USUARIO -----------------------
@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'msg': 'Acceso denegado'})

    data = request.get_json()
    user_id = data.get('id')

    if user_id == session['user_id']:
        return jsonify({'success': False, 'msg': 'No puedes borrar tu propia cuenta'})

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'msg': 'Usuario eliminado correctamente'})


# ----------------------- RECORDATORIOS -----------------------
@app.route('/admin/send_reminder', methods=['POST'])
def send_reminder():
    return jsonify({'success': True})


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

    if request.method == 'POST':
        if request.form.get('action') == 'create_project':
            name = request.form.get('project_name')
            cur.execute("INSERT INTO projects(provider_id, name, created_at) VALUES(%s,%s,NOW())",
                        (user['id'], name))
            conn.commit()
            return	redirect(url_for('dashboard_proveedor'))

        if request.form.get('action') == 'upload_doc':
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')

            if archivo and archivo.filename:
                filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"
                local_path = os.path.join(UPLOAD_FOLDER, filename)
                archivo.save(local_path)

                if USE_S3:
                    upload_to_s3(local_path, filename)

                cur.execute("""
                    INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id)
                    VALUES(%s,%s,%s,%s,NOW(),%s)
                """, (user['id'], archivo.filename, filename, tipo, project_id))
                conn.commit()
                flash("Documento subido correctamente.")

    cur.execute("SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC", (user['id'],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (user['id'],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_project = {}
    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p['id']] = {}
        for d in docs:
            if d['project_id'] == p['id']:
                documentos_subidos[p['id']][d['tipo_documento']] = d

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_subidos=documentos_subidos
    )


# ----------------------- DESCARGAS -----------------------
@app.route('/uploads/<filename>')
def descargar(filename):
    if USE_S3:
        try:
            url = S3.generate_presigned_url(
                'get_object',
                Params={'Bucket': S3_BUCKET, 'Key': filename},
                ExpiresIn=600
            )
            return redirect(url)
        except NoCredentialsError:
            return "Error de credenciales S3", 500
    else:
        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# ----------------------- RUN -----------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true')
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
