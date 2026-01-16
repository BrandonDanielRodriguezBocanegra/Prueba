# app.py
import os
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg
import psycopg.rows
import psycopg.errors
import boto3

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL no está configurada.")
    # Render suele requerir sslmode=require
    url = DATABASE_URL
    if "sslmode=" not in url:
        url = url + ("&" if "?" in url else "?") + "sslmode=require"
    return psycopg.connect(url, connect_timeout=10)

# ---------- AWS CONFIG ----------
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

MONTHS_ES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}

def get_presigned_url(key):
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=300
        )
    except Exception as e:
        print("Presigned error:", e)
        return None

def _safe_filename(name: str) -> str:
    # Evitar cosas raras, pero conservando nombre original
    return name.replace("\\", "_").replace("/", "_").strip()

# ----------------------- LOGIN -----------------------
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
                return redirect(url_for("requerimientos_proveedor"))
        else:
            flash("Credenciales incorrectas")

    return render_template("login.html")

# ----------------------- REGISTRO -----------------------
@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        usuario = request.form.get("usuario")
        correo = request.form.get("correo")
        contrasena = request.form.get("contrasena")
        rol = int(request.form.get("rol") or 2)
        password_hash = generate_password_hash(contrasena)

        # Campos extra proveedor (solo si rol=2)
        extra = {
            "repse_registro": request.form.get("repse_registro"),
            "repse_folio": request.form.get("repse_folio"),
            "repse_aviso_num": request.form.get("repse_aviso_num"),
            "repse_aviso_fecha": request.form.get("repse_aviso_fecha") or None,
            "repse_vigencia": request.form.get("repse_vigencia") or None,
            "repse_rfc": request.form.get("repse_rfc"),
            "repse_regimen_patronal": request.form.get("repse_regimen_patronal"),
            "repse_objeto_servicio": request.form.get("repse_objeto_servicio"),
            "contacto_nombre": request.form.get("contacto_nombre"),
            "contacto_telefono": request.form.get("contacto_telefono"),
            "contacto_correo": request.form.get("contacto_correo"),
        }

        conn = get_conn()
        cur = conn.cursor()
        try:
            if rol == 2:
                cur.execute("""
                    INSERT INTO usuarios(
                        nombre, usuario, correo, password, rol, estado,
                        repse_registro, repse_folio, repse_aviso_num, repse_aviso_fecha,
                        repse_vigencia, repse_rfc, repse_regimen_patronal, repse_objeto_servicio,
                        contacto_nombre, contacto_telefono, contacto_correo
                    )
                    VALUES(
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s,%s,
                        %s,%s,%s
                    )
                """, (
                    nombre, usuario, correo, password_hash, rol, "pendiente",
                    extra["repse_registro"], extra["repse_folio"], extra["repse_aviso_num"], extra["repse_aviso_fecha"],
                    extra["repse_vigencia"], extra["repse_rfc"], extra["repse_regimen_patronal"], extra["repse_objeto_servicio"],
                    extra["contacto_nombre"], extra["contacto_telefono"], extra["contacto_correo"],
                ))
            else:
                cur.execute("""
                    INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                    VALUES(%s,%s,%s,%s,%s,%s)
                """, (nombre, usuario, correo, password_hash, rol, "pendiente"))

            conn.commit()
            flash("Registro exitoso. Espera aprobación del administrador.")
            return redirect(url_for("login"))

        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash("El usuario o correo ya existe.")
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

# ----------------------- PROVEEDOR: REQUERIMIENTOS -----------------------
@app.route("/proveedor/requerimientos", methods=["GET", "POST"])
def requerimientos_proveedor():
    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE id=%s", (session["user_id"],))
    user = cur.fetchone()

    if not user or user["estado"] != "aprobado":
        cur.close()
        conn.close()
        flash("Tu cuenta aún no ha sido aprobada.")
        return redirect(url_for("login"))

    if request.method == "POST":
        periodo_year = int(request.form.get("periodo_year"))
        periodo_month = int(request.form.get("periodo_month"))
        total = int(request.form.get("total_proyectos"))

        # Crear proyectos según lo que capturó
        for i in range(1, total + 1):
            pedido_no = (request.form.get(f"pedido_no_{i}") or "").strip()
            if not pedido_no:
                continue
            # Usamos pedido como "name" también para que sea consistente
            cur.execute("""
                INSERT INTO projects(provider_id, name, created_at, pedido_no, periodo_year, periodo_month)
                VALUES(%s,%s,NOW(),%s,%s,%s)
            """, (user["id"], pedido_no, pedido_no, periodo_year, periodo_month))

        conn.commit()
        cur.close()
        conn.close()

        flash("Pedidos registrados correctamente.")
        return redirect(url_for("dashboard_proveedor", year=periodo_year, month=periodo_month))

    cur.close()
    conn.close()

    years = list(range(datetime.utcnow().year - 2, datetime.utcnow().year + 3))
    return render_template("requerimientos.html", years=years, months=MONTHS_ES)

