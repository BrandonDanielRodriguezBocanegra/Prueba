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

MESES = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre")
]
MESES_MAP = {m: n for m, n in MESES}

# ========== LOGIN ==========
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
                # NUEVO: primero pasa por requerimientos
                return redirect(url_for('requerimientos_proveedor'))
        else:
            flash('Credenciales incorrectas')

    return render_template('login.html')

# ========== REGISTRO ==========
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

# ========== ADMIN DASHBOARD (sin cambios aquí, lo de admin lo dejamos como ya lo tienes) ==========
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

@app.route('/admin/send_reminder', methods=['POST'], endpoint='send_reminder')
def send_reminder():
    if 'usuario' not in session or session.get('rol') != 1:
        return jsonify({'success': False, 'message': 'Acceso denegado'}), 403

    data = request.get_json() or {}
    provider_ids = data.get('provider_ids', [])
    if not provider_ids:
        return jsonify({'success': False, 'message': 'No providers selected'}), 400

    return jsonify({'success': True, 'sent': len(provider_ids)})

# ===========================
# NUEVO: REQUERIMIENTOS PROVEEDOR
# ===========================
@app.route('/proveedor/requerimientos', methods=['GET', 'POST'])
def requerimientos_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    if request.method == 'POST':
        periodo_year = int(request.form.get('periodo_year') or 0)
        periodo_month = int(request.form.get('periodo_month') or 0)
        cantidad = int(request.form.get('cantidad') or 0)

        if periodo_year < 2000 or periodo_year > 2100:
            flash("Año inválido.")
            return redirect(url_for('requerimientos_proveedor'))
        if periodo_month < 1 or periodo_month > 12:
            flash("Mes inválido.")
            return redirect(url_for('requerimientos_proveedor'))
        if cantidad < 1 or cantidad > 50:
            flash("Cantidad inválida (1 a 50).")
            return redirect(url_for('requerimientos_proveedor'))

        pedidos = []
        for i in range(1, cantidad + 1):
            v = (request.form.get(f"pedido_{i}") or "").strip()
            if v:
                pedidos.append(v)

        if len(pedidos) != cantidad:
            flash("Debes llenar todos los No. de pedido.")
            return redirect(url_for('requerimientos_proveedor'))

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)

        cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
        user = cur.fetchone()

        # crear N proyectos (1 por pedido)
        for pedido_no in pedidos:
            # name = pedido_no (o puedes cambiarlo por otro campo si quieres)
            cur.execute("""
                INSERT INTO projects(provider_id, name, created_at, pedido_no, periodo_year, periodo_month)
                VALUES (%s, %s, NOW(), %s, %s, %s)
            """, (user['id'], pedido_no, pedido_no, periodo_year, periodo_month))

        conn.commit()
        cur.close()
        conn.close()

        flash("Pedidos registrados correctamente.")
        return redirect(url_for('dashboard_proveedor', year=periodo_year, month=f"{periodo_month:02d}"))

    # GET
    current_year = datetime.utcnow().year
    return render_template('requerimientos.html', meses=MESES, current_year=current_year)

# ========== PROVEEDOR DASHBOARD ==========
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'usuario' not in session or session.get('rol') != 2:
        flash('Acceso denegado')
        return redirect(url_for('login'))

    # filtros (GET)
    selected_year = (request.args.get('year') or '').strip()
    selected_month = (request.args.get('month') or '').strip()  # "01".."12"

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session['usuario'],))
    user = cur.fetchone()

    if request.method == 'POST':
        if request.form.get('action') == 'upload_doc':
            project_id = int(request.form.get('project_id'))
            tipo = request.form.get('tipo_documento')
            archivo = request.files.get('documento')

            if archivo and archivo.filename != '':
                ext = archivo.filename.rsplit('.', 1)[-1].lower()
                if ext not in ['pdf','jpg','jpeg','png']:
                    flash("Tipo de archivo no permitido.")
                else:
                    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{archivo.filename}"

                    s3.upload_fileobj(
                        archivo,
                        BUCKET_NAME,
                        filename,
                        ExtraArgs={'ACL':'private'}
                    )

                    cur.execute("""
                        INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,fecha_subida,project_id)
                        VALUES(%s,%s,%s,%s,NOW(),%s)
                    """, (user['id'], archivo.filename, filename, tipo, project_id))
                    conn.commit()
                    flash("Documento subido correctamente.")

    # traer proyectos (pedidos) con filtro
    query = "SELECT * FROM projects WHERE provider_id=%s"
    params = [user['id']]

    if selected_year:
        query += " AND periodo_year=%s"
        params.append(int(selected_year))
    if selected_month:
        query += " AND periodo_month=%s"
        params.append(int(selected_month))

    query += " ORDER BY periodo_year DESC, periodo_month DESC, created_at DESC"
    cur.execute(query, tuple(params))
    projects = cur.fetchall()

    # years para filtro
    cur.execute("SELECT DISTINCT periodo_year FROM projects WHERE provider_id=%s AND periodo_year IS NOT NULL ORDER BY periodo_year DESC", (user['id'],))
    years_available = [r['periodo_year'] for r in cur.fetchall() if r['periodo_year']]

    # docs
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
        for doc in DOCUMENTOS_OBLIGATORIOS:
            for d in docs_by_project.get(p['id'], []):
                if d['tipo_documento'] == doc:
                    documentos_subidos[p['id']][doc] = d

    # helper: mes en palabras
    def mes_nombre(m):
        return MESES_MAP.get(m, "")

    return render_template(
        'dashboard_proveedor.html',
        projects=projects,
        documentos_subidos=documentos_subidos,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        meses=MESES,
        years_available=years_available,
        selected_year=selected_year,
        selected_month=selected_month,
        mes_nombre=mes_nombre
    )

if __name__ == '__main__':
    app.run()
