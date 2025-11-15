from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
from datetime import datetime

app = Flask(__name__)
app.secret_key = "tu_secreto_aqui"

# PostgreSQL database URL
DATABASE_URL = "postgresql://repse_db_user:DdWJ7DrHXlVnC96eAxxnqNgbjTgFGS0f@dpg-d4c15c6r433s73d7o3dg-a.oregon-postgres.render.com/repse_db"

# Documentos obligatorios
DOCUMENTOS_OBLIGATORIOS = ["Documento 1", "Documento 2", "Documento 3"]

# --- Función para conectar a la base de datos ---
def get_db_connection():
    return psycopg.connect(DATABASE_URL)

# =========================
# LOGIN
# =========================
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form["usuario"]
        contrasena = request.form["contrasena"]
        with get_db_connection() as conn:
            with conn.cursor() as c:
                c.execute("SELECT id, password, rol, estado FROM usuarios WHERE usuario=%s", (usuario,))
                user = c.fetchone()
                if user and check_password_hash(user[1], contrasena):
                    session["usuario_id"] = user[0]
                    session["usuario"] = usuario
                    session["rol"] = user[2]
                    if user[2] == 1:
                        return redirect(url_for("dashboard_admin"))
                    else:
                        return redirect(url_for("dashboard_proveedor"))
                else:
                    flash("Usuario o contraseña incorrectos")
    return render_template("login.html")

# =========================
# REGISTRO
# =========================
@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form["nombre"]
        usuario = request.form["usuario"]
        correo = request.form["correo"]
        contrasena = generate_password_hash(request.form["contrasena"])
        rol = int(request.form["rol"])
        estado = "pendiente" if rol == 2 else "aprobado"
        with get_db_connection() as conn:
            with conn.cursor() as c:
                try:
                    c.execute("""
                        INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, (nombre, usuario, correo, contrasena, rol, estado))
                    flash("Registro exitoso")
                    return redirect(url_for("login"))
                except psycopg.errors.UniqueViolation:
                    flash("Usuario ya existe")
    return render_template("registro.html")

# =========================
# LOGOUT
# =========================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =========================
# DASHBOARD ADMIN
# =========================
@app.route("/admin/dashboard")
def dashboard_admin():
    if session.get("rol") != 1:
        return redirect(url_for("login"))

    with get_db_connection() as conn:
        with conn.cursor() as c:
            # Usuarios pendientes
            c.execute("SELECT id, nombre, usuario, correo, rol FROM usuarios WHERE estado='pendiente'")
            pendientes = [{"id": row[0], "nombre": row[1], "usuario": row[2], "correo": row[3], "rol": row[4]} for row in c.fetchall()]

            # Proveedores aprobados
            c.execute("SELECT id, nombre, usuario FROM usuarios WHERE rol=2 AND estado='aprobado'")
            aprobados = [{"id": row[0], "nombre": row[1], "usuario": row[2]} for row in c.fetchall()]

            # Documentos por usuario
            documentos = {}
            for p in aprobados:
                c.execute("SELECT tipo_documento, nombre_archivo, ruta FROM documentos WHERE usuario_id=%s", (p["id"],))
                docs = {row[0]: {"nombre_archivo": row[1], "ruta": row[2]} for row in c.fetchall()}
                documentos[p["id"]] = docs

            # Proyectos
            c.execute("SELECT id, provider_id, name, created_at, completed FROM projects ORDER BY created_at DESC")
            projects = [{"id": row[0], "provider_id": row[1], "name": row[2], "created_at": row[3], "completed": row[4]} for row in c.fetchall()]

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,
        aprobados=aprobados,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos=documentos,
        projects=projects
    )

# =========================
# DASHBOARD PROVEEDOR
# =========================
@app.route("/proveedor/dashboard")
def dashboard_proveedor():
    if session.get("rol") != 2:
        return redirect(url_for("login"))

    user_id = session["usuario_id"]
    with get_db_connection() as conn:
        with conn.cursor() as c:
            # Proyectos del proveedor
            c.execute("SELECT id, name, created_at FROM projects WHERE provider_id=%s ORDER BY created_at DESC", (user_id,))
            projects = [{"id": row[0], "name": row[1], "created_at": row[2]} for row in c.fetchall()]

            # Documentos subidos por proyecto
            documentos_subidos = {}
            for p in projects:
                c.execute("SELECT tipo_documento, nombre_archivo, ruta FROM documentos WHERE project_id=%s", (p["id"],))
                docs = {row[0]: {"nombre_archivo": row[1], "ruta": row[2]} for row in c.fetchall()}
                documentos_subidos[p["id"]] = docs

    return render_template(
        "dashboard_proveedor.html",
        projects=projects,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        documentos_subidos=documentos_subidos
    )

# =========================
# EJECUTAR APP
# =========================
if __name__ == "__main__":
    app.run(debug=True)
