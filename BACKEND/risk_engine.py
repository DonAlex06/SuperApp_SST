"""
risk_engine.py
Motor de Correlación y Alertas Automáticas.

Regla de negocio (dada en el enunciado):
    Marcar a un empleado como "Riesgo Alto" si:
      (a) acumula MÁS DE 2 incapacidades en los últimos 60 días   -> >= 3
      O
      (b) presenta síntomas recurrentes en la encuesta de salud   -> >= 2
          encuestas con síntoma distinto de "Sin sintomas" en la misma ventana

Ventana temporal:
    Los últimos 60 días se calculan respecto a una fecha de referencia
    ("hoy simulado"). Por defecto usamos la fecha más reciente presente
    en los datos (ver etl.get_reference_date), pero se puede sobreescribir
    con ?as_of=YYYY-MM-DD en el endpoint para poder demostrar el motor en
    vivo con distintos escenarios.
"""
from datetime import datetime, timedelta

from database import get_connection
from etl import get_reference_date

VENTANA_DIAS = 60
UMBRAL_INCAPACIDADES = 2   # "más de 2" => >= 3
UMBRAL_SINTOMAS = 2        # "recurrentes" => >= 2


def _parse(d):
    return datetime.strptime(d, "%Y-%m-%d").date()


def evaluar_riesgo(conn, as_of: str = None):
    """
    Recalcula el riesgo de TODOS los empleados y sincroniza la tabla
    alertas_riesgo: crea alertas nuevas en estado PENDIENTE para quienes
    cumplen la regla y no tienen ya una alerta abierta, y no toca las
    alertas que el equipo médico ya gestionó.
    """
    if as_of is None:
        as_of = get_reference_date(conn)
    ref = _parse(as_of)
    ventana_inicio = (ref - timedelta(days=VENTANA_DIAS)).isoformat()

    empleados = [r["id_empleado"] for r in conn.execute("SELECT id_empleado FROM empleados")]
    resultados = []

    for emp_id in empleados:
        n_incap = conn.execute(
            """SELECT COUNT(*) as n FROM incapacidades
               WHERE id_empleado = ? AND fecha_inicio BETWEEN ? AND ?""",
            (emp_id, ventana_inicio, as_of),
        ).fetchone()["n"]

        n_sintomas = conn.execute(
            """SELECT COUNT(*) as n FROM encuestas
               WHERE id_empleado = ? AND fecha_encuesta BETWEEN ? AND ?
                     AND sintoma_principal IS NOT NULL
                     AND sintoma_principal != 'Sin sintomas'""",
            (emp_id, ventana_inicio, as_of),
        ).fetchone()["n"]

        gatillo_incap = n_incap > UMBRAL_INCAPACIDADES
        gatillo_sintomas = n_sintomas >= UMBRAL_SINTOMAS

        if gatillo_incap or gatillo_sintomas:
            motivos = []
            if gatillo_incap:
                motivos.append(f"{n_incap} incapacidades en los últimos {VENTANA_DIAS} días (mayor a 2 dias)")
            if gatillo_sintomas:
                motivos.append(f"{n_sintomas} reportes de síntomas recurrentes en los últimos {VENTANA_DIAS} días (mayor o igual a 2 dias)")
            motivo = " y ".join(motivos)

            existente = conn.execute(
                """SELECT id_alerta, estado FROM alertas_riesgo
                   WHERE id_empleado = ? AND estado != 'ATENDIDA'
                   ORDER BY id_alerta DESC LIMIT 1""",
                (emp_id,),
            ).fetchone()

            if existente is None:
                conn.execute(
                    """INSERT INTO alertas_riesgo
                       (id_empleado, fecha_generacion, motivo, nivel_riesgo, estado)
                       VALUES (?, ?, ?, 'ALTO', 'PENDIENTE')""",
                    (emp_id, as_of, motivo),
                )
            else:
                conn.execute(
                    "UPDATE alertas_riesgo SET motivo = ?, fecha_generacion = ? WHERE id_alerta = ?",
                    (motivo, as_of, existente["id_alerta"]),
                )

            resultados.append({
                "id_empleado": emp_id, "riesgo": "ALTO",
                "incapacidades_60d": n_incap, "sintomas_recurrentes_60d": n_sintomas,
                "motivo": motivo,
            })

    conn.commit()
    return {"as_of": as_of, "ventana_dias": VENTANA_DIAS,
            "empleados_en_riesgo_alto": len(resultados), "detalle": resultados}


if __name__ == "__main__":
    conn = get_connection()
    out = evaluar_riesgo(conn)
    print(f"Fecha de referencia (as_of): {out['as_of']}")
    print(f"Empleados en Riesgo Alto: {out['empleados_en_riesgo_alto']}")
    for d in out["detalle"]:
        print(" -", d["id_empleado"], "|", d["motivo"])
    conn.close()
