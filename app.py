import os
from datetime import datetime
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg
import psycopg.rows
import psycopg.errors

import boto3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

# ---------------- DB ----------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def _ensure_sslmode(url: str) -> str:
    """Render a veces requiere sslmode=require; si ya viene, no lo toca."""
    if not url:
        return url
    try:
        u = urlparse(url)
        q = dict(parse_qsl(u.query))
        if "sslmode" not in q:
            q["sslmode"] = "require"
        new_u = u._replace(query=urlencode(q))
        return urlunparse(new_u)
    except Exception:
        return url

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada en Render.")
    return psycopg.connect(_ensure_sslmode(DATABASE_URL))

# ---------------- AWS S3 ----------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME", "repse-documento")

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    region_name=AWS_REGION
)

def get_presigned_url(filename: str):
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": filename},
            ExpiresIn=300
        )
    except Exception as e:
        print("get_presigned_url error:", e)
        return None

# ---------------- Constantes ----------------
DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]

MONTHS = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

# ---------------- Auth ----------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        contrasena = request.form.get("contrasena", "")

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password"], contrasena):
            if user.get("estado") == "pendiente":
                flash("Tu cuenta está pendiente de aprobación.")
                return redirect(url_for("login"))

            session["usuario"] = user["usuario"]
            session["rol"] = user["rol"]
            session["user_id"] = user["id"]

            if user["rol"] == 1:
                return redirect(url_for("dashboard_admin"))
            return redirect(url_for("dashboard_proveedor"))

        flash("Credenciales incorrectas")

    return render_template("login.html")

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        usuario = request.form.get("usuario", "").strip()
        correo = request.form.get("correo", "").strip()
        contrasena = request.form.get("contrasena", "")
        rol = int(request.form.get("rol") or 2)

        password_hash = generate_password_hash(contrasena)

        conn = get_conn()
        cur = conn.cursor()

        try:
            # Si tu tabla usuarios ya tiene más columnas de proveedor, aquí las insertas.
            # Para no romperte si no existen, insertamos las básicas.
            cur.execute(
                """
                INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (nombre, usuario, correo, password_hash, rol, "pendiente")
            )
            conn.commit()
            flash("Registro exitoso. Espera aprobación del administrador.")
            return redirect(url_for("login"))

        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash("El usuario ya existe.")
        finally:
            cur.close()
            conn.close()

    return render_template("registro.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------- ADMIN ----------------
@app.route("/admin/dashboard")
def dashboard_admin():
    if "usuario" not in session or session.get("rol") != 1:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    # filtros GET
    providers = request.args.getlist("providers")  # multi-select
    year_raw = (request.args.get("year") or "").strip()
    month_raw = (request.args.get("month") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    provider_ids = []
    for x in providers:
        try:
            provider_ids.append(int(x))
        except:
            pass

    year_i = None
    month_i = None
    try:
        year_i = int(year_raw) if year_raw else None
    except:
        year_i = None
    try:
        month_i = int(month_raw) if month_raw else None
    except:
        month_i = None

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # pendientes
    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente' ORDER BY id DESC")
    pendientes = cur.fetchall()

    # TODOS proveedores aprobados (para el select)
    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2 ORDER BY nombre ASC")
    proveedores_todos = cur.fetchall()

    # proveedores filtrados por search (sin romper el select)
    proveedores_visibles = proveedores_todos
    if q:
        proveedores_visibles = [
            p for p in proveedores_todos
            if (p.get("nombre", "").lower().find(q) != -1)
            or (p.get("usuario", "").lower().find(q) != -1)
            or (p.get("correo", "").lower().find(q) != -1)
        ]

    # aplica filtro por proveedores seleccionados (en la vista)
    if provider_ids:
        proveedores_visibles = [p for p in proveedores_visibles if p["id"] in provider_ids]

    # proyectos
    # Si tu filtro por proveedor existe, lo aplica desde SQL para ser más rápido
    if provider_ids:
        cur.execute(
            "SELECT * FROM projects WHERE provider_id = ANY(%s) ORDER BY created_at DESC",
            (provider_ids,)
        )
    else:
        cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    proyectos = cur.fetchall()

    # filtro por periodo (si columnas existen en projects)
    if year_i is not None:
        proyectos = [pr for pr in proyectos if pr.get("periodo_year") == year_i]
    if month_i is not None:
        proyectos = [pr for pr in proyectos if pr.get("periodo_month") == month_i]

    # documentos
    cur.execute("SELECT * FROM documentos ORDER BY fecha_subida DESC")
    docs = cur.fetchall()

    cur.close()
    conn.close()

    # ✅ IMPORTANTÍSIMO: esto lo usa el template
    documentos_por_usuario = {}
    for d in docs:
        uid = d.get("usuario_id")
        pid = d.get("project_id")
        if uid is None or pid is None:
            continue
        documentos_por_usuario.setdefault(uid, {}).setdefault(pid, []).append(d)

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,
        proveedores=proveedores_visibles,
        proveedores_todos=proveedores_todos,
        proyectos=proyectos,
        documentos_por_usuario=documentos_por_usuario,  # ✅ ya no falla
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url,
        selected_providers=provider_ids,
        selected_year=year_i or "",
        selected_month=month_i or "",
        q=q,
        months=MONTHS
    )

@app.route("/admin/accion/<int:id>/<accion>")
def accion(id, accion):
    if "usuario" not in session or session.get("rol") != 1:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    if accion == "aprobar":
        cur.execute("UPDATE usuarios SET estado='aprobado' WHERE id=%s", (id,))
    else:
        cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))

    conn.commit()
    cur.close()
    conn.close()

    flash("Operación realizada.")
    return redirect(url_for("dashboard_admin"))

@app.route("/admin/delete_user", methods=["POST"])
def delete_user():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"})

    data = request.get_json() or {}
    user_id = data.get("id")

    if not user_id:
        return jsonify({"success": False, "msg": "ID inválido"})

    if int(user_id) == int(session["user_id"]):
        return jsonify({"success": False, "msg": "No puedes borrar tu propia cuenta"})

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # borrar archivos S3 del usuario
    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()
    for d in docs:
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=d["ruta"])
        except Exception as e:
            print("Error eliminando S3:", e)

    # borrar BD
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "msg": "Usuario eliminado correctamente"})

@app.route("/admin/delete_project", methods=["POST"])
def delete_project():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    project_id = data.get("project_id")

    if not project_id:
        return jsonify({"success": False, "msg": "Project ID inválido"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # borrar docs del proyecto en S3
    cur.execute("SELECT ruta FROM documentos WHERE project_id=%s", (project_id,))
    docs = cur.fetchall()
    for d in docs:
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=d["ruta"])
        except Exception as e:
            print("Error delete S3:", e)

    # borrar docs BD + proyecto
    cur.execute("DELETE FROM documentos WHERE project_id=%s", (project_id,))
    cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "msg": "Proyecto eliminado"})

@app.route("/admin/send_reminder", methods=["POST"], endpoint="send_reminder")
def send_reminder():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "message": "Acceso denegado"}), 403

    data = request.get_json() or {}
    provider_ids = data.get("provider_ids", [])
    subject = data.get("subject", "Recordatorio REPSE")
    message = data.get("message", "")

    if not provider_ids:
        return jsonify({"success": False, "message": "No providers selected"}), 400

    # (Aquí va tu envío real por Outlook/SMTP si lo implementas)
    sent = len(provider_ids)
    return jsonify({"success": True, "sent": sent})

# ---------------- PROVEEDOR (placeholder) ----------------
@app.route("/proveedor/dashboard", methods=["GET", "POST"])
def dashboard_proveedor():
    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    # Si tu flujo real manda primero a meses habilitados/requerimientos, aquí mantén lo tuyo.
    # Esto es solo para que no truene.
    return render_template("dashboard_proveedor.html", DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS, months=MONTHS)

if __name__ == "__main__":
    app.run()
