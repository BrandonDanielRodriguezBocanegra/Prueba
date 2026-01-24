# app.py
import os
import re
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

import psycopg
import psycopg.rows
import psycopg.errors

import boto3


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

# ===================== DB =====================
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    if "sslmode=" in url:
        return url
    joiner = "&" if "?" in url else "?"
    return url + f"{joiner}sslmode=require"

def get_conn():
    return psycopg.connect(_normalize_db_url(DATABASE_URL))

def ensure_tables():
    """
    Crea tablas NUEVAS si no existen (no rompe la DB).
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        # meses habilitados para cada proveedor
        cur.execute("""
        CREATE TABLE IF NOT EXISTS enabled_periods(
            id SERIAL PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES usuarios(id),
            periodo_year INT NOT NULL,
            periodo_month INT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE(provider_id, periodo_year, periodo_month)
        )
        """)

        # docs que aplican / completado por pedido (project)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS project_docs(
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            tipo_documento TEXT NOT NULL,
            aplica BOOLEAN NOT NULL DEFAULT FALSE,
            completed BOOLEAN NOT NULL DEFAULT FALSE,
            UNIQUE(project_id, tipo_documento)
        )
        """)
        conn.commit()
    finally:
        cur.close()
        conn.close()

# ===================== AWS S3 =====================
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME", "repse-documento")

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
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

MONTHS = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png"}

def _safe_int(x):
    try:
        return int(x)
    except:
        return None

def _clean_filename(name: str) -> str:
    name = name or "archivo"
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")
    return name[:180] if len(name) > 180 else name

def get_presigned_url(s3_key: str, download_name: str | None = None) -> str | None:
    if not s3_key:
        return None
    try:
        params = {"Bucket": BUCKET_NAME, "Key": s3_key}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{_clean_filename(download_name)}"'
        return s3.generate_presigned_url("get_object", Params=params, ExpiresIn=300)
    except Exception as e:
        print("Presign error:", e)
        return None

def s3_delete_key(key: str):
    if not key:
        return
    try:
        s3.delete_object(Bucket=BUCKET_NAME, Key=key)
    except Exception as e:
        print("S3 delete error:", e)

# ===================== AUTH =====================
@app.route("/", methods=["GET", "POST"])
def login():
    ensure_tables()

    if request.method == "POST":
        usuario = request.form.get("usuario")
        contrasena = request.form.get("contrasena")

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password"], contrasena):
            if user["estado"] == "pendiente":
                flash("Tu cuenta está pendiente de aprobación.")
                return redirect(url_for("login"))

            session["usuario"] = user["usuario"]
            session["rol"] = user["rol"]
            session["user_id"] = user["id"]

            if user["rol"] == 1:
                return redirect(url_for("dashboard_admin"))
            else:
                return redirect(url_for("meses_habilitados"))
        else:
            flash("Credenciales incorrectas")

    return render_template("login.html")

