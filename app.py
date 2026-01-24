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

# -------------------- DB --------------------
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

# -------------------- AWS S3 --------------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME", "repse-documento")

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    region_name=AWS_REGION
)

# -------------------- CONSTANTS --------------------
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

# -------------------- S3 HELPERS --------------------
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

# -------------------- AUTH --------------------
@app.route("/", methods=["GET", "POST"])
def login():
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
                # NUEVO: proveedor entra primero a meses habilitados
                return redirect(url_for("meses_habilitados"))
        else:
            flash("Credenciales incorrectas")

    return render_template("login.html")

@app.route("/registro", methods=["GET", "POST"])
def registro():
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
                "INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado) VALUES(%s,%s,%s,%s,%s,%s)",
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

# -------------------- ADMIN --------------------
@app.route("/admin/dashboard")
def dashboard_admin():
    if "usuario" not in session or session.get("rol") != 1:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente' ORDER BY id DESC")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2 ORDER BY nombre ASC")
    proveedores = cur.fetchall()

    # proyectos
    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    proyectos = cur.fetchall()

    # docs globales (por periodo)
    cur.execute("""
        SELECT * FROM documentos
        ORDER BY fecha_subida DESC
    """)
    docs = cur.fetchall()

    # docs por usuario y por periodo (global)
    docs_by_user_period = {}
    for d in docs:
        uid = d["usuario_id"]
        key = (d.get("periodo_year"), d.get("periodo_month"))
        docs_by_user_period.setdefault(uid, {}).setdefault(key, []).append(d)

    # docs aplicados por pedido
    cur.execute("SELECT project_id, tipo_documento FROM project_required_docs")
    req_rows = cur.fetchall()
    required_by_project = {}
    for r in req_rows:
        required_by_project.setdefault(r["project_id"], set()).add(r["tipo_documento"])

    cur.close()
    conn.close()

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,
        proveedores=proveedores,
        proyectos=proyectos,
        documentos_por_usuario_periodo=docs_by_user_period,
        required_by_project=required_by_project,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=lambda key, name=None: get_presigned_url(key, name),
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
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    user_id = _safe_int(data.get("id"))
    if user_id is None:
        return jsonify({"success": False, "msg": "ID inválido"}), 400

    if user_id == session.get("user_id"):
        return jsonify({"success": False, "msg": "No puedes borrar tu propia cuenta"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()
    for d in docs:
        s3_delete_key(d["ruta"])

    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM provider_enabled_months WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "msg": "Usuario eliminado correctamente"})

# -------- ADMIN: habilitar meses --------
@app.route("/admin/set_enabled_months", methods=["POST"])
def set_enabled_months():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    provider_id = _safe_int(data.get("provider_id"))
    year = _safe_int(data.get("year"))
    months = data.get("months", [])

    if not provider_id or not year or not isinstance(months, list):
        return jsonify({"success": False, "msg": "Datos inválidos"}), 400

    months_int = []
    for m in months:
        mi = _safe_int(m)
        if mi and 1 <= mi <= 12:
            months_int.append(mi)

    conn = get_conn()
    cur = conn.cursor()

    # limpiamos ese año y lo volvemos a setear
    cur.execute("DELETE FROM provider_enabled_months WHERE provider_id=%s AND year=%s", (provider_id, year))
    for m in months_int:
        cur.execute("""
            INSERT INTO provider_enabled_months(provider_id, year, month, enabled)
            VALUES(%s,%s,%s,TRUE)
            ON CONFLICT(provider_id, year, month) DO UPDATE SET enabled=TRUE
        """, (provider_id, year, m))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "msg": "Meses habilitados actualizados"})

# -------------------- PROVEEDOR: MESES HABILITADOS --------------------
@app.route("/proveedor/meses", methods=["GET"])
def meses_habilitados():
    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    year = _safe_int(request.args.get("year")) or datetime.utcnow().year

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("""
        SELECT month FROM provider_enabled_months
        WHERE provider_id=%s AND year=%s AND enabled=TRUE
        ORDER BY month ASC
    """, (session["user_id"], year))
    rows = cur.fetchall()
    enabled_months = [r["month"] for r in rows]

    cur.close()
    conn.close()

    return render_template(
        "meses_habilitados.html",
        year=year,
        months=MONTHS,
        enabled_months=enabled_months
    )

