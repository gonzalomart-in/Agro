"""Exportación de recorridas a Excel (pandas + openpyxl)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

import pandas as pd

ZONA_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")

COLUMNAS_ORDEN = [
    "id",
    "fecha_hora",
    "localidad",
    "lote",
    "cultivo",
    "hibrido_variedad",
    "ensayo",
    "tratamiento",
    "estadio_fenologico",
    "stand_valor",
    "stand_unidad",
    "estado_cultivo",
    "malezas",
    "plagas",
    "enfermedades",
    "umbral_dano_economico",
    "acciones",
    "comentarios",
    "latitud",
    "longitud",
    "transcripcion_original",
]


def _aplanar_items(valor, campos: list[str]) -> str:
    """Convierte una lista JSONB de items (malezas/plagas/enfermedades) a texto plano."""
    if valor is None:
        return ""
    items = json.loads(valor) if isinstance(valor, str) else valor
    if not items:
        return ""
    lineas = []
    for item in items:
        partes = [str(item.get("nombre", ""))]
        for campo in campos:
            v = item.get(campo)
            if v is not None and v != "":
                partes.append(f"{campo}={v}")
        if item.get("observacion"):
            partes.append(item["observacion"])
        lineas.append(" | ".join(partes))
    return "; ".join(lineas)


def _fecha_hora_argentina_sin_tz(fecha_hora: datetime) -> datetime:
    """Convierte a hora de Argentina y le quita el tzinfo (openpyxl no admite datetimes con zona horaria)."""
    if fecha_hora.tzinfo is None:
        fecha_hora = fecha_hora.replace(tzinfo=ZoneInfo("UTC"))
    return fecha_hora.astimezone(ZONA_ARGENTINA).replace(tzinfo=None)


def registros_a_dataframe(registros: list[dict]) -> pd.DataFrame:
    filas = []
    for r in registros:
        fila = dict(r)
        fila["fecha_hora"] = _fecha_hora_argentina_sin_tz(fila["fecha_hora"])
        fila["malezas"] = _aplanar_items(fila.get("malezas"), ["tamano", "porcentaje_cobertura"])
        fila["plagas"] = _aplanar_items(fila.get("plagas"), ["cantidad_por_metro_lineal", "porcentaje_dano"])
        fila["enfermedades"] = _aplanar_items(fila.get("enfermedades"), ["porcentaje_incidencia", "severidad"])
        filas.append(fila)

    df = pd.DataFrame(filas)
    if df.empty:
        df = pd.DataFrame(columns=COLUMNAS_ORDEN)
    else:
        df = df[[c for c in COLUMNAS_ORDEN if c in df.columns]]
    return df


def generar_excel(registros: list[dict]) -> BytesIO:
    df = registros_a_dataframe(registros)
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Recorridas")
    buffer.seek(0)
    return buffer


def calcular_fecha_desde(dias: int | None) -> datetime | None:
    if dias is None:
        return None
    return datetime.now(ZoneInfo("UTC")) - timedelta(days=dias)
