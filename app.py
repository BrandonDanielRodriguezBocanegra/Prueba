# app.py (actualizado con AWS S3 y mejoras)
import os
import boto3
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import psycopg.errors

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# ---------- STORAGE CONFIG ----------
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3")  # "local" si en futuro cambias
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "repse-documento")
S3_REGION = os.getenv("AWS_REGION", "us-east-2")

# Cliente AWS S3
s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=S3_REGION
)

BASE_DIR = os.getcwd()
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')  # Solo si habilitas local
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ---------- DATABASE ----------
DATABASE_URL = os.getenv("DATABASE_URL")
def get_conn():
    return psycopg.connect(DATABASE_URL)

# ---------- DOCUMENTOS REQUERIDOS ----------
DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]

# ---------- STORAGE HELPERS ----------
def upload_to_storage(file_path, filename):
    if STORAGE_BACKEND == "s3":
        s3.upload_file(file_path, S3_BUCKET, filename)
        os.remove(file_path)  # Elimina local después de subir
        return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{filename}"
    else:
        return filename


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

            return redirect(url_for('dashboard_admin' if user['rol']==1 else 'dashboard_proveedor'))
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
        except:
            conn.rollback()
            flash('Error en el registro')
        finally:
            cur.close()
            conn.close()

    return render_template('registro.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ----------------------- ADMIN DASHBOARD -----------------------
@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        return redirect(url_for('login'))

    filtro = request.args.get("proveedor")
    
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    sql_proveedores = "SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2"
    params = ()
    if filtro:
        sql_proveedores += " AND id=%s"
        params = (filtro,)

    cur.execute(sql_proveedores, params)
    proveedores = cur.fetchall()

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()

    documentos_por_usuario = {}
    for p in proveedores:
        cur.execute("SELECT * FROM documentos WHERE usuario_id=%s", (p['id'],))
        docs = cur.fetchall()
        by_project = {}
        for d in docs:
            by_project.setdefault(d['project_id'], []).append(d)
        documentos_por_usuario[p['id']] = by_project

    cur.close()
    conn.close()

    return render_template(
        'dashboard_admin.html',
        pendientes=pendientes,
        proveedores=proveedores,
        documentos_por_usuario=documentos_por_usuario,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        projects=projects,
        filtro=filtro
    )


# ----------------------- ELIMINAR USUARIO -----------------------
@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False})

    user_id = request.json.get('id')
    if user_id == session['user_id']:
        return jsonify({'success': False, 'msg': 'No puedes borrarte a ti mismo'})

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True})


# ----------------------- PROVEEDOR DASHBOARD -----------------------
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
    user = cur.fetchone()

    if request.method == 'POST' and request.form.get('action') == 'upload_doc':
        archivo = request.files.get('documento')
        project_id = request.form.get('project_id')
        tipo_doc = request.form.get('tipo_documento')

        if archivo:
            filename = f"{datetime.utcnow().timestamp()}_{archivo.filename}"
            local_path = os.path.join(UPLOAD_FOLDER, filename)
            archivo.save(local_path)

            file_url = upload_to_storage(local_path, filename)

            cur.execute("""INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id)
                           VALUES(%s,%s,%s,%s,NOW(),%s)""",
                        (user['id'], archivo.filename, file_url, tipo_doc, project_id))
            conn.commit()
            flash("Documento subido con éxito")

    cur.execute("SELECT * FROM projects WHERE provider_id=%s", (user['id'],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s", (user['id'],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p['id']] = {}
        for d in docs:
            if d["project_id"] == p["id"]:
                documentos_subidos[p['id']][d['tipo_documento']] = d

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_subidos=documentos_subidos
    )


# ----------------------- RUN -----------------------
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)))
