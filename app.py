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
    # psycopg3
    return psycopg.connect(DATABASE_URL)

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

# ========== HELPERS ==========
def get_presigned_url(key):
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=300
        )
    except Exception as e:
        print("Presign error:", e)
        return None

def require_admin():
    return ("usuario" in session and session.get("rol") == 1)

def require_provider():
    return ("usuario" in session and session.get("rol") == 2)

# ========== LOGIN ==========
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
                # si tienes requerimientos antes del dashboard, manda ahí
                # return redirect(url_for("requerimientos"))
                return redirect(url_for("dashboard_proveedor"))
        else:
            flash("Credenciales incorrectas")

    return render_template("login.html")

# ========== REGISTRO ==========
@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        usuario = request.form.get("usuario")
        correo = request.form.get("correo")
        contrasena = request.form.get("contrasena")
        rol = int(request.form.get("rol") or 2)

        password_hash = generate_password_hash(contrasena)

        # Campos extra proveedor (si no vienen, quedan None)
        repse_numero = request.form.get("repse_numero")
        registro_folio = request.form.get("registro_folio")
        aviso_registro = request.form.get("aviso_registro")
        vigencia = request.form.get("vigencia")
        rfc = request.form.get("rfc")
        regimen_patronal = request.form.get("regimen_patronal")
        objeto_servicio = request.form.get("objeto_servicio")

        contacto_nombre = request.form.get("contacto_nombre")
        contacto_telefono = request.form.get("contacto_telefono")
        contacto_correo = request.form.get("contacto_correo")

        conn = get_conn()
        cur = conn.cursor()
        try:
            # OJO: Esto asume que existen estas columnas en usuarios.
            # Si tu tabla no tiene columnas extra, dime y lo adapto.
            cur.execute("""
                INSERT INTO usuarios(
                    nombre, usuario, correo, password, rol, estado,
                    repse_numero, registro_folio, aviso_registro, vigencia,
                    rfc, regimen_patronal, objeto_servicio,
                    contacto_nombre, contacto_telefono, contacto_correo
                )
                VALUES(
                    %s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s
                )
            """, (
                nombre, usuario, correo, password_hash, rol, "pendiente",
                repse_numero, registro_folio, aviso_registro, vigencia,
                rfc, regimen_patronal, objeto_servicio,
                contacto_nombre, contacto_telefono, contacto_correo
            ))
            conn.commit()
            flash("Registro exitoso. Espera aprobación del administrador.")
            return redirect(url_for("login"))
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash("El usuario ya existe.")
        except Exception as e:
            conn.rollback()
            print("Registro error:", e)
            flash("Error registrando. Revisa logs.")
        finally:
            cur.close()
            conn.close()

    return render_template("registro.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ========== ADMIN DASHBOARD (CON FILTROS REALES) ==========
@app.route("/admin/dashboard")
def dashboard_admin():
    if not require_admin():
        flash("Acceso denegado")
        return redirect(url_for("login"))

    # ---- leer filtros (GET)
    provider_ids = request.args.getlist("providers")  # multi-select
    provider_ids_int = []
    for x in provider_ids:
        try:
            provider_ids_int.append(int(x))
        except:
            pass

    year = (request.args.get("year") or "").strip()
    month = (request.args.get("month") or "").strip()
    q = (request.args.get("q") or "").strip()

    selected_year = int(year) if year.isdigit() else None
    selected_month = int(month) if month.isdigit() else None

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    # pendientes
    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente' ORDER BY id DESC")
    pendientes = cur.fetchall()

    # proveedores ALL (para: gestión, datos proveedor, recordatorios, select)
    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2 ORDER BY nombre ASC")
    proveedores_all = cur.fetchall()

    # proveedores filtrados SOLO para pestaña proveedores
    proveedores_filtrados = proveedores_all
    if provider_ids_int:
        proveedores_filtrados = [p for p in proveedores_all if p["id"] in provider_ids_int]

    # -------- query dinámica de projects con filtros reales
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

    # búsqueda
    if q:
        where.append("""
            (
              name ILIKE %s OR
              COALESCE(pedido_no,'') ILIKE %s OR
              CAST(provider_id AS TEXT) ILIKE %s OR
              CAST(id AS TEXT) ILIKE %s
            )
        """)
        like = f"%{q}%"
        params.extend([like, like, like, like])

    sql_projects = "SELECT * FROM projects"
    if where:
        sql_projects += " WHERE " + " AND ".join(where)
    sql_projects += " ORDER BY created_at DESC"

    cur.execute(sql_projects, params)
    proyectos = cur.fetchall()

    # Si hay búsqueda, filtra también proveedores_filtrados por nombre/usuario/correo
    if q:
        ql = q.lower()
        proveedores_filtrados = [
            p for p in proveedores_filtrados
            if ql in (p.get("nombre", "") or "").lower()
            or ql in (p.get("usuario", "") or "").lower()
            or ql in (p.get("correo", "") or "").lower()
        ]

    # documentos SOLO de proyectos visibles (para que el filtro afecte docs)
    project_ids = [pr["id"] for pr in proyectos]
    documentos = []
    if project_ids:
        cur.execute(
            "SELECT * FROM documentos WHERE project_id = ANY(%s) ORDER BY fecha_subida DESC",
            (project_ids,)
        )
        documentos = cur.fetchall()

    cur.close()
    conn.close()

    documentos_por_usuario = {}
    for d in documentos:
        documentos_por_usuario.setdefault(d["usuario_id"], {}).setdefault(d["project_id"], []).append(d)

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,

        proveedores_all=proveedores_all,           # NO filtrado (para gestión, datos, recordatorios)
        proveedores=proveedores_filtrados,         # filtrado (para pestaña proveedores)

        proyectos=proyectos,
        documentos_por_usuario=documentos_por_usuario,

        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url,

        selected_provider_ids=provider_ids_int,
        selected_year=selected_year,
        selected_month=selected_month,
        q=q
    )

