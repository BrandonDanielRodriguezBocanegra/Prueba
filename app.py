# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3, os, datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "supersecret"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DOCUMENTOS_OBLIGATORIOS = ['RFC', 'Acta Constitutiva', 'Comprobante Domicilio']

# ----------------- LOGIN -----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"]
        contrasena = request.form["contrasena"]
        conn = sqlite3.connect('repse_system.db')
        conn.row_factory = sqlite3.Row
        user = conn.execute("SELECT * FROM usuarios WHERE usuario=?", (usuario,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], contrasena):
            session["usuario"] = user["usuario"]
            session["rol"] = user["rol"]
            session["id"] = user["id"]
            if user["rol"] == 1:
                return redirect(url_for("dashboard_admin"))
            else:
                return redirect(url_for("dashboard_proveedor"))
        else:
            flash("Usuario o contraseña incorrectos")
    return render_template("login.html")

# ----------------- LOGOUT -----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ----------------- REGISTRO -----------------
@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form["nombre"]
        usuario = request.form["usuario"]
        correo = request.form["correo"]
        contrasena = generate_password_hash(request.form["contrasena"])
        rol = int(request.form["rol"])
        estado = "pendiente"
        conn = sqlite3.connect('repse_system.db')
        try:
            conn.execute(
                "INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(?,?,?,?,?,?)",
                (nombre, usuario, correo, contrasena, rol, estado)
            )
            conn.commit()
            flash("Usuario registrado correctamente")
        except sqlite3.IntegrityError:
            flash("El usuario ya existe")
        conn.close()
        return redirect(url_for("login"))
    return render_template("registro.html")

# ----------------- DASHBOARD ADMIN -----------------
@app.route("/admin/dashboard")
def dashboard_admin():
    if "usuario" not in session or session["rol"] != 1:
        return redirect(url_for("login"))

    conn = sqlite3.connect('repse_system.db')
    conn.row_factory = sqlite3.Row

    # Usuarios pendientes
    pendientes = conn.execute("SELECT * FROM usuarios WHERE estado='pendiente'").fetchall()

    # Usuarios aprobados
    aprobados = conn.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2").fetchall()

    # Proyectos por proveedor
    projects = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()

    # Documentos por usuario
    documentos_por_usuario = {}
    for user in aprobados:
        docs = conn.execute("SELECT * FROM documentos WHERE usuario_id=?", (user["id"],)).fetchall()
        documentos_por_usuario[user["id"]] = {d["tipo_documento"]: dict(d) for d in docs}

    conn.close()
    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,
        aprobados=aprobados,
        projects=projects,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_por_usuario=documentos_por_usuario
    )

# ----------------- ACCIONES ADMIN -----------------
@app.route("/admin/accion/<int:id>/<accion>")
def accion(id, accion):
    if "usuario" not in session or session["rol"] != 1:
        return redirect(url_for("login"))
    conn = sqlite3.connect('repse_system.db')
    if accion == "aprobar":
        conn.execute("UPDATE usuarios SET estado='aprobado' WHERE id=?", (id,))
    elif accion == "rechazar":
        conn.execute("DELETE FROM usuarios WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("dashboard_admin"))

# ----------------- DASHBOARD PROVEEDOR -----------------
@app.route("/proveedor/dashboard", methods=["GET"])
def dashboard_proveedor():
    if "usuario" not in session or session["rol"] != 2:
        return redirect(url_for("login"))
    user_id = session["id"]
    conn = sqlite3.connect('repse_system.db')
    conn.row_factory = sqlite3.Row

    # Listar proyectos del proveedor
    projects = conn.execute("SELECT * FROM projects WHERE provider_id=? ORDER BY created_at DESC", (user_id,)).fetchall()

    # Documentos subidos
    documentos_subidos = {}
    for project in projects:
        docs = conn.execute("SELECT * FROM documentos WHERE project_id=?", (project["id"],)).fetchall()
        documentos_subidos[project["id"]] = {d["tipo_documento"]: dict(d) for d in docs}

    conn.close()
    return render_template("dashboard_proveedor.html", projects=projects, DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS, documentos_subidos=documentos_subidos)

# ----------------- CREAR PROYECTO -----------------
@app.route("/proveedor/crear_proyecto", methods=["POST"])
def crear_proyecto():
    if "usuario" not in session or session["rol"] != 2:
        return redirect(url_for("login"))
    name = request.form["nombre_proyecto"]
    user_id = session["id"]
    fecha = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect('repse_system.db')
    conn.execute("INSERT INTO projects(provider_id, name, created_at) VALUES(?,?,?)", (user_id, name, fecha))
    conn.commit()
    conn.close()
    flash("Proyecto creado correctamente")
    return redirect(url_for("dashboard_proveedor"))

# ----------------- SUBIR DOCUMENTO -----------------
@app.route("/proveedor/upload_document/<int:project_id>", methods=["POST"])
def upload_document(project_id):
    if "usuario" not in session or session["rol"] != 2:
        return redirect(url_for("login"))
    tipo = request.form["tipo_documento"]
    file = request.files["documento"]
    if file:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        fecha = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect('repse_system.db')
        conn.execute(
            "INSERT INTO documentos(usuario_id, project_id, nombre_archivo, ruta, tipo_documento, fecha_subida) VALUES(?,?,?,?,?,?)",
            (session["id"], project_id, filename, filepath, tipo, fecha)
        )
        conn.commit()
        conn.close()
        flash(f"Documento {tipo} subido correctamente")
    return redirect(url_for("dashboard_proveedor"))

if __name__ == "__main__":
    app.run(debug=True)
