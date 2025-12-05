import os
import boto3
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')


# ---------- AWS S3 CONFIG ----------
S3_BUCKET = os.environ.get('S3_BUCKET_NAME')
S3_REGION = os.environ.get('AWS_REGION')

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name=S3_REGION
)

def s3_url(filename):
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{filename}"


# ---------- DATABASE ----------
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_conn():
    return psycopg.connect(DATABASE_URL)


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


# ==========================================================
#                      LOGIN
# ==========================================================
@app.route('/', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        contrasena = request.form.get('contrasena')

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user['password'], contrasena):
            if user['estado'] == "pendiente":
                flash("Tu cuenta está pendiente de aprobación.")
                return redirect(url_for("login"))

            session['usuario'] = user['usuario']
            session['rol'] = user['rol']
            session['user_id'] = user['id']

            if user['rol'] == 1:
                return redirect(url_for('dashboard_admin'))
            else:
                return redirect(url_for('dashboard_proveedor'))
        else:
            flash("Credenciales inválidas")

    return render_template('login.html')


# ==========================================================
#                    REGISTRO
# ==========================================================
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
            cur.execute("""
                INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                VALUES(%s,%s,%s,%s,%s,%s)
            """, (nombre, usuario, correo, password_hash, rol, "pendiente"))
            conn.commit()
            flash("Registrado correctamente, espera aprobación.")
            return redirect(url_for('login'))
        except:
            conn.rollback()
            flash("El usuario ya existe.")
        finally:
            cur.close()
            conn.close()

    return render_template('registro.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ==========================================================
#                 DASHBOARD PROVEEDOR
# ==========================================================
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
    user = cur.fetchone()

    if user['estado'] != "aprobado":
        flash("Tu cuenta aún no está aprobada.")
        return redirect(url_for('login'))


    if request.method == 'POST':
        action = request.form.get('action')

        # Crear proyecto
        if action == 'create_project':
            name = request.form.get('project_name')
            cur.execute("INSERT INTO projects(provider_id, name) VALUES(%s,%s)",
                        (user['id'], name))
            conn.commit()

        # Subida documento
        if action == 'upload_doc':
            file = request.files.get('documento')
            tipo = request.form.get('tipo_documento')
            project_id = int(request.form.get('project_id'))

            if file and file.filename != '':
                ext = file.filename.rsplit('.',1)[-1].lower()
                if ext not in ['pdf','jpg','jpeg','png']:
                    flash("Archivo inválido")
                else:
                    # Borrar archivo anterior del mismo tipo
                    cur.execute("""
                        SELECT ruta FROM documentos 
                        WHERE usuario_id=%s AND tipo_documento=%s AND project_id=%s
                    """,(user['id'], tipo, project_id))
                    old = cur.fetchone()
                    if old:
                        try:
                            s3.delete_object(Bucket=S3_BUCKET, Key=old['ruta'])
                        except: pass
                        cur.execute("DELETE FROM documentos WHERE ruta=%s",(old['ruta'],))

                    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{file.filename}"
                    s3.upload_fileobj(file, S3_BUCKET, filename)

                    cur.execute("""
                        INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, project_id)
                        VALUES(%s,%s,%s,%s,%s)
                    """,(user['id'],file.filename, filename, tipo, project_id))
                    conn.commit()
                    flash("Documento actualizado exitosamente")

        return redirect(url_for('dashboard_proveedor'))

    # Consultar datos
    cur.execute("SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC",(user['id'],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s",(user['id'],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_project = {}
    for d in docs:
        docs_by_project.setdefault(d['project_id'], {})[d['tipo_documento']] = d

    return render_template('dashboard_proveedor.html',
                           projects=projects,
                           documentos_subidos=docs_by_project,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           s3_url=s3_url)


# ==========================================================
#                 DASHBOARD ADMIN
# ==========================================================
@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
    proveedores = cur.fetchall()

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos ORDER BY fecha_subida DESC")
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_user = {}
    for d in docs:
        docs_by_user.setdefault(d['usuario_id'], {}).setdefault(d['project_id'], []).append(d)

    return render_template("dashboard_admin.html",
                           proveedores=proveedores,
                           pendientes=pendientes,
                           projects=projects,
                           docs_by_user=docs_by_user,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           s3_url=s3_url)


# ==========================================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)))
