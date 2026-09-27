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
    "visita_id",
    "fecha_hora",
    "provincia",
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
    "aplicaciones",
    "comentarios",
    "latitud",
    "longitud",
    "transcripcion_original",
]

# hoja "Aplicaciones": un renglón por producto, con los datos de su recorrida
COLUMNAS_APLICACIONES = {
    "recorrida_id": "N° recorrida",
    "fecha_hora": "Fecha",
    "localidad": "Localidad",
    "lote": "Lote",
    "cultivo": "Cultivo",
    "hibrido_variedad": "Híbrido/variedad",
    "estado": "Estado",
    "producto": "Producto",
    "principio_activo": "Principio activo",
    "dosis": "Dosis",
    "unidad": "Unidad",
    "objetivo": "Para",
    "momento": "Momento",
    "coadyuvante": "Coadyuvante",
    "volumen_caldo": "Caldo (l/ha)",
}
_ESTADO_TEXTO = {"recomendada": "A aplicar", "realizada": "Ya aplicado"}


def _aplanar_items(valor, campos: list[str], sin_presencia: bool = False) -> str:
    """Convierte una lista JSONB de items (malezas/plagas/enfermedades) a texto plano.

    Lista vacía: "Sin presencia" si el técnico lo confirmó, o "" si no informó nada.
    """
    items = json.loads(valor) if isinstance(valor, str) else valor
    if not items:
        return "Sin presencia" if sin_presencia else ""
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


def _lista_json(valor) -> list[dict]:
    items = json.loads(valor) if isinstance(valor, str) else valor
    return list(items or [])


def _aplanar_aplicaciones(valor) -> str:
    """'A aplicar: Roundup (glifosato) 2 l/ha, para rama negra; Ya aplicado: atrazina 1 l/ha'."""
    textos = []
    for a in _lista_json(valor):
        texto = str(a.get("producto", ""))
        if a.get("principio_activo") and a["principio_activo"].lower() != texto.lower():
            texto += f" ({a['principio_activo']})"
        if a.get("dosis") is not None:
            texto += f" {a['dosis']:g} {a.get('unidad') or ''}".rstrip()
        extras = [f"para {a['objetivo']}" if a.get("objetivo") else "", a.get("momento") or "",
                  f"+ {a['coadyuvante']}" if a.get("coadyuvante") else "",
                  f"caldo {a['volumen_caldo']:g} l/ha" if a.get("volumen_caldo") is not None else ""]
        texto = ", ".join([texto, *[e for e in extras if e]])
        textos.append(f"{_ESTADO_TEXTO.get(a.get('estado'), 'A aplicar')}: {texto}")
    return "; ".join(textos)


def aplicaciones_a_dataframe(registros: list[dict]) -> pd.DataFrame:
    """Un renglón por producto (recomendado o aplicado), para filtrar y sumar en Excel."""
    filas = []
    for r in registros:
        for a in _lista_json(r.get("aplicaciones")):
            filas.append({
                "recorrida_id": r.get("id"),
                "fecha_hora": _fecha_hora_argentina_sin_tz(r["fecha_hora"]),
                **{c: r.get(c) for c in ("localidad", "lote", "cultivo", "hibrido_variedad")},
                **{c: a.get(c) for c in ("producto", "principio_activo", "dosis", "unidad", "objetivo", "momento",
                                         "coadyuvante", "volumen_caldo")},
                "estado": _ESTADO_TEXTO.get(a.get("estado"), "A aplicar"),
            })
    return pd.DataFrame(filas, columns=list(COLUMNAS_APLICACIONES)).rename(columns=COLUMNAS_APLICACIONES)


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
        fila["malezas"] = _aplanar_items(
            fila.get("malezas"), ["tamano", "porcentaje_cobertura"], bool(fila.get("sin_malezas"))
        )
        fila["plagas"] = _aplanar_items(
            fila.get("plagas"), ["cantidad_por_metro_lineal", "porcentaje_dano"], bool(fila.get("sin_plagas"))
        )
        fila["enfermedades"] = _aplanar_items(
            fila.get("enfermedades"), ["porcentaje_incidencia", "severidad"], bool(fila.get("sin_enfermedades"))
        )
        fila["aplicaciones"] = _aplanar_aplicaciones(fila.get("aplicaciones"))
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
        aplicaciones = aplicaciones_a_dataframe(registros)
        if not aplicaciones.empty:
            aplicaciones.to_excel(writer, index=False, sheet_name="Aplicaciones")
    buffer.seek(0)
    return buffer


def calcular_fecha_desde(dias: int | None) -> datetime | None:
    if dias is None:
        return None
    return datetime.now(ZoneInfo("UTC")) - timedelta(days=dias)
