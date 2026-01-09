# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import psycopg.errors
import boto3

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_conn():
    return psycopg.connect(DATABASE_URL)


# ---------- AWS CONFIG ----------
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-2')
BUCKET_NAME = os.environ.get('AWS_BUCKET_NAME')

s3 = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
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


# ==========================================
# LOGIN
# ==========================================
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
        flash('Credenciales incorrectas')

    return render_template('login.html')


# ==========================================
# REGISTRO
# ==========================================
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
            flash('Registro exitoso. Espera aprobación.')
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


# ==========================================
# ADMIN DASHBOARD
# ==========================================
def get_presigned_url(filename):
    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': filename},
            ExpiresIn=300
        )
    except:
        return None


@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
    proveedores = cur.fetchall()

    # FILTRO OPCIONAL
    filtro = request.args.get('proveedor_id')
    if filtro and filtro.isdigit():
        proveedores_filtrados = [p for p in proveedores if p['id'] == int(filtro)]
    else:
        proveedores_filtrados = proveedores

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos ORDER BY fecha_subida DESC")
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_user = {}
    for d in docs:
        docs_by_user.setdefault(d['usuario_id'], {}).setdefault(d['project_id'], []).append(d)

    return render_template(
        'dashboard_admin.html',
        pendientes=pendientes,
        proveedores=proveedores_filtrados,
        proveedores_original=proveedores,
        proyectos=projects,
        documentos_por_usuario=docs_by_user,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url
    )


# ==========================================
# APROBAR / RECHAZAR USUARIO
# ==========================================
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

    flash('Operación realizada')
    return redirect(url_for('dashboard_admin'))


# ==========================================
# DELETE USER
# ==========================================
@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'msg': 'Acceso denegado'})

    data = request.get_json()
    user_id = data.get('id')

    if user_id == session['user_id']:
        return jsonify({'success': False, 'msg': 'No puedes borrar tu propia cuenta'})

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()

    # BORRAR DOCUMENTOS EN S3
    for d in docs:
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=d['ruta'])
        except:
            pass

    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'msg': 'Usuario eliminado correctamente'})


# ==========================================
# SEND REMINDER
# ==========================================
@app.route('/admin/send_reminder', methods=['POST'])
def send_reminder():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False})

    data = request.get_json()
    provider_ids = data.get('provider_ids', [])
    subject = data.get('subject', 'Recordatorio REPSE')
    message = data.get('message', '')

    if not provider_ids:
        return jsonify({'success': False, 'message': 'No providers selected'})

    # Simulación de envío
    return jsonify({'success': True, 'sent': len(provider_ids)})


# ==========================================
# PROVEEDOR DASHBOARD
# ==========================================
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
            cur.execute("INSERT INTO projects(provider_id,name,created_at) VALUES(%s,%s,NOW())",
                        (user['id'], request.form.get('project_name')))
            conn.commit()
            flash("Proyecto creado.")
            return redirect(url_for('dashboard_proveedor'))

        if request.form.get('action') == 'upload_doc':
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')

            if archivo and archivo.filename != '':
                filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{archivo.filename}"
                
                cur.execute("SELECT * FROM documentos WHERE usuario_id=%s AND tipo_documento=%s AND project_id=%s",
                            (user['id'], tipo, project_id))
                existente = cur.fetchone()

                if existente:
                    try:
                        s3.delete_object(Bucket=BUCKET_NAME, Key=existente['ruta'])
                    except: pass

                    cur.execute("DELETE FROM documentos WHERE id=%s", (existente['id'],))

                s3.upload_fileobj(archivo, BUCKET_NAME, filename,
                                  ExtraArgs={'ACL': 'private'})

                cur.execute("""
                INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,fecha_subida,project_id)
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
    for d in docs:
        docs_by_project.setdefault(d['project_id'], []).append(d)

    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p['id']] = {}
        for d in docs_by_project.get(p['id'], []):
            documentos_subidos[p['id']][d['tipo_documento']] = d

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        documentos_subidos=documentos_subidos,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS
    )


if __name__ == '__main__':
    app.run()
