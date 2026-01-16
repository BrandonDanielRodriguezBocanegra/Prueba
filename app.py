# app.py
import os
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
import psycopg.rows
import psycopg.errors
import boto3

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'supersecretkey')

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("Falta DATABASE_URL en variables de entorno")

def get_conn():
    # Render suele requerir SSL. Si tu DATABASE_URL ya trae sslmode, esto no estorba.
    # Si no lo trae, forzamos 'require' para reducir errores intermitentes.
    if "sslmode=" in DATABASE_URL:
        return psycopg.connect(DATABASE_URL)
    return psycopg.connect(DATABASE_URL + ("&" if "?" in DATABASE_URL else "?") + "sslmode=require")

# ---------- AWS CONFIG ----------
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-2')
BUCKET_NAME = os.environ.get('AWS_BUCKET_NAME', 'repse-documento')

s3 = boto3.client(
    's3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
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

MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

# ===================== HELPERS =====================

def get_presigned_url(key: str):
    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': key},
            ExpiresIn=300
        )
    except Exception as e:
        print("Presigned error:", e)
        return None

def delete_s3_object(key: str):
    try:
        s3.delete_object(Bucket=BUCKET_NAME, Key=key)
    except Exception as e:
        print("Error eliminando S3:", e)

# ===================== AUTH =====================