# ----------------------- PROVEEDOR DASHBOARD -----------------------
@app.route("/proveedor/dashboard", methods=["GET", "POST"])
def dashboard_proveedor():
    if "usuario" not in session or session.get("rol") != 2:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE id=%s", (session["user_id"],))
    user = cur.fetchone()

    if not user or user["estado"] != "aprobado":
        cur.close()
        conn.close()
        flash("Tu cuenta aún no ha sido aprobada.")
        return redirect(url_for("login"))

    # Subir / actualizar documento
    if request.method == "POST" and request.form.get("action") == "upload_doc":
        project_id = int(request.form.get("project_id"))
        tipo = request.form.get("tipo_documento")
        archivo = request.files.get("documento")

        if not archivo or archivo.filename == "":
            flash("Selecciona un archivo.")
            return redirect(url_for("dashboard_proveedor"))

        ext = archivo.filename.rsplit(".", 1)[-1].lower()
        if ext not in ["pdf", "jpg", "jpeg", "png"]:
            flash("Tipo de archivo no permitido.")
            return redirect(url_for("dashboard_proveedor"))

        original_name = _safe_filename(archivo.filename)
        key = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{original_name}"

        # Si ya existe un documento de ese tipo, lo reemplazamos (borra S3 anterior y actualiza row)
        cur.execute("""
            SELECT id, ruta FROM documentos
            WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s
            ORDER BY fecha_subida DESC
            LIMIT 1
        """, (user["id"], project_id, tipo))
        existing = cur.fetchone()

        # Subir nuevo a S3
        try:
            s3.upload_fileobj(archivo, BUCKET_NAME, key)
        except Exception as e:
            flash("Error subiendo a S3: " + str(e))
            cur.close()
            conn.close()
            return redirect(url_for("dashboard_proveedor"))

        # Borrar el anterior si existía
        if existing and existing["ruta"]:
            try:
                s3.delete_object(Bucket=BUCKET_NAME, Key=existing["ruta"])
            except Exception as e:
                print("No se pudo borrar S3 anterior:", e)

            cur.execute("""
                UPDATE documentos
                SET nombre_archivo=%s, ruta=%s, fecha_subida=NOW()
                WHERE id=%s
            """, (original_name, key, existing["id"]))
        else:
            cur.execute("""
                INSERT INTO documentos(usuario_id, nombre_archivo, ruta, tipo_documento, fecha_subida, project_id)
                VALUES(%s,%s,%s,%s,NOW(),%s)
            """, (user["id"], original_name, key, tipo, project_id))

        conn.commit()
        flash("Documento actualizado correctamente.")
        return redirect(url_for("dashboard_proveedor", year=request.args.get("year"), month=request.args.get("month")))

    # --- filtros proveedor por periodo (mes/año)
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)

    q = """
        SELECT * FROM projects
        WHERE provider_id=%s
    """
    params = [user["id"]]

    if year:
        q += " AND periodo_year=%s"
        params.append(year)
    if month:
        q += " AND periodo_month=%s"
        params.append(month)

    q += " ORDER BY created_at DESC"
    cur.execute(q, tuple(params))
    projects = cur.fetchall()

    # docs del usuario
    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s ORDER BY fecha_subida DESC", (user["id"],))
    docs = cur.fetchall()
    cur.close()
    conn.close()

    docs_by_project = {}
    for d in docs:
        docs_by_project.setdefault(d["project_id"], []).append(d)

    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p["id"]] = {}
        for doc in DOCUMENTOS_OBLIGATORIOS:
            for d in docs_by_project.get(p["id"], []):
                if d["tipo_documento"] == doc:
                    documentos_subidos[p["id"]][doc] = d

    years = list(range(datetime.utcnow().year - 2, datetime.utcnow().year + 3))

    return render_template(
        "dashboard_proveedor.html",
        projects=projects,
        documentos_subidos=documentos_subidos,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        years=years,
        months=MONTHS_ES,
        selected_year=year,
        selected_month=month
    )

