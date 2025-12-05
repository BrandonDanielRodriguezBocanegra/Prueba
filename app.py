# app.py
import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import psycopg.errors
import boto3
from botocore.exceptions import NoCredentialsError

app = Flask(__name__)
app.secret_key = "supersecret"

# 📌 DB
DATABASE_URL = os.environ.get("DATABASE_URL")
def get_conn():
    return psycopg.connect(DATABASE_URL)

# 📌 AWS S3 CONFIG
USE_S3 = True
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_BUCKET = "repse-documento"

s3_client = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

# Carpeta local como respaldo
UPLOAD_FOLDER = os.path.join(os.getcwd(), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DOCUMENTOS_OBLIGATORIOS = [
    "Cédula fiscal",
    "Identificación oficial",
    "Acta constitutiva",
    "Constancia RFC",
    "Registros IMSS",
    "Comprobantes de nómina",
    "Documentación de capacitación"
]


# ---------------- LOGIN ----------------
@app.route("/", methods=["GET","POST"])
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

            return redirect(url_for("dashboard_admin" if user["rol"]==1 else "dashboard_proveedor"))
        flash("Credenciales incorrectas")
    return render_template("login.html")


# ---------------- REGISTRO ----------------
@app.route("/registro", methods=["GET","POST"])
def registro():
    if request.method == "POST":
        nombre = request.form.get("nombre")
        usuario = request.form.get("usuario")
        correo = request.form.get("correo")
        contrasena = request.form.get("contrasena")
        password_hash = generate_password_hash(contrasena)

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                INSERT INTO usuarios(nombre, usuario, correo, password, rol, estado)
                VALUES(%s,%s,%s,%s,%s,%s)
            """, (nombre, usuario, correo, password_hash, 2, "pendiente"))
            conn.commit()
            flash("Registro exitoso, espera aprobación")
        except:
            conn.rollback()
            flash("Usuario ya existe")
        finally:
            cur.close()
            conn.close()
        return redirect(url_for("login"))
    return render_template("registro.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------- DASHBOARD ADMIN ----------------
@app.route("/admin/dashboard")
def dashboard_admin():
    if session.get("rol") != 1:
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE estado='pendiente'")
    pendientes = cur.fetchall()

    cur.execute("SELECT * FROM usuarios WHERE estado='aprobado' AND rol=2")
    proveedores = cur.fetchall()

    cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
    projects = cur.fetchall()

    documentos_por_usuario = {}
    for p in proveedores:
        cur.execute("""
            SELECT DISTINCT ON (tipo_documento, project_id) *
            FROM documentos WHERE usuario_id=%s
            ORDER BY tipo_documento, fecha_subida DESC
        """, (p["id"],))
        docs = cur.fetchall()
        by_project = {}
        for d in docs:
            pid = d["project_id"]
            by_project.setdefault(pid, []).append(d)
        documentos_por_usuario[p["id"]] = by_project

    cur.close()
    conn.close()

    return render_template("dashboard_admin.html",
        pendientes=pendientes,
        proveedores=proveedores,
        projects=projects,
        documentos_por_usuario=documentos_por_usuario,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS
    )


# ---------------- APROBAR / RECHAZAR ----------------
@app.route("/admin/accion/<int:id>/<accion>")
def accion(id, accion):
    if session.get("rol")!=1:
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor()
    if accion=="aprobar":
        cur.execute("UPDATE usuarios SET estado='aprobado' WHERE id=%s", (id,))
    else:
        cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    conn.commit()
    return redirect(url_for("dashboard_admin"))


# ---------------- DELETE USER ----------------
@app.route("/admin/delete_user", methods=["POST"])
def delete_user():
    data = request.get_json()
    user_id = data.get("id")
    if user_id == session.get("user_id"):
        return jsonify(success=False,msg="No puedes borrarte")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM documentos WHERE usuario_id=%s",(user_id,))
    cur.execute("DELETE FROM projects WHERE provider_id=%s",(user_id,))
    cur.execute("DELETE FROM usuarios WHERE id=%s",(user_id,))
    conn.commit()
    return jsonify(success=True,msg="Usuario eliminado")


# ---------------- DELETE DOC (Proveedor) ----------------
@app.route("/proveedor/delete_doc", methods=["POST"])
def delete_doc():
    data = request.get_json()
    doc_id = data.get("id")

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT ruta FROM documentos WHERE id=%s", (doc_id,))
    row = cur.fetchone()

    if row:
        filename = row["ruta"]

        # borrar de S3 o local
        try:
            if USE_S3:
                s3_client.delete_object(Bucket=AWS_BUCKET, Key=filename)
            else:
                path = os.path.join(UPLOAD_FOLDER, filename)
                if os.path.exists(path):
                    os.remove(path)
        except:
            pass

        cur.execute("DELETE FROM documentos WHERE id=%s",(doc_id,))
        conn.commit()

    return jsonify(success=True,msg="Documento eliminado")


# ---------------- DASHBOARD PROVEEDOR ----------------
@app.route("/proveedor/dashboard", methods=["GET","POST"])
def dashboard_proveedor():
    if session.get("rol")!=2:
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)

    cur.execute("SELECT * FROM usuarios WHERE usuario=%s",(session["usuario"],))
    user = cur.fetchone()

    if request.method == "POST" and request.form.get("action")=="upload_doc":
        project_id = int(request.form.get("project_id"))
        tipo = request.form.get("tipo_documento")
        archivo = request.files.get("documento")

        if archivo and archivo.filename!="":
            filename = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_u{user['id']}_p{project_id}_{archivo.filename}"

            # Eliminar el documento anterior del mismo tipo
            cur.execute("""
                SELECT id,ruta FROM documentos
                WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s
                ORDER BY fecha_subida DESC LIMIT 1
            """, (user["id"], project_id, tipo))
            anterior = cur.fetchone()
            if anterior:
                try:
                    if USE_S3:
                        s3_client.delete_object(Bucket=AWS_BUCKET, Key=anterior["ruta"])
                    else:
                        os.remove(os.path.join(UPLOAD_FOLDER, anterior["ruta"]))
                except:
                    pass
                cur.execute("DELETE FROM documentos WHERE id=%s",(anterior["id"],))
                conn.commit()

            # Subir nuevo
            ruta_archivo = filename
            try:
                if USE_S3:
                    s3_client.upload_fileobj(
                        archivo,
                        AWS_BUCKET,
                        filename,
                        ExtraArgs={"ACL": "public-read"}
                    )
                else:
                    archivo.save(os.path.join(UPLOAD_FOLDER, filename))
            except:
                flash("Error subiendo archivo")
                return redirect(url_for("dashboard_proveedor"))

            cur.execute("""
                INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,fecha_subida,project_id)
                VALUES(%s,%s,%s,%s,NOW(),%s)
            """, (user["id"], archivo.filename, ruta_archivo, tipo, project_id))
            conn.commit()
            flash("Documento actualizado")

        return redirect(url_for("dashboard_proveedor"))

    cur.execute("SELECT * FROM projects WHERE provider_id=%s",(user["id"],))
    projects = cur.fetchall()

    # Solo el más reciente por tipo
    documentos_subidos = {}
    for p in projects:
        documentos_subidos[p["id"]] = {}
        for doc in DOCUMENTOS_OBLIGATORIOS:
            cur.execute("""
                SELECT * FROM documentos
                WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s
                ORDER BY fecha_subida DESC LIMIT 1
            """,(user["id"], p["id"], doc))
            row = cur.fetchone()
            if row:
                documentos_subidos[p["id"]][doc]=row

    cur.close()
    conn.close()

    return render_template("dashboard_proveedor.html",
        projects=projects,
        documentos_subidos=documentos_subidos,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS
    )


# ---------------- DESCARGA ----------------
@app.route("/uploads/<path:filename>")
def descargar(filename):
    if USE_S3:
        try:
            url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket":AWS_BUCKET,"Key":filename},
                ExpiresIn=3600
            )
            return redirect(url)
        except:
            flash("Error descargando")
            return redirect(request.referrer)
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