# -------------------- PROVEEDOR: REQUERIMIENTOS (por mes habilitado) --------------------
@app.route("/proveedor/requerimientos", methods=["GET", "POST"])
def requerimientos():
    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    year = _safe_int(request.args.get("year"))
    month = _safe_int(request.args.get("month"))

    if not year or not month or month not in MONTHS:
        flash("Selecciona un mes habilitado.")
        return redirect(url_for("meses_habilitados"))

    # validar que esté habilitado
    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("""
        SELECT 1 FROM provider_enabled_months
        WHERE provider_id=%s AND year=%s AND month=%s AND enabled=TRUE
    """, (session["user_id"], year, month))
    ok = cur.fetchone()
    if not ok:
        cur.close()
        conn.close()
        flash("Ese mes no está habilitado.")
        return redirect(url_for("meses_habilitados", year=year))

    if request.method == "POST":
        count = _safe_int(request.form.get("count"))
        if not count or count < 1:
            flash("Indica cuántos pedidos registrarás.")
            cur.close()
            conn.close()
            return redirect(url_for("requerimientos", year=year, month=month))

        created = 0
        for i in range(1, count + 1):
            pedido = (request.form.get(f"pedido_no_{i}") or "").strip()
            if not pedido:
                continue
            name = f"Pedido {pedido}"
            cur.execute("""
                INSERT INTO projects(provider_id, name, created_at, pedido_no, periodo_year, periodo_month, completed)
                VALUES(%s,%s,NOW(),%s,%s,%s,0)
            """, (session["user_id"], name, pedido, year, month))
            created += 1

        conn.commit()
        cur.close()
        conn.close()

        flash(f"Se registraron {created} pedido(s) para {MONTHS[month]} {year}.")
        return redirect(url_for("dashboard_proveedor", year=year, month=month))

    cur.close()
    conn.close()
    return render_template("requerimientos.html", months=MONTHS, year=year, month=month)

