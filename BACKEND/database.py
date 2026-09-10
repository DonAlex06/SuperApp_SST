"""
database.py
Capa de acceso a datos: conexión SQLite y creación de esquema.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "sst.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS empleados (
    id_empleado TEXT PRIMARY KEY,
    nombre_completo TEXT NOT NULL,
    area_trabajo TEXT NOT NULL,
    area_original TEXT,
    cargo TEXT,
    fecha_ingreso TEXT
);

CREATE TABLE IF NOT EXISTS incapacidades (
    cod_registro TEXT PRIMARY KEY,
    id_empleado TEXT NOT NULL,
    fecha_inicio TEXT NOT NULL,
    fecha_fin TEXT,
    dias_ausencia INTEGER,
    dias_estimados INTEGER,        -- 1 si dias_ausencia fue imputado por dato faltante
    codigo_cie10 TEXT,
    diagnostico_medico_confidencial TEXT NOT NULL,  -- SOLO se expone a rol MEDICO
    categoria_salud TEXT,
    entidad_expedidora TEXT,
    estado_caso TEXT,              -- 'ACTIVO' | 'CERRADO' (calculado tras la carga)
    FOREIGN KEY (id_empleado) REFERENCES empleados(id_empleado)
);

CREATE TABLE IF NOT EXISTS encuestas (
    id_respuesta TEXT PRIMARY KEY,
    id_empleado TEXT NOT NULL,
    fecha_encuesta TEXT NOT NULL,
    sintoma_principal TEXT,
    peligro_identificado TEXT,
    nivel_dolor_percibido INTEGER,
    requiere_valoracion_medica INTEGER,  -- 0/1, NULL si sin dato
    FOREIGN KEY (id_empleado) REFERENCES empleados(id_empleado)
);

CREATE TABLE IF NOT EXISTS alertas_riesgo (
    id_alerta INTEGER PRIMARY KEY AUTOINCREMENT,
    id_empleado TEXT NOT NULL,
    fecha_generacion TEXT NOT NULL,
    motivo TEXT NOT NULL,              -- explicación de la regla disparada
    nivel_riesgo TEXT NOT NULL,        -- 'ALTO'
    estado TEXT NOT NULL DEFAULT 'PENDIENTE',  -- 'PENDIENTE' | 'AGENDADA' | 'ATENDIDA'
    fecha_agendamiento TEXT,
    FOREIGN KEY (id_empleado) REFERENCES empleados(id_empleado)
);

CREATE TABLE IF NOT EXISTS etl_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fuente TEXT,
    registros_leidos INTEGER,
    registros_cargados INTEGER,
    registros_descartados INTEGER,
    detalle TEXT,
    ejecutado_en TEXT
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_schema(reset: bool = False):
    if reset and DB_PATH.exists():
        DB_PATH.unlink()
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