# ----------------------- ADMIN DASHBOARD -----------------------
@app.route("/admin/dashboard")
def dashboard_admin():
    if "usuario" not in session or session.get("rol") != 1:
        flash("Acceso denegado")
        return redirect(url_for("login"))

    # filtros (para pestaña proveedores)
    selected_provider_ids = request.args.getlist("provider_id")
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    search = (request.args.get("search") or "").strip().lower()

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente' ORDER BY id DESC")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2 ORDER BY nombre ASC")
    proveedores_all = cur.fetchall()

    # proveedores filtrados SOLO para pestaña Proveedores
    proveedores_view = proveedores_all
    if selected_provider_ids:
        ids = tuple(int(x) for x in selected_provider_ids if x.isdigit())
        proveedores_view = [p for p in proveedores_all if p["id"] in ids]

    # proyectos con filtros de periodo + búsqueda
    projects_q = "SELECT * FROM projects WHERE 1=1"
    params = []
    if selected_provider_ids:
        ids = tuple(int(x) for x in selected_provider_ids if x.isdigit())
        if ids:
            projects_q += " AND provider_id = ANY(%s)"
            params.append(list(ids))
    if year:
        projects_q += " AND periodo_year=%s"
        params.append(year)
    if month:
        projects_q += " AND periodo_month=%s"
        params.append(month)
    if search:
        projects_q += " AND (LOWER(name) LIKE %s OR LOWER(COALESCE(pedido_no,'')) LIKE %s)"
        params.extend([f"%{search}%", f"%{search}%"])

    projects_q += " ORDER BY created_at DESC"
    cur.execute(projects_q, tuple(params))
    proyectos = cur.fetchall()

    cur.execute("SELECT * FROM documentos ORDER BY fecha_subida DESC")
    docs = cur.fetchall()

    cur.close()
    conn.close()

    documentos_por_usuario = {}
    for d in docs:
        documentos_por_usuario.setdefault(d["usuario_id"], {}).setdefault(d["project_id"], []).append(d)

    years = list(range(datetime.utcnow().year - 2, datetime.utcnow().year + 3))

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,

        # Para pestaña proveedores (con filtro)
        proveedores=proveedores_view,
        proyectos=proyectos,

        # Para gestión / datos / recordatorios (SIN filtro)
        proveedores_all=proveedores_all,

        documentos_por_usuario=documentos_por_usuario,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url,

        # filtros UI
        years=years,
        months=MONTHS_ES,
        selected_provider_ids=[int(x) for x in selected_provider_ids if x.isdigit()],
        selected_year=year,
        selected_month=month,
        selected_search=search
    )

# ----------------------- APROBAR / RECHAZAR -----------------------
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

# ----------------------- DELETE USER -----------------------
@app.route("/admin/delete_user", methods=["POST"])
def delete_user():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"})

    data = request.get_json() or {}
    user_id = int(data.get("id") or 0)

    if user_id == session.get("user_id"):
        return jsonify({"success": False, "msg": "No puedes borrar tu propia cuenta"})

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # borrar docs S3 del usuario
    cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
    docs = cur.fetchall()
    for d in docs:
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=d["ruta"])
        except Exception as e:
            print("Error borrando S3:", e)

    # borrar BD
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"success": True, "msg": "Usuario eliminado correctamente"})

# ----------------------- DELETE PROJECT (ADMIN) -----------------------
@app.route("/admin/delete_project", methods=["POST"])
def delete_project():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")

    try:
        project_id = int(project_id)
    except Exception:
        return jsonify({"success": False, "msg": "project_id inválido"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    try:
        # 1) Traer rutas de S3 antes de borrar
        cur.execute("SELECT ruta FROM documentos WHERE project_id=%s", (project_id,))
        docs = cur.fetchall()

        # 2) Borrar docs en S3
        for d in docs:
            key = d.get("ruta")
            if key:
                try:
                    s3.delete_object(Bucket=BUCKET_NAME, Key=key)
                except Exception as e:
                    print("Error borrando S3:", e)

        # 3) Borrar en BD (documentos primero para evitar FK)
        cur.execute("DELETE FROM documentos WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))

        conn.commit()
        return jsonify({"success": True, "msg": "Proyecto eliminado correctamente"})

    except Exception as e:
        conn.rollback()
        print("ERROR delete_project:", e)
        return jsonify({"success": False, "msg": f"Error eliminando proyecto: {e}"}), 500
    finally:
        cur.close()
        conn.close()

# ----------------------- REMINDER (placeholder) -----------------------
@app.route("/admin/send_reminder", methods=["POST"], endpoint="send_reminder")
def send_reminder():
    if "usuario" not in session or session.get("rol") != 1:
        return jsonify({"success": False, "message": "Acceso denegado"}), 403

    data = request.get_json() or {}
    provider_ids = data.get("provider_ids", [])
    if not provider_ids:
        return jsonify({"success": False, "message": "No providers selected"}), 400

    # Placeholder: aquí conectas SMTP Outlook real
    return jsonify({"success": True, "sent": len(provider_ids)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
