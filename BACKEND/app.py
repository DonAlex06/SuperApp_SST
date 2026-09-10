"""
app.py
API REST de la SuperApp SST.

Endpoints:
  POST /api/auth/login              -> autenticación, devuelve JWT
  GET  /api/dashboard/resumen        -> Pilar C: métricas, varía según rol
  GET  /api/casos                    -> Pilar A: listado, enmascarado según rol
  GET  /api/alertas                  -> Pilar B: alertas de riesgo alto
  POST /api/alertas/<id>/agendar     -> agenda examen médico (solo MEDICO)
  POST /api/etl/ejecutar             -> re-corre ETL + motor de riesgo (solo MEDICO/admin)
"""
from datetime import datetime, date

from flask import Flask, request, jsonify, g, send_from_directory

from database import get_connection, init_schema
from etl import run_etl, get_reference_date
from risk_engine import evaluar_riesgo
from security import authenticate, create_token, require_auth, ROLE_HRBP, ROLE_MEDICO

app = Flask(__name__, static_folder="static", static_url_path="")


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = body.get("username", "")
    password = body.get("password", "")
    user = authenticate(username, password)
    if not user:
        return jsonify({"error": "Usuario o contraseña incorrectos"}), 401
    token = create_token(username, user["role"])
    return jsonify({
        "token": token,
        "role": user["role"],
        "nombre": user["nombre"],
        "expira_en_minutos": 60,
    })


@app.get("/api/auth/me")
@require_auth()
def me():
    return jsonify(g.user)


# ---------------------------------------------------------------------------
# Dashboard (según rol autenticado)
# ---------------------------------------------------------------------------

@app.get("/api/dashboard/resumen")
@require_auth()
def dashboard_resumen():
    conn = get_connection()
    ref = get_reference_date(conn)

    por_area = conn.execute("""
        SELECT e.area_trabajo AS area,
               COUNT(DISTINCT e.id_empleado) AS total_empleados,
               COALESCE(SUM(i.dias_ausencia), 0) AS dias_ausencia_totales,
               SUM(CASE WHEN i.estado_caso = 'ACTIVO' THEN 1 ELSE 0 END) AS casos_activos,
               SUM(CASE WHEN i.estado_caso = 'CERRADO' THEN 1 ELSE 0 END) AS casos_cerrados
        FROM empleados e
        LEFT JOIN incapacidades i ON i.id_empleado = e.id_empleado
        GROUP BY e.area_trabajo
        ORDER BY e.area_trabajo
    """).fetchall()

    alertas_por_area = conn.execute("""
        SELECT e.area_trabajo AS area, COUNT(*) AS n
        FROM alertas_riesgo a
        JOIN empleados e ON e.id_empleado = a.id_empleado
        WHERE a.estado != 'ATENDIDA'
        GROUP BY e.area_trabajo
    """).fetchall()
    alertas_map = {r["area"]: r["n"] for r in alertas_por_area}

    total_casos_activos = conn.execute(
        "SELECT COUNT(*) AS n FROM incapacidades WHERE estado_caso='ACTIVO'").fetchone()["n"]
    total_casos_cerrados = conn.execute(
        "SELECT COUNT(*) AS n FROM incapacidades WHERE estado_caso='CERRADO'").fetchone()["n"]
    total_alertas = conn.execute(
        "SELECT COUNT(*) AS n FROM alertas_riesgo WHERE estado != 'ATENDIDA'").fetchone()["n"]
    total_empleados = conn.execute("SELECT COUNT(*) AS n FROM empleados").fetchone()["n"]

    areas_payload = []
    for r in por_area:
        areas_payload.append({
            "area": r["area"],
            "total_empleados": r["total_empleados"],
            "dias_ausencia_totales": r["dias_ausencia_totales"],
            "pct_ausentismo": round(
                (r["dias_ausencia_totales"] / (r["total_empleados"] * 180)) * 100, 2
            ) if r["total_empleados"] else 0,
            "casos_activos": r["casos_activos"],
            "casos_cerrados": r["casos_cerrados"],
            "alertas_riesgo_alto": alertas_map.get(r["area"], 0),
        })

    payload = {
        "as_of": ref,
        "rol": g.user["role"],
        "totales": {
            "empleados": total_empleados,
            "casos_activos": total_casos_activos,
            "casos_cerrados": total_casos_cerrados,
            "alertas_riesgo_alto": total_alertas,
        },
        "por_area": areas_payload,
    }

    # HRBP: SOLO agregados por área. Nunca se incluyen nombres, diagnósticos
    # ni ningún identificador de empleado individual en esta respuesta.
    if g.user["role"] == ROLE_HRBP:
        conn.close()
        return jsonify(payload)

    # MEDICO / Admin SST: además incluye el detalle de empleados en riesgo.
    detalle_riesgo = conn.execute("""
        SELECT a.id_alerta, a.id_empleado, e.nombre_completo, e.area_trabajo,
               a.motivo, a.estado, a.fecha_generacion
        FROM alertas_riesgo a
        JOIN empleados e ON e.id_empleado = a.id_empleado
        WHERE a.estado != 'ATENDIDA'
        ORDER BY a.fecha_generacion DESC
    """).fetchall()
    payload["alertas_detalle"] = [dict(r) for r in detalle_riesgo]
    conn.close()
    return jsonify(payload)


