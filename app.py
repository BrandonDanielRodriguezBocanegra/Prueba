import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
import psycopg
import psycopg.rows
import boto3
from botocore.exceptions import NoCredentialsError

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_conn():
    return psycopg.connect(DATABASE_URL)

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")
S3_BUCKET = os.getenv("AWS_BUCKET_NAME")

s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION
)

DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form.get('usuario')
        contrasena = request.form.get('contrasena')

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
        user = cur.fetchone()
        cur.close(); conn.close()

        if user and check_password_hash(user['password'], contrasena):
            if user['estado'] == 'pendiente':
                flash("Tu cuenta está pendiente de aprobación.")
                return redirect(url_for('login'))

            session.update({
                'usuario': user['usuario'],
                'rol': user['rol'],
                'user_id': user['id']
            })

            return redirect(url_for('dashboard_admin') if user['rol']==1 else url_for('dashboard_proveedor'))

        flash("Credenciales incorrectas")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

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

    cur.execute("SELECT * FROM documentos")
    docs = cur.fetchall()

    cur.close()
    conn.close()

    documentos_por_usuario = {}
    for d in docs:
        documentos_por_usuario.setdefault(d['usuario_id'], {}).setdefault(d['project_id'], []).append(d)

    return render_template(
        'dashboard_admin.html',
        proveedores=proveedores,
        pendientes=pendientes,
        projects=projects,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_por_usuario=documentos_por_usuario
    )

@app.route('/accion/<int:id>/<accion>')
def accion(id, accion):
    if session.get('rol') != 1: return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor()
    if accion == 'aprobar':
        cur.execute("UPDATE usuarios SET estado='aprobado' WHERE id=%s", (id,))
    else:
        cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    conn.commit()
    cur.close(); conn.close()

    return redirect(url_for('dashboard_admin'))

@app.route('/delete_user', methods=['POST'])
def delete_user():
    data = request.get_json()
    id = data['id']

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    conn.commit()
    cur.close(); conn.close()

    return jsonify({"success": True, "msg": "Usuario eliminado"})

@app.route('/send_reminder', methods=['POST'])
def send_reminder():
    data = request.get_json()
    return jsonify({"sent": len(data.get('provider_ids', []))})

@app.route('/admin/download/<path:key>')
def admin_download(key):
    if session.get('rol') != 1: return redirect(url_for('login'))

    url = s3.generate_presigned_url("get_object",
        Params={'Bucket': S3_BUCKET, 'Key': key},
        ExpiresIn=300
    )
    return redirect(url)

@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE id=%s", (session['user_id'],))
    user = cur.fetchone()

    if request.method == 'POST':
        tipo_doc = request.form.get('tipo_documento')
        project_id = request.form.get('project_id')
        file = request.files.get('documento')

        if file:
            key = f"{datetime.now(timezone.utc).timestamp()}_{file.filename}"
            try:
                s3.upload_fileobj(file, S3_BUCKET, key)

                cur.execute("""SELECT ruta FROM documentos
                               WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s""",
                               (user['id'], project_id, tipo_doc))
                old = cur.fetchone()

                if old:
                    try:
                        s3.delete_object(Bucket=S3_BUCKET, Key=old['ruta'])
                    except: pass

                cur.execute("DELETE FROM documentos WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s",
                            (user['id'], project_id, tipo_doc))

                cur.execute("""
                    INSERT INTO documentos(usuario_id,nombre_archivo,ruta,fecha_subida,tipo_documento,project_id)
                    VALUES(%s,%s,%s,NOW(),%s,%s)
                """,(user['id'], file.filename, key, tipo_doc, project_id))

                conn.commit()
                flash("Documento actualizado correctamente")

            except NoCredentialsError:
                flash("Error en las credenciales AWS")

    cur.execute("SELECT * FROM projects WHERE provider_id=%s", (user['id'],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s", (user['id'],))
    docs = cur.fetchall()

    cur.close(); conn.close()

    docs_map = {}
    for d in docs:
        docs_map.setdefault(d['project_id'], {})[d['tipo_documento']] = d

    return render_template('dashboard_proveedor.html',
                           projects=projects,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           docs_map=docs_map)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
