import os
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import psycopg
import psycopg.rows
import psycopg.errors
import boto3

# =============================
# CONFIG
# =============================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")

DATABASE_URL = os.environ.get("DATABASE_URL")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME")

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

# =============================
# DB
# =============================
def get_conn():
    return psycopg.connect(DATABASE_URL)

# =============================
# LOGIN
# =============================
@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario")
        password = request.form.get("contrasena")

        conn = get_conn()
        cur = conn.cursor(row_factory=psycopg.rows.dict_row)
        cur.execute("SELECT * FROM usuarios WHERE usuario=%s", (usuario,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user["password"], password):
            if user["estado"] != "aprobado":
                flash("Tu cuenta está pendiente de aprobación.")
                return redirect(url_for("login"))

            session["usuario"] = user["usuario"]
            session["rol"] = user["rol"]
            session["user_id"] = user["id"]

            if user["rol"] == 1:
                return redirect(url_for("dashboard_admin"))
            else:
                return redirect(url_for("dashboard_proveedor"))
        else:
            flash("Credenciales incorrectas")

    return render_template("login.html")

# =============================
# REGISTRO
# =============================
@app.route("/registro", methods=["GET","POST"])
def registro():
    if request.method == "POST":
        rol = int(request.form.get("rol"))
        password_hash = generate_password_hash(request.form.get("contrasena"))

        # campos base
        data = {
            "nombre": request.form.get("nombre"),
            "usuario": request.form.get("usuario"),
            "correo": request.form.get("correo"),
            "password": password_hash,
            "rol": rol,
            "estado": "pendiente"
        }

        # campos REPSE solo para proveedores
        if rol == 2:
            repse_fields = [
                "repse_numero","repse_folio","repse_aviso","repse_fecha_aviso",
                "repse_vigencia","rfc","regimen_patronal","objeto_servicio",
                "contacto_nombre","contacto_telefono","contacto_correo"
            ]
            for f in repse_fields:
                if not request.form.get(f):
                    flash("Debes llenar todos los datos REPSE")
                    return redirect(url_for("registro"))
                data[f] = request.form.get(f)
        else:
            for f in ["repse_numero","repse_folio","repse_aviso","repse_fecha_aviso","repse_vigencia",
                      "rfc","regimen_patronal","objeto_servicio","contacto_nombre","contacto_telefono","contacto_correo"]:
                data[f] = None

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
            INSERT INTO usuarios(
              nombre,usuario,correo,password,rol,estado,
              repse_numero,repse_folio,repse_aviso,repse_fecha_aviso,repse_vigencia,
              rfc,regimen_patronal,objeto_servicio,contacto_nombre,contacto_telefono,contacto_correo
            )
            VALUES (%(nombre)s,%(usuario)s,%(correo)s,%(password)s,%(rol)s,%(estado)s,
                    %(repse_numero)s,%(repse_folio)s,%(repse_aviso)s,%(repse_fecha_aviso)s,%(repse_vigencia)s,
                    %(rfc)s,%(regimen_patronal)s,%(objeto_servicio)s,%(contacto_nombre)s,%(contacto_telefono)s,%(contacto_correo)s)
            """, data)

            conn.commit()
            flash("Registro exitoso. Espera aprobación.")
            return redirect(url_for("login"))
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            flash("Usuario ya existe")
        finally:
            cur.close()
            conn.close()

    return render_template("registro.html")

# =============================
# LOGOUT
# =============================
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# =============================
# S3
# =============================
def get_presigned_url(key):
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=600
    )

# =============================
# ADMIN
# =============================
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

    cur.execute("SELECT * FROM documentos ORDER BY fecha_subida DESC")
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_user = {}
    for d in docs:
        docs_by_user.setdefault(d["usuario_id"], {}).setdefault(d["project_id"], []).append(d)

    return render_template(
        "dashboard_admin.html",
        pendientes=pendientes,
        proveedores=proveedores,
        proyectos=projects,
        documentos_por_usuario=docs_by_user,
        DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS,
        get_presigned_url=get_presigned_url
    )

# =============================
# PROVEEDOR
# =============================
@app.route("/proveedor/dashboard", methods=["GET","POST"])
def dashboard_proveedor():
    if session.get("rol") != 2:
        return redirect(url_for("login"))

    conn = get_conn()
    cur = conn.cursor(row_factory=psycopg.rows.dict_row)
    cur.execute("SELECT * FROM usuarios WHERE id=%s", (session["user_id"],))
    user = cur.fetchone()

    if request.method == "POST":
        # Crear proyecto
        if request.form.get("action") == "create_project":
            cur.execute("INSERT INTO projects(provider_id,name,created_at) VALUES(%s,%s,NOW())",
                        (user["id"], request.form.get("project_name")))
            conn.commit()

        # Subir o actualizar documento
        if request.form.get("action") == "upload_doc":
            file = request.files["documento"]
            project_id = request.form.get("project_id")
            tipo = request.form.get("tipo_documento")

            key = f"{user['id']}/{datetime.utcnow().timestamp()}_{file.filename}"
            s3.upload_fileobj(file, BUCKET_NAME, key)

            cur.execute("DELETE FROM documentos WHERE usuario_id=%s AND project_id=%s AND tipo_documento=%s",
                        (user["id"], project_id, tipo))

            cur.execute("""
            INSERT INTO documentos(usuario_id,nombre_archivo,ruta,tipo_documento,fecha_subida,project_id)
            VALUES(%s,%s,%s,%s,NOW(),%s)
            """, (user["id"], file.filename, key, tipo, project_id))
            conn.commit()

    cur.execute("SELECT * FROM projects WHERE provider_id=%s", (user["id"],))
    projects = cur.fetchall()

    cur.execute("SELECT * FROM documentos WHERE usuario_id=%s", (user["id"],))
    docs = cur.fetchall()

    cur.close()
    conn.close()

    docs_by_project = {}
    for d in docs:
        docs_by_project.setdefault(d["project_id"], {})[d["tipo_documento"]] = d

    return render_template("dashboard_proveedor.html",
                           projects=projects,
                           documentos_subidos=docs_by_project,
                           DOCUMENTOS_OBLIGATORIOS=DOCUMENTOS_OBLIGATORIOS)

# =============================
# START
# =============================
if __name__ == "__main__":
    app.run()