# ---------------------------------------------------------------------------
# Casos (incapacidades) — enmascaramiento estricto por rol
# ---------------------------------------------------------------------------

@app.get("/api/casos")
@require_auth()
def listar_casos():
    conn = get_connection()
    area_filtro = request.args.get("area")

    query = """
        SELECT i.cod_registro, i.id_empleado, e.nombre_completo, e.area_trabajo,
               i.fecha_inicio, i.fecha_fin, i.dias_ausencia, i.estado_caso,
               i.categoria_salud, i.codigo_cie10,
               i.diagnostico_medico_confidencial, i.entidad_expedidora
        FROM incapacidades i
        JOIN empleados e ON e.id_empleado = i.id_empleado
    """
    params = []
    if area_filtro:
        query += " WHERE e.area_trabajo = ?"
        params.append(area_filtro)
    query += " ORDER BY i.fecha_inicio DESC"

    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    if g.user["role"] == ROLE_HRBP:
        # RBAC estricto: se eliminan por completo los campos clínicos y
        # el detalle de diagnóstico antes de que el dato salga del backend.
        # No se envía "null" en el campo: el campo NO EXISTE en el JSON.
        for r in rows:
            r.pop("diagnostico_medico_confidencial", None)
            r.pop("codigo_cie10", None)
            r.pop("categoria_salud", None)
            r.pop("nombre_completo", None)  # HRBP ve agregados, no identidad individual
            r.pop("id_empleado", None)

    return jsonify({"rol": g.user["role"], "total": len(rows), "casos": rows})


# ---------------------------------------------------------------------------
# Alertas de Riesgo Alto
# ---------------------------------------------------------------------------

@app.get("/api/alertas")
@require_auth()
def listar_alertas():
    conn = get_connection()
    rows = conn.execute("""
        SELECT a.id_alerta, a.id_empleado, e.nombre_completo, e.area_trabajo,
               a.fecha_generacion, a.motivo, a.nivel_riesgo, a.estado,
               a.fecha_agendamiento
        FROM alertas_riesgo a
        JOIN empleados e ON e.id_empleado = a.id_empleado
        ORDER BY a.estado = 'ATENDIDA' ASC, a.fecha_generacion DESC
    """).fetchall()
    conn.close()

    result = [dict(r) for r in rows]

    if g.user["role"] == ROLE_HRBP:
        # HRBP solo ve el conteo agregado por área, jamás el detalle nominal.
        conteo_por_area = {}
        for r in result:
            conteo_por_area[r["area_trabajo"]] = conteo_por_area.get(r["area_trabajo"], 0) + 1
        return jsonify({
            "rol": g.user["role"],
            "total_alertas": len(result),
            "alertas_por_area": conteo_por_area,
        })

    return jsonify({"rol": g.user["role"], "total_alertas": len(result), "alertas": result})


@app.post("/api/alertas/<int:id_alerta>/agendar")
@require_auth(allowed_roles=[ROLE_MEDICO])
def agendar_alerta(id_alerta):
    body = request.get_json(silent=True) or {}
    fecha_examen = body.get("fecha_examen")
    if not fecha_examen:
        return jsonify({"error": "Se requiere 'fecha_examen' (YYYY-MM-DD)"}), 400
    try:
        datetime.strptime(fecha_examen, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Formato de fecha inválido, use YYYY-MM-DD"}), 400

    conn = get_connection()
    cur = conn.execute(
        "UPDATE alertas_riesgo SET estado='AGENDADA', fecha_agendamiento=? WHERE id_alerta=?",
        (fecha_examen, id_alerta),
    )
    conn.commit()
    if cur.rowcount == 0:
        conn.close()
        return jsonify({"error": "Alerta no encontrada"}), 404
    row = conn.execute("SELECT * FROM alertas_riesgo WHERE id_alerta=?", (id_alerta,)).fetchone()
    conn.close()
    return jsonify(dict(row))


# ---------------------------------------------------------------------------
# Administración: re-ejecutar ETL + motor de riesgo
# ---------------------------------------------------------------------------

@app.post("/api/etl/ejecutar")
@require_auth(allowed_roles=[ROLE_MEDICO])
def ejecutar_etl():
    resultados = run_etl(reset=True)
    conn = get_connection()
    riesgo = evaluar_riesgo(conn)
    conn.close()
    return jsonify({"etl": resultados, "riesgo": riesgo})


@app.get("/api/etl/log")
@require_auth(allowed_roles=[ROLE_MEDICO])
def etl_log():
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM etl_log ORDER BY id DESC").fetchall()]
    conn.close()
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


def bootstrap():
    """Inicializa esquema + carga ETL + motor de riesgo si la BD no existe."""
    import os
    from database import DB_PATH
    if not os.path.exists(DB_PATH):
        print("Base de datos no encontrada. Ejecutando ETL inicial...")
        run_etl(reset=True)
        conn = get_connection()
        evaluar_riesgo(conn)
        conn.close()
        print("ETL inicial completado.")


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=8000, debug=True)
