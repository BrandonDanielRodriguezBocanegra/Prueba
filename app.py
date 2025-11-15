# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = 'secretkey123'

DB_URL = "postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db"

DOCUMENTOS_OBLIGATORIOS = ['INE', 'Comprobante Domicilio', 'RFC']

# ---------- RUTAS DE AUTENTICACIÓN ----------
@app.route('/', methods=['GET','POST'])
def login():
    if request.method=='POST':
        usuario = request.form['usuario']
        contrasena = request.form['contrasena']
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
                user = cur.fetchone()
                if user and check_password_hash(user[4], contrasena):
                    session['id'] = user[0]
                    session['usuario'] = user[2]
                    session['rol'] = user[5]
                    if user[5]==1:
                        return redirect(url_for('dashboard_admin'))
                    else:
                        return redirect(url_for('dashboard_proveedor'))
                else:
                    flash("Usuario o contraseña incorrectos")
    return render_template('login.html')

@app.route('/registro', methods=['GET','POST'])
def registro():
    if request.method=='POST':
        nombre = request.form['nombre']
        usuario = request.form['usuario']
        correo = request.form['correo']
        contrasena = generate_password_hash(request.form['contrasena'])
        rol = int(request.form['rol'])
        with psycopg.connect(DB_URL) as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute(
                        "INSERT INTO usuarios(nombre,usuario,correo,password,rol,estado) VALUES(%s,%s,%s,%s,%s,%s)",
                        (nombre, usuario, correo, contrasena, rol, 'pendiente')
                    )
                    flash("Registro exitoso")
                    return redirect(url_for('login'))
                except Exception as e:
                    flash("Error: "+str(e))
    return render_template('registro.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---------- DASHBOARD ADMIN ----------
@app.route('/admin/dashboard', methods=['GET','POST'])
def dashboard_admin():
    if 'rol' not in session or session['rol']!=1:
        return redirect(url_for('login'))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            # Usuarios pendientes
            cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
            pendientes = cur.fetchall()
            # Usuarios aprobados
            cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
            proveedores = cur.fetchall()
            # Proyectos
            cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
            projects = cur.fetchall()
    
    return render_template('dashboard_admin.html',
                           pendientes=pendientes,
                           proveedores=proveedores,
                           projects=projects,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)

@app.route('/admin/aprobar/<int:id>')
def aprobar_usuario(id):
    if 'rol' not in session or session['rol']!=1:
        return redirect(url_for('login'))
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE usuarios SET estado='aprobado' WHERE id=%s", (id,))
    return redirect(url_for('dashboard_admin'))

@app.route('/admin/rechazar/<int:id>')
def rechazar_usuario(id):
    if 'rol' not in session or session['rol']!=1:
        return redirect(url_for('login'))
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    return redirect(url_for('dashboard_admin'))

@app.route('/admin/enviar_recordatorio', methods=['POST'])
def enviar_recordatorio():
    if 'rol' not in session or session['rol']!=1:
        return redirect(url_for('login'))
    destinatarios = request.form.getlist('destinatarios')  # lista de correos
    asunto = request.form['asunto']
    cuerpo = request.form['cuerpo']

    # Obtener correo y password del admin logueado
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT correo, mail_password FROM usuarios WHERE id=%s", (session['id'],))
            data = cur.fetchone()
            correo_admin, pass_admin = data[0], data[1]

    if not correo_admin or not pass_admin:
        flash("Admin no tiene credenciales configuradas")
        return redirect(url_for('dashboard_admin'))

    # Enviar correo
    try:
        msg = EmailMessage()
        msg['Subject'] = asunto
        msg['From'] = correo_admin
        msg['To'] = ', '.join(destinatarios)
        msg.set_content(cuerpo)

        # SMTP Outlook
        with smtplib.SMTP('smtp.office365.com', 587) as smtp:
            smtp.starttls()
            smtp.login(correo_admin, pass_admin)
            smtp.send_message(msg)
        flash("Recordatorio enviado")
    except Exception as e:
        flash("Error al enviar correo: "+str(e))

    return redirect(url_for('dashboard_admin'))

# ---------- DASHBOARD PROVEEDOR ----------
@app.route('/proveedor/dashboard', methods=['GET','POST'])
def dashboard_proveedor():
    if 'rol' not in session or session['rol']!=2:
        return redirect(url_for('login'))

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            # Obtener proyectos del proveedor
            cur.execute("SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC", (session['id'],))
            projects = cur.fetchall()
            # Documentos por proyecto
            documentos = {}
            for p in projects:
                cur.execute("SELECT tipo_documento, nombre_archivo, ruta FROM documentos WHERE project_id=%s", (p[0],))
                docs = cur.fetchall()
                documentos[p[0]] = {d[0]: {'nombre_archivo': d[1], 'ruta': d[2]} for d in docs}

    return render_template('dashboard_proveedor.html',
                           projects=projects,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
                           documentos=documentos)

# ---------- SUBIR DOCUMENTO ----------
@app.route('/proveedor/subir/<int:project_id>', methods=['POST'])
def subir_documento(project_id):
    if 'rol' not in session or session['rol']!=2:
        return redirect(url_for('login'))

    archivo = request.files['documento']
    tipo = request.form['tipo_documento']
    ruta = f"uploads/{archivo.filename}"
    archivo.save(ruta)

    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,project_id) VALUES(%s,%s,%s,%s,%s)",
                (session['id'], archivo.filename, ruta, tipo, project_id)
            )
    flash("Documento subido")
    return redirect(url_for('dashboard_proveedor'))

# ---------- CREAR PROYECTO ----------
@app.route('/proveedor/crear_proyecto', methods=['POST'])
def crear_proyecto():
    if 'rol' not in session or session['rol']!=2:
        return redirect(url_for('login'))
    nombre = request.form['nombre_proyecto']
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO projects(provider_id,name) VALUES(%s,%s)", (session['id'], nombre))
    flash("Proyecto creado")
    return redirect(url_for('dashboard_proveedor'))

if __name__=="__main__":
    app.run(debug=True)
