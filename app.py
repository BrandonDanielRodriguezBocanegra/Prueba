import os
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import psycopg
import psycopg.rows
import boto3
from botocore.exceptions import NoCredentialsError

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

# ---------- DATABASE ----------
DATABASE_URL = os.environ.get('DATABASE_URL')
def get_conn():
    return psycopg.connect(DATABASE_URL)

# ---------- AWS S3 CONFIG ----------
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET = os.getenv("AWS_S3_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

# ---------- DOCUMENTOS OBLIGATORIOS ----------
DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]


# ======================================================
# LOGIN (NO se modificó nada)
# ======================================================
@app.route('/', methods=['GET', 'POST'])
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

        flash('Credenciales incorrectas')
    return render_template('login.html')


# ======================================================
# REGISTRO (NO se modificó nada)
# ======================================================
@app.route('/registro', methods=['GET', 'POST'])
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
            cur.execute("""
                INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                VALUES(%s,%s,%s,%s,%s,%s)
            """, (nombre, usuario, correo, password_hash, rol, "aprobado"))
            conn.commit()
            flash('Registro exitoso.')
            return redirect(url_for('login'))
        except Exception as e:
            conn.rollback()
            flash("Error: " + str(e))
        finally:
            cur.close()
            conn.close()

    return render_template('registro.html')


# ======================================================
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ======================================================
# DASHBOARD ADMIN COMPLETO
# ======================================================
@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
    proveedores = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM projects")
    projects = cur.fetchall()

    cur.execute("""
        SELECT d.*, u.nombre AS proveedor_nombre, p.name AS project_name
        FROM documentos d
        JOIN usuarios u ON d.usuario_id = u.id
        JOIN projects p ON d.project_id = p.id
    """)
    documentos = cur.fetchall()

    cur.close()
    conn.close()

    # Mapear documentos por proveedor y proyecto
    docs_map = {}
    for d in documentos:
        docs_map.setdefault(d['usuario_id'], {}).setdefault(d['project_id'], []).append(d)

    return render_template('dashboard_admin.html',
                           proveedores=proveedores,
                           pendientes=pendientes,
                           projects=projects,
                           documentos_por_usuario=docs_map,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)


# ======================================================
# DASHBOARD PROVEEDOR (Sube/Reemplaza)
# ======================================================
@app.route('/proveedor/dashboard', methods=['GET', 'POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
    user = cur.fetchone()

    if request.method == 'POST':
        project_id = request.form.get('project_id')
        tipo_doc = request.form.get('tipo_documento')
        file = request.files.get('documento')

        if file:
            filename = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{file.filename}"

            try:
                s3.upload_fileobj(file, S3_BUCKET, filename)

                cur.execute("""SELECT ruta FROM documentos
                               WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s""",
                            (user['id'], project_id, tipo_doc))
                old = cur.fetchone()
                if old:
                    try:
                        s3.delete_object(Bucket=S3_BUCKET, Key=old['ruta'])
                    except:
                        pass

                cur.execute("DELETE FROM documentos WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s",
                            (user['id'], project_id, tipo_doc))

                cur.execute("""
                    INSERT INTO documentos(usuario_id, nombre_archivo, ruta, fecha_subida, tipo_documento, project_id)
                    VALUES(%s,%s,%s,NOW(),%s,%s)
                """, (user['id'], file.filename, filename, tipo_doc, project_id))

                conn.commit()
                flash("Documento actualizado correctamente.")

            except NoCredentialsError:
                flash("Error de credenciales AWS")

    cur.execute("SELECT * FROM projects WHERE provider_id=%s", (user['id'],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s", (user['id'],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_map = {}
    for d in docs:
        docs_map.setdefault(d['project_id'], {})[d['tipo_documento']] = d

    return render_template('dashboard_proveedor.html',
                           projects=projects,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           docs_map=docs_map)


# ======================================================
# DESCARGA ADMIN DESDE S3
# ======================================================
@app.route('/admin/download/<ruta>')
def admin_download(ruta):
    if 'usuario' not in session or session.get('rol') != 1:
        return redirect(url_for('login'))

    url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': S3_BUCKET, 'Key': ruta},
        ExpiresIn=300
    )
    return redirect(url)


# ======================================================
# BORRAR USUARIO (solo admin)
# ======================================================
@app.route('/admin/delete_user/<int:id>')
def delete_user(id):
    if 'usuario' not in session or session.get('rol') != 1:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()

    flash("Usuario eliminado correctamente.")
    return redirect(url_for('dashboard_admin'))


# ======================================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))
