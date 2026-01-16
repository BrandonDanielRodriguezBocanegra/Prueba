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

# ========== HELPERS ==========
def get_presigned_url(filename):
    try:
        return s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': filename},
            ExpiresIn=300
        )
    except Exception:
        return None

def is_admin():
    return 'usuario' in session and session.get('rol') == 1

def is_provider():
    return 'usuario' in session and session.get('rol') == 2

# ========== LOGIN ==========
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

            # ✅ PROVEEDOR: antes de dashboard, pasa por requerimientos
            return redirect(url_for('requerimientos_proveedor'))

        flash('Credenciales incorrectas')

    return render_template('login.html')

# ========== REGISTRO ==========
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        usuario = request.form.get('usuario')
        correo = request.form.get('correo')
        contrasena = request.form.get('contrasena')
        rol = int(request.form.get('rol') or 2)

        password_hash = generate_password_hash(contrasena)

        # Campos REPSE/Contacto (solo proveedor, pero si vienen vacíos no pasa nada)
        repse_numero = request.form.get('repse_numero')
        repse_folio = request.form.get('repse_folio')
        repse_aviso = request.form.get('repse_aviso')
        repse_fecha_aviso = request.form.get('repse_fecha_aviso')
        repse_vigencia = request.form.get('repse_vigencia')
        repse_rfc = request.form.get('repse_rfc')
        repse_regimen = request.form.get('repse_regimen')
        repse_objeto = request.form.get('repse_objeto')

        contacto_nombre = request.form.get('contacto_nombre')
        contacto_tel = request.form.get('contacto_tel')
        contacto_correo = request.form.get('contacto_correo')

        conn = get_conn()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO usuarios(
                    nombre, usuario, correo, password, rol, estado,
                    repse_numero, repse_folio, repse_aviso, repse_fecha_aviso, repse_vigencia,
                    repse_rfc, repse_regimen, repse_objeto,
                    contacto_nombre, contacto_tel, contacto_correo
                )
                VALUES(%s,%s,%s,%s,%s,%s,
                       %s,%s,%s,%s,%s,
                       %s,%s,%s,
                       %s,%s,%s)
            """, (
                nombre, usuario, correo, password_hash, rol, 'pendiente',
                repse_numero, repse_folio, repse_aviso, repse_fecha_aviso, repse_vigencia,
                repse_rfc, repse_regimen, repse_objeto,
                contacto_nombre, contacto_tel, contacto_correo
            ))
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

# ==========================================================
# ✅ REQUERIMIENTOS PROVEEDOR (pantalla antes del dashboard)
# ==========================================================
@app.route('/proveedor/requerimientos', methods=['GET', 'POST'])
def requerimientos_proveedor():
    if not is_provider():
        flash('Acceso denegado')
        return redirect(url_for('login'))

    user_id = session['user_id']
    now = datetime.utcnow()
    default_month = now.month
    default_year = now.year

    if request.method == 'POST':
        month = int(request.form.get('month') or default_month)
        year = int(request.form.get('year') or default_year)
        total = int(request.form.get('total') or 0)

        pedidos = []
        for i in range(1, total + 1):
            val = (request.form.get(f'pedido_{i}') or '').strip()
            if val:
                pedidos.append(val)

        if total <= 0:
            flash('Indica cuántos pedidos tienes.')
            return redirect(url_for('requerimientos_proveedor'))

        if len(pedidos) != total:
            flash('Debes llenar todos los números de pedido.')
            return redirect(url_for('requerimientos_proveedor'))

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)

        # Crear proyectos por cada pedido (si no existen)
        created = 0
        for pedido in pedidos:
            try:
                cur.execute("""
                    INSERT INTO projects(provider_id, name, created_at, completed, month, year, pedido_num)
                    VALUES(%s,%s,NOW(),0,%s,%s,%s)
                    ON CONFLICT (provider_id, month, year, pedido_num) DO NOTHING
                """, (user_id, f"Pedido {pedido}", month, year, pedido))
                created += cur.rowcount
            except Exception:
                # si algo falla en uno, se verá abajo; hacemos rollback global si truena
                raise

        conn.commit()

        # Guardar selección actual en sesión (para filtrar en dashboard)
        session['active_month'] = month
        session['active_year'] = year

        flash(f'Requerimientos guardados. Pedidos agregados: {created}')
        cur.close()
        conn.close()

        return redirect(url_for('dashboard_proveedor'))

    # GET: Mostrar si ya existen pedidos del mes/año actual (opcional)
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("""
        SELECT month, year, COUNT(*) AS total
        FROM projects
        WHERE provider_id=%s AND pedido_num IS NOT NULL
        GROUP BY month, year
        ORDER BY year DESC, month DESC
        LIMIT 12
    """, (user_id,))
    resumen = cur.fetchall()
    cur.close()
    conn.close()

    return render_template(
        'requerimientos.html',
        default_month=default_month,
        default_year=default_year,
        resumen=resumen
    )

# ========== ADMIN DASHBOARD ==========
@app.route('/admin/dashboard')
def dashboard_admin():
    if not is_admin():
        flash('Acceso denegado')
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

    return render_template(
        'dashboard_admin.html',
        pendientes=pendientes,
        proveedores=proveedores,
        proyectos=projects,
        documentos_por_usuario=docs_by_user,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url
    )

# APROBAR / RECHAZAR
@app.route('/admin/accion/<int:id>/<accion>')
def accion(id, accion):
    if not is_admin():
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

# DELETE USER (con borrado en S3)
@app.route('/admin/delete_user', methods=['POST'])
def delete_user():
    if not is_admin():
        return jsonify({'success': False, 'msg': 'Acceso denegado'})

    data = request.get_json() or {}
    user_id = data.get('id')

    if not user_id:
        return jsonify({'success': False, 'msg': 'Falta id'})

    if int(user_id) == int(session['user_id']):
        return jsonify({'success': False, 'msg': 'No puedes borrar tu propia cuenta'})

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # Obtener documentos del usuario para borrarlos del bucket
    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()

    for d in docs:
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=d['ruta'])
        except Exception as e:
            print("Error eliminando archivo S3:", e)

    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({'success': True, 'msg': 'Usuario eliminado correctamente'})

# ---------- AJAX Reminder ----------
@app.route('/admin/send_reminder', methods=['POST'], endpoint='send_reminder')
def send_reminder():
    if not is_admin():
        return jsonify({'success': False, 'message': 'Acceso denegado'}), 403

    data = request.get_json() or {}
    provider_ids = data.get('provider_ids', [])
    subject = data.get('subject', 'Recordatorio REPSE')
    message = data.get('message', '')

    if not provider_ids:
        return jsonify({'success': False, 'message': 'No providers selected'}), 400

    # Aquí tú pondrás luego tu lógica real de SMTP/Outlook.
    sent = len(provider_ids)
    return jsonify({'success': True, 'sent': sent})

# ========== PROVEEDOR DASHBOARD ==========
@app.route('/proveedor/dashboard', methods=['GET', 'POST'])
def dashboard_proveedor():
    if not is_provider():
        flash('Acceso denegado')
        return redirect(url_for('login'))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
    user = cur.fetchone()

    if not user or user.get('estado') != 'aprobado':
        cur.close()
        conn.close()
        flash('Tu cuenta aún no ha sido aprobada.')
        return redirect(url_for('login'))

    # Si no eligió mes/año (no pasó por requerimientos), lo mandamos
    if 'active_month' not in session or 'active_year' not in session:
        cur.close()
        conn.close()
        return redirect(url_for('requerimientos_proveedor'))

    active_month = int(session['active_month'])
    active_year = int(session['active_year'])

    # SUBIR/ACTUALIZAR DOCUMENTO
    if request.method == 'POST':
        if request.form.get('action') in ('upload_doc', 'update_doc'):
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')

            if not archivo or archivo.filename == '':
                flash('Selecciona un archivo.')
            else:
                ext = archivo.filename.rsplit('.', 1)[-1].lower()
                if ext not in ['pdf', 'jpg', 'jpeg', 'png']:
                    flash('Tipo de archivo no permitido.')
                else:
                    # Verificamos que el project pertenezca al proveedor
                    cur.execute("SELECT * FROM projects WHERE id=%s AND provider_id=%s", (project_id, user['id']))
                    pr = cur.fetchone()
                    if not pr:
                        flash('Proyecto inválido.')
                    else:
                        filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"

                        # Si es update, borrar anterior (S3 y BD) de ese tipo/doc en ese proyecto
                        if request.form.get('action') == 'update_doc':
                            cur.execute("""
                                SELECT id, ruta FROM documentos
                                WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s
                                ORDER BY fecha_subida DESC
                                LIMIT 1
                            """, (user['id'], project_id, tipo))
                            old = cur.fetchone()
                            if old:
                                try:
                                    s3.delete_object(Bucket=BUCKET_NAME, Key=old['ruta'])
                                except Exception as e:
                                    print("Error borrando viejo en S3:", e)
                                cur.execute("DELETE FROM documentos WHERE id=%s", (old['id'],))

                        # Subir a S3
                        s3.upload_fileobj(
                            archivo,
                            BUCKET_NAME,
                            filename,
                            ExtraArgs={'ACL': 'private'}
                        )

                        # Insert BD
                        cur.execute("""
                            INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id)
                            VALUES(%s,%s,%s,%s,NOW(),%s)
                        """, (user['id'], archivo.filename, filename, tipo, project_id))
                        conn.commit()
                        flash('Documento guardado correctamente.')

    # Solo proyectos del mes/año activo (los pedidos)
    cur.execute("""
        SELECT * FROM projects
        WHERE provider_id=%s AND month=%s AND year=%s
        ORDER BY created_at DESC
    """, (user['id'], active_month, active_year))
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
        for doc in DOCUMENTOS_OBLIGATORIOS:
            for d in docs_by_project.get(p['id'], []):
                if d['tipo_documento'] == doc:
                    documentos_subidos[p['id']][doc] = d

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        docs_by_project=docs_by_project,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_subidos=documentos_subidos,
        active_month=active_month,
        active_year=active_year
    )

if __name__ == '__main__':
    app.run()
