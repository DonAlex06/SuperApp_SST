"""
Pipeline de Extracción-Transformación-Carga (ETL) para las 3 fuentes crudas de SST.

  1. Estandarización de IDs y nombres (empleados, áreas, EPS)
  2. Procedimiento de fechas heterogéneas + filtrado de fechas inválidas
  3. Conversión de tipos mixtos (días de ausencia, escala de dolor, sí/no)
  4. El enmascaramiento de diagnósticos NO ocurre aquí: se guarda el dato
     limpio en bruto en la BD, y el enmascaramiento se aplica en la capa
     API (routers) según el rol autenticado. Esto es intencional: la BD
     debe conservar el dato real para el rol MEDICO; el control de acceso
     vive en el backend de la API, no en el ETL.
"""
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from database import get_connection, init_schema

DATA_DIR = Path(__file__).parent / "data"

# ---------------------------------------------------------------------------
# Mapeo de campo
# ---------------------------------------------------------------------------

AREA_MAP = {
    "comercial": "Comercial",
    "finanzas": "Finanzas",
    "it": "Tecnología",
    "sistemas": "Tecnología",
    "tecnologia": "Tecnología",
    "logistica": "Logística",
    "operaciones": "Operaciones",
    "ops": "Operaciones",
    "rrhh": "Recursos Humanos",
    "recursos humanos": "Recursos Humanos",
    "ventas": "Ventas",
}

EPS_MAP = {
    "eps sura": "EPS Sura",
    "sura eps": "EPS Sura",
    "sanitas": "Sanitas",
    "compensar": "Compensar",
    "particular": "Particular",
}

DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"]

DOLOR_TEXT_MAP = {"bajo": 2, "medio": 5, "alto": 8}

SI_VALUES = {"1", "s", "si", "sí"}
NO_VALUES = {"0", "n", "no"}


def normalize_employee_id(raw) -> str:
    """EMP-001 / EMP1 / emp-1 -> 'EMP-001' (3 dígitos, con guion)."""
    if pd.isna(raw):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        return None
    return f"EMP-{int(digits):03d}"


def normalize_area(raw) -> tuple:
    """Devuelve (area_canonica, area_original)."""
    if pd.isna(raw):
        return "Sin Área", None
    original = str(raw).strip()
    key = original.lower().strip()
    canon = AREA_MAP.get(key, original.title())
    return canon, original


def parse_date(raw):
    """Intenta varios formatos de fecha; devuelve date o None si es inválida."""
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_dias_ausencia(raw):
    """Extrae el entero de campos mixtos: '2 dias', '3 Días', '15', NaN -> None."""
    if pd.isna(raw):
        return None
    match = re.search(r"\d+", str(raw))
    if not match:
        return None
    return int(match.group())


def normalize_eps(raw):
    if pd.isna(raw) or str(raw).strip() == "":
        return "No especificado"
    key = str(raw).strip().lower()
    return EPS_MAP.get(key, str(raw).strip())


def normalize_dolor(raw):
    """Escala 0-10 mixta con texto Alto/Medio/Bajo -> entero 0-10 o None."""
    if pd.isna(raw):
        return None
    s = str(raw).strip().lower()
    if s in DOLOR_TEXT_MAP:
        return DOLOR_TEXT_MAP[s]
    try:
        val = int(float(s))
        return max(0, min(10, val))
    except ValueError:
        return None


def normalize_si_no(raw):
    """8 codificaciones distintas de sí/no -> 1 / 0 / None (sin dato)."""
    if pd.isna(raw):
        return None
    s = str(raw).strip().lower()
    if s in SI_VALUES:
        return 1
    if s in NO_VALUES:
        return 0
    return None


# ---------------------------------------------------------------------------
# Carga de cada fuente
# ---------------------------------------------------------------------------