# -------------------- PROVEEDOR DASHBOARD (docs globales arriba + pedidos abajo) --------------------
@app.route("/proveedor/dashboard", methods=["GET", "POST"])
def dashboard_proveedor():
    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    year = _safe_int(request.args.get("year"))
    month = _safe_int(request.args.get("month"))
    q = (request.args.get("q") or "").strip()

    if not year or not month or month not in MONTHS:
        # si no viene periodo, manda a meses habilitados
        return redirect(url_for("meses_habilitados"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # validar mes habilitado
    cur.execute("""
        SELECT 1 FROM provider_enabled_months
        WHERE provider_id=%s AND year=%s AND month=%s AND enabled=TRUE
    """, (session["user_id"], year, month))
    if not cur.fetchone():
        cur.close()
        conn.close()
        flash("Ese mes no está habilitado.")
        return redirect(url_for("meses_habilitados", year=year))

    # ---- POST actions ----
    if request.method == "POST":
        action = request.form.get("action")

        # 1) Subir documento GLOBAL para el periodo (una vez)
        if action == "upload_global_doc":
            tipo = request.form.get("tipo_documento")
            archivo = request.files.get("documento")

            if not tipo or tipo not in DOCUMENTOS_OBLIGATORIOS or not archivo or not archivo.filename:
                flash("Selecciona un documento y un archivo.")
            else:
                ext = archivo.filename.rsplit(".", 1)[-1].lower()
                if ext not in ALLOWED_EXT:
                    flash("Tipo de archivo no permitido.")
                else:
                    safe_original = _clean_filename(archivo.filename)
                    key = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{session['user_id']}_{year}{month:02d}_{_clean_filename(tipo)}_{safe_original}"

                    # si ya existe ese doc en ese periodo => lo reemplazamos (borrando S3 anterior)
                    cur.execute("""
                        SELECT id, ruta FROM documentos
                        WHERE usuario_id=%s AND tipo_documento=%s
                          AND periodo_year=%s AND periodo_month=%s
                          AND (project_id IS NULL)
                        ORDER BY fecha_subida DESC
                        LIMIT 1
                    """, (session["user_id"], tipo, year, month))
                    prev = cur.fetchone()
                    if prev and prev.get("ruta"):
                        s3_delete_key(prev["ruta"])

                    try:
                        s3.upload_fileobj(archivo, BUCKET_NAME, key, ExtraArgs={"ACL": "private"})
                        if prev:
                            cur.execute("""
                                UPDATE documentos
                                SET nombre_archivo=%s, ruta=%s, fecha_subida=NOW()
                                WHERE id=%s
                            """, (safe_original, key, prev["id"]))
                        else:
                            cur.execute("""
                                INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id, periodo_year, periodo_month)
                                VALUES(%s,%s,%s,%s,NOW(),NULL,%s,%s)
                            """, (session["user_id"], safe_original, key, tipo, year, month))

                        conn.commit()
                        flash(f"Documento '{tipo}' subido correctamente.")
                    except Exception as e:
                        conn.rollback()
                        flash("Error subiendo a S3: " + str(e))

        # 2) Actualizar qué docs aplican a un pedido
        if action == "update_project_docs":
            project_id = _safe_int(request.form.get("project_id"))
            if project_id:
                selected = request.form.getlist("docs_aplican")  # tipos

                # limpiar y reinsertar
                cur.execute("DELETE FROM project_required_docs WHERE project_id=%s", (project_id,))
                for t in selected:
                    if t in DOCUMENTOS_OBLIGATORIOS:
                        cur.execute("""
                            INSERT INTO project_required_docs(project_id, tipo_documento)
                            VALUES(%s,%s)
                            ON CONFLICT(project_id, tipo_documento) DO NOTHING
                        """, (project_id, t))
                conn.commit()
                flash("Documentos aplicables actualizados.")

        # 3) Completar pedido
        if action == "complete_project":
            project_id = _safe_int(request.form.get("project_id"))
            if project_id:
                cur.execute("""
                    UPDATE projects SET completed=1
                    WHERE id=%s AND provider_id=%s
                """, (project_id, session["user_id"]))
                conn.commit()
                flash("Pedido marcado como completado ✅")

        return redirect(url_for("dashboard_proveedor", year=year, month=month, q=q))

    # ---- GET data ----

    # docs globales del periodo
    cur.execute("""
        SELECT * FROM documentos
        WHERE usuario_id=%s
          AND periodo_year=%s AND periodo_month=%s
          AND (project_id IS NULL)
        ORDER BY fecha_subida DESC
    """, (session["user_id"], year, month))
    docs_periodo = cur.fetchall()

    docs_map = {}
    for d in docs_periodo:
        # nos quedamos con el más reciente por tipo
        t = d.get("tipo_documento")
        if t and t not in docs_map:
            docs_map[t] = d

    # pedidos del periodo
    params = [session["user_id"], year, month]
    where = "provider_id=%s AND periodo_year=%s AND periodo_month=%s"
    if q:
        where += " AND (name ILIKE %s OR COALESCE(pedido_no,'') ILIKE %s)"
        like = f"%{q}%"
        params.extend([like, like])

    cur.execute(f"""
        SELECT * FROM projects
        WHERE {where}
        ORDER BY created_at DESC
    """, params)
    projects = cur.fetchall()

    # docs aplicables por pedido
    cur.execute("""
        SELECT project_id, tipo_documento
        FROM project_required_docs
        WHERE project_id = ANY(%s)
    """, ([p["id"] for p in projects] or [0],))
    req_rows = cur.fetchall()
    required_by_project = {}
    for r in req_rows:
        required_by_project.setdefault(r["project_id"], set()).add(r["tipo_documento"])

    cur.close()
    conn.close()

    return render_template(
        "dashboard_proveedor.html",
        year=year,
        month=month,
        month_name=MONTHS[month],
        q=q,
        months=MONTHS,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        docs_map=docs_map,
        projects=projects,
        required_by_project=required_by_project
    )

# -------------------- RUN --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() in ("1", "true")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