@app.route('/', methods=['GET', 'POST'])
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
            if user['estado'] == 'pendiente':
                flash('Tu cuenta está pendiente de aprobación.')
                return redirect(url_for('login'))

            session['usuario'] = user['usuario']
            session['rol'] = user['rol']
            session['user_id'] = user['id']

            if user['rol'] == 1:
                return redirect(url_for('dashboard_admin'))

            # Proveedor -> primero Requerimientos si aún no tiene pedidos
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM projects WHERE provider_id=%s", (user['id'],))
            count_projects = cur.fetchone()[0]
            cur.close()
            conn.close()

            if count_projects == 0:
                return redirect(url_for('requerimientos'))
            return redirect(url_for('dashboard_proveedor'))

        flash('Credenciales incorrectas')

    return render_template('login.html')

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
            cur.execute(
                """
                INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (nombre, usuario, correo, password_hash, rol, 'pendiente')
            )
            conn.commit()
            flash('Registro exitoso. Espera aprobación del administrador.')
            return redirect(url_for('login'))
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash('El usuario ya existe.')
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

# ===================== PROVEEDOR: REQUERIMIENTOS =====================

@app.route('/proveedor/requerimientos', methods=['GET', 'POST'])
def requerimientos():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    provider_id = session['user_id']

    if request.method == 'POST':
        mes = request.form.get('mes')
        fecha_str = request.form.get('fecha')
        cantidad = int(request.form.get('cantidad') or 0)

        pedidos = []
        for i in range(1, cantidad + 1):
            p = (request.form.get(f'pedido_{i}') or '').strip()
            if p:
                pedidos.append(p)

        if not mes or mes not in MESES_ES:
            flash("Selecciona un mes válido.")
            return redirect(url_for('requerimientos'))

        if not fecha_str:
            flash("Selecciona una fecha válida.")
            return redirect(url_for('requerimientos'))

        if cantidad <= 0 or len(pedidos) == 0:
            flash("Debes capturar al menos 1 pedido.")
            return redirect(url_for('requerimientos'))

        try:
            fecha_dt = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except Exception:
            flash("Fecha inválida.")
            return redirect(url_for('requerimientos'))

        conn = get_conn()
        cur = conn.cursor()

        # Guardar mes/fecha en usuarios (para referencia)
        cur.execute("UPDATE usuarios SET req_mes=%s, req_fecha=%s WHERE id=%s", (mes, fecha_dt, provider_id))

        # Crear projects por cada pedido (si no existe con mismo nombre)
        for pedido in pedidos:
            cur.execute(
                "SELECT id FROM projects WHERE provider_id=%s AND name=%s",
                (provider_id, pedido)
            )
            exists = cur.fetchone()
            if not exists:
                cur.execute(
                    "INSERT INTO projects(provider_id, name, created_at) VALUES(%s,%s,NOW())",
                    (provider_id, pedido)
                )

        conn.commit()
        cur.close()
        conn.close()

        flash("Requerimientos guardados. Ya puedes subir documentación.")
        return redirect(url_for('dashboard_proveedor'))

    return render_template('requerimientos.html', meses=MESES_ES)

# ===================== PROVEEDOR DASHBOARD =====================

@app.route('/proveedor/dashboard', methods=['GET', 'POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE id=%s", (session['user_id'],))
    user = cur.fetchone()

    if not user or user['estado'] != 'aprobado':
        cur.close()
        conn.close()
        flash('Tu cuenta aún no ha sido aprobada.')
        return redirect(url_for('login'))

    if request.method == 'POST':
        action = request.form.get('action')

        # Crear pedido manual (opcional, si lo sigues usando)
        if action == 'create_project':
            name = (request.form.get('project_name') or '').strip()
            if not name:
                flash("Nombre inválido.")
            else:
                cur.execute(
                    "INSERT INTO projects(provider_id, name, created_at) VALUES(%s,%s,NOW())",
                    (user['id'], name)
                )
                conn.commit()
                flash("Pedido creado.")
            return redirect(url_for('dashboard_proveedor'))

        # Subir / actualizar documento
        if action == 'upload_doc':
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')

            if not archivo or archivo.filename == '':
                flash("Selecciona un archivo.")
                return redirect(url_for('dashboard_proveedor'))

            ext = archivo.filename.rsplit('.', 1)[-1].lower()
            if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
                flash("Tipo de archivo no permitido.")
                return redirect(url_for('dashboard_proveedor'))

            # Si ya existe doc para ese tipo+proyecto, lo reemplazamos (S3 + DB)
            cur.execute("""
                SELECT id, ruta
                FROM documentos
                WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s
                ORDER BY fecha_subida DESC
                LIMIT 1
            """, (user['id'], project_id, tipo))
            existing = cur.fetchone()

            new_key = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"

            # Subir a S3
            try:
                s3.upload_fileobj(
                    archivo,
                    BUCKET_NAME,
                    new_key,
                    ExtraArgs={'ACL': 'private'}
                )
            except Exception as e:
                print("S3 upload error:", e)
                flash("Error subiendo a S3.")
                return redirect(url_for('dashboard_proveedor'))

            if existing:
                # borrar el anterior en S3
                if existing.get('ruta'):
                    delete_s3_object(existing['ruta'])
                # actualizar registro
                cur.execute("""
                    UPDATE documentos
                    SET nombre_archivo=%s, ruta=%s, fecha_subida=NOW()
                    WHERE id=%s
                """, (archivo.filename, new_key, existing['id']))
            else:
                cur.execute("""
                    INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,fecha_subida,project_id)
                    VALUES(%s,%s,%s,%s,NOW(),%s)
                """, (user['id'], archivo.filename, new_key, tipo, project_id))

            conn.commit()
            flash("Documento guardado/actualizado correctamente.")
            return redirect(url_for('dashboard_proveedor'))

    # Cargar pedidos (projects)
    cur.execute("SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC", (user['id'],))
    projects = cur.fetchall()

    # Docs del proveedor
    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (user['id'],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    # indexar docs por project_id y tipo
    docs_by_project = {}
    for d in docs:
        pid = d['project_id']
        docs_by_project.setdefault(pid, []).append(d)

    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p['id']] = {}
        for doc in DOCUMENTOS_OBLIGATORIOS:
            # buscar el doc (si existe)
            found = None
            for d in docs_by_project.get(p['id'], []):
                if d['tipo_documento'] == doc:
                    found = d
                    break
            if found:
                documentos_subidos[p['id']][doc] = found

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        documentos_subidos=documentos_subidos,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS
    )

# ===================== ADMIN DASHBOARD =====================

@app.route('/admin/dashboard')
def dashboard_admin():
    if 'usuario' not in session or session.get('rol') != 1:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2 ORDER BY nombre ASC")
    proveedores = cur.fetchall()

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    proyectos = cur.fetchall()

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
        proveedores=proveedores,
        proyectos=proyectos,
        documentos_por_usuario=docs_by_user,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url
    )

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

# ADMIN: borrar usuario
@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'msg': 'Acceso denegado'})

    data = request.get_json() or {}
    user_id = data.get('id')

    if user_id == session.get('user_id'):
        return jsonify({'success': False, 'msg': 'No puedes borrar tu propia cuenta'})

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # borrar S3 docs del usuario
    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()
    for d in docs:
        if d.get('ruta'):
            delete_s3_object(d['ruta'])

    # borrar BD
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'msg': 'Usuario eliminado correctamente'})

# ADMIN: borrar pedido (project)
@app.route('/admin/delete_pedido', methods=['POST'])
def delete_pedido():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'msg': 'Acceso denegado'})

    data = request.get_json() or {}
    project_id = data.get('project_id')
    provider_id = data.get('provider_id')

    if not project_id or not provider_id:
        return jsonify({'success': False, 'msg': 'Datos incompletos'})

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # confirmar pertenece a ese proveedor
    cur.execute("SELECT id FROM projects WHERE id=%s AND provider_id=%s", (project_id, provider_id))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return jsonify({'success': False, 'msg': 'Pedido no encontrado'})

    # borrar docs de ese pedido en S3
    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s AND project_id=%s", (provider_id, project_id))
    docs = cur.fetchall()
    for d in docs:
        if d.get('ruta'):
            delete_s3_object(d['ruta'])

    # borrar docs y pedido
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s AND project_id=%s", (provider_id, project_id))
    cur.execute("DELETE FROM projects WHERE id=%s AND provider_id=%s", (project_id, provider_id))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'msg': 'Pedido eliminado correctamente'})

# Reminder (queda como stub si no estás enviando SMTP real)
@app.route('/admin/send_reminder', methods=['POST'], endpoint='send_reminder')
def send_reminder():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'message': 'Acceso denegado'}), 403

    data = request.get_json() or {}
    provider_ids = data.get('provider_ids', [])
    if not provider_ids:
        return jsonify({'success': False, 'message': 'No providers selected'}), 400

    # Aquí iría tu SMTP outlook (cuando lo actives)
    sent = len(provider_ids)
    return jsonify({'success': True, 'sent': sent})

# ===================== RUN =====================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true')
    app.run(host='0.0.0.0', port=port, debug=debug_mode)