# APROBAR / RECHAZAR
@app.route("/admin/accion/<int:id>/<accion>")
def accion(id, accion):
    if not require_admin():
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()

    try:
        if accion == "aprobar":
            cur.execute("UPDATE usuarios SET estado='aprobado' WHERE id=%s", (id,))
        else:
            cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
        conn.commit()
        flash("Operación realizada.")
    except Exception as e:
        conn.rollback()
        print("Accion error:", e)
        flash("Error en operación.")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for("dashboard_admin"))

# DELETE USER (con limpieza S3)
@app.route("/admin/delete_user", methods=["POST"])
def delete_user():
    if not require_admin():
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    user_id = data.get("id")

    if not user_id:
        return jsonify({"success": False, "msg": "Falta id"}), 400

    if int(user_id) == int(session["user_id"]):
        return jsonify({"success": False, "msg": "No puedes borrar tu propia cuenta"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    try:
        # obtener docs para borrar S3
        cur.execute("SELECT ruta FROM documentos WHERE usuario_id=%s", (user_id,))
        docs = cur.fetchall()

        for d in docs:
            try:
                s3.delete_object(Bucket=BUCKET_NAME, Key=d["ruta"])
            except Exception as e:
                print("S3 delete doc error:", e)

        # borrar db
        cur.execute("DELETE FROM documentos WHERE usuario_id=%s", (user_id,))
        cur.execute("DELETE FROM projects WHERE provider_id=%s", (user_id,))
        cur.execute("DELETE FROM usuarios WHERE id=%s", (user_id,))

        conn.commit()
        return jsonify({"success": True, "msg": "Usuario eliminado correctamente"})
    except Exception as e:
        conn.rollback()
        print("Delete user error:", e)
        return jsonify({"success": False, "msg": "Error al eliminar usuario"}), 500
    finally:
        cur.close()
        conn.close()

# DELETE PROJECT (admin)
@app.route("/admin/delete_project", methods=["POST"])
def delete_project():
    if not require_admin():
        return jsonify({"success": False, "msg": "Acceso denegado"}), 403

    data = request.get_json() or {}
    project_id = data.get("project_id")

    if not project_id:
        return jsonify({"success": False, "msg": "Falta project_id"}), 400

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    try:
        # docs del proyecto
        cur.execute("SELECT ruta FROM documentos WHERE project_id=%s", (project_id,))
        docs = cur.fetchall()

        for d in docs:
            try:
                s3.delete_object(Bucket=BUCKET_NAME, Key=d["ruta"])
            except Exception as e:
                print("S3 delete project doc error:", e)

        # borrar docs y proyecto
        cur.execute("DELETE FROM documentos WHERE project_id=%s", (project_id,))
        cur.execute("DELETE FROM projects WHERE id=%s", (project_id,))

        conn.commit()
        return jsonify({"success": True, "msg": "Proyecto eliminado correctamente"})
    except Exception as e:
        conn.rollback()
        print("Delete project error:", e)
        return jsonify({"success": False, "msg": "Error al eliminar proyecto."}), 500
    finally:
        cur.close()
        conn.close()

# Reminder (stub)
@app.route("/admin/send_reminder", methods=["POST"], endpoint="send_reminder")
def send_reminder():
    if not require_admin():
        return jsonify({"success": False, "message": "Acceso denegado"}), 403

    data = request.get_json() or {}
    provider_ids = data.get("provider_ids", [])
    subject = data.get("subject", "Recordatorio REPSE")
    message = data.get("message", "")

    if not provider_ids:
        return jsonify({"success": False, "message": "No providers selected"}), 400

    # Aquí iría tu envío real (Outlook/SMTP)
    return jsonify({"success": True, "sent": len(provider_ids), "subject": subject})

# ========== PROVEEDOR DASHBOARD ==========
@app.route("/proveedor/dashboard", methods=["GET", "POST"])
def dashboard_proveedor():
    if not require_provider():
        flash("Acceso denegado")
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (session["usuario"],))
    user = cur.fetchone()

    if request.method == "POST":
        # subir doc
        if request.form.get("action") == "upload_doc":
            project_id = int(request.form.get("project_id"))
            tipo = request.form.get("tipo_documento")
            archivo = request.files.get("documento")

            if archivo and archivo.filename:
                ext = archivo.filename.rsplit(".", 1)[-1].lower()
                if ext not in ["pdf", "jpg", "jpeg", "png"]:
                    flash("Tipo de archivo no permitido.")
                else:
                    filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{archivo.filename}"

                    s3.upload_fileobj(
                        archivo,
                        BUCKET_NAME,
                        filename,
                        ExtraArgs={"ACL": "private"}
                    )

                    cur.execute("""
                        INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,fecha_subida,project_id)
                        VALUES(%s,%s,%s,%s,NOW(),%s)
                    """, (user["id"], archivo.filename, filename, tipo, project_id))
                    conn.commit()
                    flash("Documento subido correctamente.")

    # Aquí no filtras por mes/año porque el requerimiento de esa parte depende de tu requerimientos.html.
    # Solo listamos proyectos del proveedor:
    cur.execute("SELECT * FROM projects WHERE provider_id=%s ORDER BY created_at DESC", (user["id"],))
    projects = cur.fetchall()

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

    return render_template(
        "dashboard_proveedor.html",
        projects=projects,
        documentos_subidos=documentos_subidos,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS
    )

if __name__ == "__main__":
    app.run()