def load_empleados(conn) -> dict:
    df = pd.read_csv(DATA_DIR / "RAW_BD_EMPLEADOS.csv")
    leidos = len(df)
    rows = []
    descartados = 0
    for _, r in df.iterrows():
        emp_id = normalize_employee_id(r["ID_Empleado"])
        area, area_orig = normalize_area(r["Area_Trabajo"])
        fecha_ing = parse_date(r["Fecha_Ingreso"])
        if emp_id is None:
            descartados += 1
            continue
        rows.append((
            emp_id,
            str(r["Nombre_Completo"]).strip(),
            area,
            area_orig,
            str(r["Cargo"]).strip(),
            fecha_ing.isoformat() if fecha_ing else None,
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO empleados
           (id_empleado, nombre_completo, area_trabajo, area_original, cargo, fecha_ingreso)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return {"fuente": "RAW_BD_EMPLEADOS.csv", "leidos": leidos,
            "cargados": len(rows), "descartados": descartados}


def load_incapacidades(conn) -> dict:
    df = pd.read_csv(DATA_DIR / "RAW_HISTORICO_INCAPACIDADES_CONFIDENCIAL.csv")
    leidos = len(df)
    empleados_validos = {
        row["id_empleado"] for row in conn.execute("SELECT id_empleado FROM empleados")
    }
    rows = []
    descartados = 0
    descartados_fk = 0
    for _, r in df.iterrows():
        emp_id = normalize_employee_id(r["EMPLEADO_REF"])
        fecha_inicio = parse_date(r["FECHA_INICIO_INCAPACIDAD"])
        # Regla: filtrar registros con fechas erróneas o referencia de empleado ilegible
        if emp_id is None or fecha_inicio is None:
            descartados += 1
            continue
        # Integridad referencial: el ID normaliza correctamente pero no existe
        # en el maestro de empleados (ej. EMP-101..EMP-120 fuera de rango).
        if emp_id not in empleados_validos:
            descartados += 1
            descartados_fk += 1
            continue

        dias = parse_dias_ausencia(r["DIAS_AUSENCIA"])
        dias_estimado = 0
        if dias is None:
            dias = 1  # imputación conservadora: al menos día reportado
            dias_estimado = 1

        fecha_fin = fecha_inicio + timedelta(days=dias - 1)

        cie10 = None if pd.isna(r["CODIGO_CIE10"]) else str(r["CODIGO_CIE10"]).strip()
        diagnostico = str(r["DIAGNOSTICO_MEDICO_CONFIDENCIAL"]).strip()
        categoria = None if pd.isna(r["CATEGORIA_SALUD"]) else str(r["CATEGORIA_SALUD"]).strip()
        entidad = normalize_eps(r["ENTIDAD_EXPEDIDORA"])

        rows.append((
            str(r["COD_REGISTRO"]).strip(),
            emp_id,
            fecha_inicio.isoformat(),
            fecha_fin.isoformat(),
            dias,
            dias_estimado,
            cie10,
            diagnostico,
            categoria,
            entidad,
            None,  # estado_caso se calcula luego (depende del "as_of" de referencia)
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO incapacidades
           (cod_registro, id_empleado, fecha_inicio, fecha_fin, dias_ausencia,
            dias_estimados, codigo_cie10, diagnostico_medico_confidencial,
            categoria_salud, entidad_expedidora, estado_caso)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return {"fuente": "RAW_HISTORICO_INCAPACIDADES_CONFIDENCIAL.csv", "leidos": leidos,
            "cargados": len(rows), "descartados": descartados,
            "descartados_fk_empleado_inexistente": descartados_fk}


def load_encuestas(conn) -> dict:
    df = pd.read_csv(DATA_DIR / "RAW_ENCUESTAS_SINTOMAS_PELIGROS.csv")
    leidos = len(df)
    empleados_validos = {
        row["id_empleado"] for row in conn.execute("SELECT id_empleado FROM empleados")
    }
    rows = []
    descartados = 0
    for _, r in df.iterrows():
        emp_id = normalize_employee_id(r["CODIGO_EMPLEADO"])
        fecha = parse_date(r["FECHA_ENCUESTA"])
        if emp_id is None or fecha is None or emp_id not in empleados_validos:
            descartados += 1
            continue
        rows.append((
            str(r["ID_RESPUESTA"]).strip(),
            emp_id,
            fecha.isoformat(),
            None if pd.isna(r["SINTOMA_PRINCIPAL"]) else str(r["SINTOMA_PRINCIPAL"]).strip(),
            None if pd.isna(r["PELIGRO_IDENTIFICADO"]) else str(r["PELIGRO_IDENTIFICADO"]).strip(),
            normalize_dolor(r["NIVEL_DOLOR_PERCIBIDO"]),
            normalize_si_no(r["REQUIERE_VALORACION_MEDICA"]),
        ))
    conn.executemany(
        """INSERT OR REPLACE INTO encuestas
           (id_respuesta, id_empleado, fecha_encuesta, sintoma_principal,
            peligro_identificado, nivel_dolor_percibido, requiere_valoracion_medica)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    return {"fuente": "RAW_ENCUESTAS_SINTOMAS_PELIGROS.csv", "leidos": leidos,
            "cargados": len(rows), "descartados": descartados}


def get_reference_date(conn) -> str:
    """
    'Hoy' simulado para el motor de reglas = fecha más reciente presente en
    los datos (incapacidades o encuestas). Se documenta como asunción porque
    el dataset sintético termina en el pasado respecto al reloj real.
    """
    row = conn.execute("""
        SELECT MAX(fecha) as max_fecha FROM (
            SELECT fecha_inicio as fecha FROM incapacidades
            UNION ALL
            SELECT fecha_encuesta as fecha FROM encuestas
        )
    """).fetchone()
    return row["max_fecha"]


def compute_estado_casos(conn):
    """Marca cada incapacidad como ACTIVO (aún cubre la fecha de referencia) o CERRADO."""
    ref = get_reference_date(conn)
    conn.execute("""
        UPDATE incapacidades
        SET estado_caso = CASE WHEN fecha_fin >= ? THEN 'ACTIVO' ELSE 'CERRADO' END
    """, (ref,))
    conn.commit()


def log_etl(conn, resultado: dict):
    detalle = ""
    if resultado.get("descartados_fk_empleado_inexistente"):
        detalle = (f"{resultado['descartados_fk_empleado_inexistente']} descartados por "
                   f"referencia a empleado inexistente en el maestro")
    conn.execute(
        """INSERT INTO etl_log (fuente, registros_leidos, registros_cargados,
           registros_descartados, detalle, ejecutado_en)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (resultado["fuente"], resultado["leidos"], resultado["cargados"],
         resultado["descartados"], detalle, datetime.now().isoformat()),
    )
    conn.commit()


def run_etl(reset: bool = True) -> list:
    init_schema(reset=reset)
    conn = get_connection()
    resultados = []
    for loader in (load_empleados, load_incapacidades, load_encuestas):
        res = loader(conn)
        log_etl(conn, res)
        resultados.append(res)
    compute_estado_casos(conn)
    conn.close()
    return resultados


if __name__ == "__main__":
    for r in run_etl(reset=True):
        print(f"{r['fuente']:55s} leidos={r['leidos']:4d}  "
              f"cargados={r['cargados']:4d}  descartados={r['descartados']:3d}")