@app.route("/registro", methods=["GET", "POST"])
def registro():
    ensure_tables()

    if request.method == "POST":
        nombre = request.form.get("nombre")
        usuario = request.form.get("usuario")
        correo = request.form.get("correo")
        contrasena = request.form.get("contrasena")
        rol = int(request.form.get("rol") or 2)

        password_hash = generate_password_hash(contrasena)

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (nombre, usuario, correo, password_hash, rol, "pendiente")
            )
            conn.commit()
            flash("Registro exitoso. Espera aprobación del administrador.")
            return redirect(url_for("login"))
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash("El usuario ya existe.")
        except Exception as e:
            conn.rollback()
            flash("Error en el registro: " + str(e))
        finally:
            cur.close()
            conn.close()

    return render_template("registro.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ===================== ADMIN =====================
@app.route("/admin/dashboard")
def dashboard_admin():
    ensure_tables()

    if "usuario" not in session or session.get("rol") != 1:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    # -------- filtros GET --------
    provider_ids = request.args.getlist("providers")
    provider_ids_int = []
    for x in provider_ids:
        xi = _safe_int(x)
        if xi is not None:
            provider_ids_int.append(xi)

    year = (request.args.get("year", "") or "").strip()
    month = (request.args.get("month", "") or "").strip()
    q = (request.args.get("q", "") or "").strip()

    selected_year = int(year) if year.isdigit() else None
    selected_month = int(month) if month.isdigit() else None

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # pendientes
    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente' ORDER BY id DESC")
    pendientes = cur.fetchall()

    # proveedores ALL (para selects / otras pestañas)
    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2 ORDER BY nombre ASC")
    proveedores_all = cur.fetchall()

    # proveedores filtrados (solo pestaña proveedores)
    proveedores = proveedores_all
    if provider_ids_int:
        proveedores = [p for p in proveedores_all if p["id"] in provider_ids_int]

    # -------- proyectos filtrados --------
    where = []
    params = []

    if provider_ids_int:
        where.append("provider_id = ANY(%s)")
        params.append(provider_ids_int)

    if selected_year is not None:
        where.append("periodo_year = %s")
        params.append(selected_year)

    if selected_month is not None:
        where.append("periodo_month = %s")
        params.append(selected_month)

    if q:
        where.append("(name ILIKE %s OR COALESCE(pedido_no,'') ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like])

    sql_projects = "SELECT * FROM projects"
    if where:
        sql_projects += " WHERE " + " AND ".join(where)
    sql_projects += " ORDER BY created_at DESC"

    cur.execute(sql_projects, params)
    projects = cur.fetchall()
    project_ids = [p["id"] for p in projects]

    # -------- documentos (solo globales, porque ahora se suben 1 vez) --------
    # los ocupamos para mapear descargas por tipo_documento
    cur.execute("""
        SELECT * FROM documentos
        WHERE project_id IS NULL
        ORDER BY fecha_subida DESC
    """)
    global_docs_all = cur.fetchall()

    # -------- project_docs (qué aplica / completed por pedido) --------
    project_docs_map = {}
    if project_ids:
        cur.execute("""
            SELECT * FROM project_docs
            WHERE project_id = ANY(%s)
        """, (project_ids,))
        rows = cur.fetchall()
        for r in rows:
            project_docs_map.setdefault(r["project_id"], {})[r["tipo_documento"]] = r

    # -------- meses hábiles --------
    cur.execute("""
        SELECT ep.*, u.nombre, u.usuario, u.correo
        FROM enabled_periods ep
        JOIN usuarios u ON u.id = ep.provider_id
        ORDER BY ep.periodo_year DESC, ep.periodo_month DESC
    """)
    enabled_periods = cur.fetchall()

    cur.close()
    conn.close()

    # agrupar global docs por usuario (solo globales => pid=0)
    docs_by_user = {}
    for d in global_docs_all:
        pid = 0
        docs_by_user.setdefault(d["usuario_id"], {}).setdefault(pid, []).append(d)

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,
        proveedores_all=proveedores_all,
        proveedores=proveedores,
        proyectos=projects,
        documentos_por_usuario=docs_by_user,  # solo globales por usuario
        project_docs_map=project_docs_map,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        # PASAR LA FUNCIÓN REAL (no lambda) para poder usar (key, nombre)
        get_presigned_url=get_presigned_url,
        selected_provider_ids=provider_ids_int,
        selected_year=selected_year,
        selected_month=selected_month,
        q=q,
        months=MONTHS,
        enabled_periods=enabled_periods
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
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    user_id = _safe_int(data.get("id"))
    if user_id is None:
        return jsonify({"success": False, "msg": "ID inválido"}), 400

    if user_id == session.get("user_id"):
        return jsonify({"success": False, "msg": "No puedes borrar tu propia cuenta"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # borrar S3 docs del usuario
    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()
    for d in docs:
        s3_delete_key(d["ruta"])

    # borrar BD
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM project_docs WHERE project_id IN (SELECT id FROM projects WHERE provider_id=%s)", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM enabled_periods WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "msg": "Usuario eliminado correctamente"})

@app.route("/admin/enable_month", methods=["POST"])
def enable_month():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    provider_id = _safe_int(data.get("provider_id"))
    year = _safe_int(data.get("year"))
    month = _safe_int(data.get("month"))

    if not provider_id or not year or not month or month not in MONTHS:
        return jsonify({"success": False, "msg": "Datos inválidos"}), 400

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO enabled_periods(provider_id, periodo_year, periodo_month)
            VALUES(%s,%s,%s)
            ON CONFLICT(provider_id, periodo_year, periodo_month) DO NOTHING
        """, (provider_id, year, month))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "msg": str(e)}), 500
    finally:
        cur.close()
        conn.close()

    return jsonify({"success": True, "msg": "Mes habilitado"})

@app.route("/admin/disable_month", methods=["POST"])
def disable_month():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    ep_id = _safe_int(data.get("id"))
    if not ep_id:
        return jsonify({"success": False, "msg": "ID inválido"}), 400

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM enabled_periods WHERE id=%s", (ep_id,))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "msg": "Mes deshabilitado"})

@app.route("/admin/delete_project", methods=["POST"])
def delete_project():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    project_id = _safe_int(data.get("project_id"))
    if project_id is None:
        return jsonify({"success": False, "msg": "project_id inválido"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT ruta FROM documentos WHERE project_id=%s", (project_id,))
    docs = cur.fetchall()
    for d in docs:
        s3_delete_key(d["ruta"])

    cur.execute("DELETE FROM project_docs WHERE project_id=%s", (project_id,))
    cur.execute("DELETE FROM documentos WHERE project_id=%s", (project_id,))
    cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "msg": "Proyecto eliminado correctamente"})

@app.route("/admin/send_reminder", methods=["POST"], endpoint="send_reminder")
def send_reminder():
    # (simulado como lo traías)
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "message": "Acceso denegado"}), 403
    data = request.get_json() or {}
    provider_ids = data.get("provider_ids", [])
    sent = len(provider_ids) if provider_ids else 0
    return jsonify({"success": True, "sent": sent})

# ===================== PROVEEDOR: MESES HABILITADOS =====================
@app.route("/proveedor/meses")
def meses_habilitados():
    ensure_tables()

    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("""
        SELECT * FROM enabled_periods
        WHERE provider_id=%s
        ORDER BY periodo_year DESC, periodo_month DESC
    """, (session["user_id"],))
    periods = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("meses_habilitados.html", periods=periods, months=MONTHS)

# ===================== PROVEEDOR: REQUERIMIENTOS =====================
@app.route("/proveedor/requerimientos", methods=["GET", "POST"])
def requerimientos():
    ensure_tables()

    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    year = _safe_int(request.args.get("year"))
    month = _safe_int(request.args.get("month"))

    if not year or not month or month not in MONTHS:
        flash("Periodo inválido.")
        return redirect(url_for("meses_habilitados"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # verificar habilitado
    cur.execute("""
        SELECT 1 FROM enabled_periods
        WHERE provider_id=%s AND periodo_year=%s AND periodo_month=%s
        LIMIT 1
    """, (session["user_id"], year, month))
    ok = cur.fetchone()
    if not ok:
        cur.close()
        conn.close()
        flash("Ese mes no está habilitado.")
        return redirect(url_for("meses_habilitados"))

    if request.method == "POST":
        # botón "no se registraran nuevos pedidos"
        if request.form.get("skip") == "1":
            cur.close()
            conn.close()
            return redirect(url_for("dashboard_proveedor", year=year, month=month))

        count = _safe_int(request.form.get("count"))
        if not count or count < 1:
            flash("Indica cuántos pedidos registrarás.")
            cur.close()
            conn.close()
            return render_template("requerimientos.html", months=MONTHS, year=year, month=month)

        created = 0
        for i in range(1, count + 1):
            pedido = (request.form.get(f"pedido_no_{i}") or "").strip()
            if not pedido:
                continue

            name = f"Pedido {pedido}"
            cur.execute("""
                INSERT INTO projects(provider_id, name, created_at, pedido_no, periodo_year, periodo_month)
                VALUES(%s,%s,NOW(),%s,%s,%s)
            """, (session["user_id"], name, pedido, year, month))
            created += 1

        conn.commit()
        cur.close()
        conn.close()

        flash(f"Se registraron {created} pedido(s) del periodo {MONTHS[month]} {year}.")
        return redirect(url_for("dashboard_proveedor", year=year, month=month))

    cur.close()
    conn.close()
    return render_template("requerimientos.html", months=MONTHS, year=year, month=month)

# ===================== PROVEEDOR DASHBOARD =====================
@app.route("/proveedor/dashboard", methods=["GET", "POST"])
def dashboard_proveedor():
    ensure_tables()

    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    selected_year = _safe_int(request.args.get("year"))
    selected_month = _safe_int(request.args.get("month"))
    q = (request.args.get("q", "") or "").strip()

    if request.method == "POST":
        action = request.form.get("action")

        # -------- subir doc GLOBAL (una vez, reemplazable)
        if action == "upload_global_doc":
            tipo = request.form.get("tipo_documento")
            archivo = request.files.get("documento")

            if not tipo or tipo not in DOCUMENTOS_OBLIGATORIOS:
                flash("Tipo de documento inválido.")
            elif not archivo or not archivo.filename:
                flash("Selecciona un archivo.")
            else:
                ext = archivo.filename.rsplit(".", 1)[-1].lower()
                if ext not in ALLOWED_EXT:
                    flash("Tipo de archivo no permitido.")
                else:
                    safe_original = _clean_filename(archivo.filename)
                    key = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{session['user_id']}_GLOBAL_{safe_original}"

                    try:
                        # si ya existía ese tipo global, lo reemplazamos
                        cur.execute("""
                            SELECT id, ruta FROM documentos
                            WHERE usuario_id=%s AND project_id IS NULL AND tipo_documento=%s
                            ORDER BY fecha_subida DESC
                            LIMIT 1
                        """, (session["user_id"], tipo))
                        old = cur.fetchone()
                        if old and old["ruta"]:
                            s3_delete_key(old["ruta"])
                            cur.execute("DELETE FROM documentos WHERE id=%s", (old["id"],))

                        s3.upload_fileobj(archivo, BUCKET_NAME, key, ExtraArgs={"ACL": "private"})

                        cur.execute("""
                            INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id)
                            VALUES(%s,%s,%s,%s,NOW(),NULL)
                        """, (session["user_id"], safe_original, key, tipo))

                        conn.commit()
                        flash("Documento subido correctamente.")
                    except Exception as e:
                        conn.rollback()
                        flash("Error subiendo a S3: " + str(e))

        # -------- toggle aplica doc en pedido
        elif action == "toggle_aplica":
            project_id = _safe_int(request.form.get("project_id"))
            tipo = request.form.get("tipo_documento")
            aplica = request.form.get("aplica") == "1"

            if project_id and tipo in DOCUMENTOS_OBLIGATORIOS:
                cur.execute("""
                    INSERT INTO project_docs(project_id, tipo_documento, aplica, completed)
                    VALUES(%s,%s,%s,FALSE)
                    ON CONFLICT(project_id, tipo_documento)
                    DO UPDATE SET aplica=EXCLUDED.aplica
                """, (project_id, tipo, aplica))
                conn.commit()

        # -------- toggle pedido completado
        elif action == "toggle_project_completed":
            project_id = _safe_int(request.form.get("project_id"))
            if project_id:
                cur.execute("""
                    SELECT completed FROM projects
                    WHERE id=%s AND provider_id=%s
                """, (project_id, session["user_id"]))
                row = cur.fetchone()
                if row:
                    new_val = 0 if row["completed"] == 1 else 1
                    cur.execute("UPDATE projects SET completed=%s WHERE id=%s", (new_val, project_id))
                    conn.commit()

    # -------- proyectos filtrables
    where = ["provider_id=%s"]
    params = [session["user_id"]]

    if selected_year:
        where.append("periodo_year=%s")
        params.append(selected_year)
    if selected_month:
        where.append("periodo_month=%s")
        params.append(selected_month)
    if q:
        where.append("(name ILIKE %s OR COALESCE(pedido_no,'') ILIKE %s)")
        like = f"%{q}%"
        params.extend([like, like])

    sql_projects = "SELECT * FROM projects WHERE " + " AND ".join(where) + " ORDER BY created_at DESC"
    cur.execute(sql_projects, params)
    projects = cur.fetchall()
    project_ids = [p["id"] for p in projects]

    # docs globales (project_id NULL)
    cur.execute("""
        SELECT * FROM documentos
        WHERE usuario_id=%s AND project_id IS NULL
        ORDER BY fecha_subida DESC
    """, (session["user_id"],))
    global_docs = cur.fetchall()

    global_by_tipo = {}
    for d in global_docs:
        t = d.get("tipo_documento")
        if t and t not in global_by_tipo:
            global_by_tipo[t] = d

    # project_docs map
    project_docs_map = {}
    if project_ids:
        cur.execute("SELECT * FROM project_docs WHERE project_id = ANY(%s)", (project_ids,))
        rows = cur.fetchall()
        for r in rows:
            project_docs_map.setdefault(r["project_id"], {})[r["tipo_documento"]] = r

    cur.close()
    conn.close()

    return render_template(
        "dashboard_proveedor.html",
        projects=projects,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        months=MONTHS,
        selected_year=selected_year,
        selected_month=selected_month,
        q=q,
        global_by_tipo=global_by_tipo,
        project_docs_map=project_docs_map
    )

# ===================== RUN =====================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ("1", "true")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
