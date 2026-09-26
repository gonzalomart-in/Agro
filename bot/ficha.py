"""Cálculo de stand y formateo de la ficha legible para Telegram."""
from __future__ import annotations

from .modelos import RecorridaCampo, UnidadStand

NOMBRES_CAMPOS_CLAVE = {
    "localidad": "Localidad",
    "lote": "Lote",
    "cultivo": "Cultivo",
    "ensayo": "Ensayo",
    "stand_valor": "Stand de plantas",
    "estado_cultivo": "Estado del cultivo",
}


def calcular_stand_pl_m_lineal(cantidad_plantas: float, metros: float) -> float:
    """Calcula plantas por metro lineal a partir de una cantidad de plantas
    contadas sobre una distancia en metros. Ej: 36 plantas en 5 metros -> 7.2."""
    if metros <= 0:
        raise ValueError("Los metros deben ser mayores a 0")
    return round(cantidad_plantas / metros, 2)


def _linea_lista(titulo: str, items: list, formateador) -> str:
    if not items:
        return f"{titulo}: ninguna\n"
    lineas = [f"{titulo}:"]
    for item in items:
        lineas.append(f"  • {formateador(item)}")
    return "\n".join(lineas) + "\n"


def _formatear_maleza(m) -> str:
    partes = [m.nombre]
    if m.tamano:
        partes.append(f"tamaño: {m.tamano}")
    if m.porcentaje_cobertura is not None:
        partes.append(f"cobertura: {m.porcentaje_cobertura}%")
    if m.observacion:
        partes.append(m.observacion)
    return " - ".join(partes)


def _formatear_plaga(p) -> str:
    partes = [p.nombre]
    if p.cantidad_por_metro_lineal is not None:
        partes.append(f"{p.cantidad_por_metro_lineal}/m lineal")
    if p.porcentaje_dano is not None:
        partes.append(f"daño: {p.porcentaje_dano}%")
    if p.observacion:
        partes.append(p.observacion)
    return " - ".join(partes)


def _formatear_enfermedad(e) -> str:
    partes = [e.nombre]
    if e.porcentaje_incidencia is not None:
        partes.append(f"incidencia: {e.porcentaje_incidencia}%")
    if e.severidad:
        partes.append(f"severidad: {e.severidad}")
    if e.observacion:
        partes.append(e.observacion)
    return " - ".join(partes)


def formatear_ficha(ficha: RecorridaCampo) -> str:
    """Genera el texto legible con emojis que se muestra al técnico antes de guardar."""
    faltantes = ficha.campos_clave_faltantes()

    lineas = ["📋 *Ficha de recorrida*\n"]
    lineas.append(f"📍 Localidad: {ficha.localidad or '—'}")
    lineas.append(f"🌾 Lote: {ficha.lote or '—'}")
    lineas.append(f"🌱 Cultivo: {ficha.cultivo or '—'}")
    if ficha.hibrido_variedad:
        lineas.append(f"🧬 Híbrido/variedad: {ficha.hibrido_variedad}")
    lineas.append(f"🧪 Ensayo: {ficha.ensayo or '—'}")
    if ficha.tratamiento:
        lineas.append(f"🔬 Tratamiento: {ficha.tratamiento}")
    if ficha.estadio_fenologico:
        lineas.append(f"📈 Estadio fenológico: {ficha.estadio_fenologico}")

    if ficha.stand_valor is not None:
        unidad = ficha.stand_unidad.value if ficha.stand_unidad else ""
        lineas.append(f"🔢 Stand: {ficha.stand_valor} {unidad}".strip())
    else:
        lineas.append("🔢 Stand: —")

    lineas.append(f"🌿 Estado del cultivo: {ficha.estado_cultivo or '—'}")

    lineas.append("")
    lineas.append(_linea_lista("🌾 Malezas", ficha.malezas, _formatear_maleza).rstrip())
    lineas.append(_linea_lista("🐛 Plagas", ficha.plagas, _formatear_plaga).rstrip())
    lineas.append(_linea_lista("🦠 Enfermedades", ficha.enfermedades, _formatear_enfermedad).rstrip())

    lineas.append("")
    lineas.append(f"⚠️ Umbral de daño económico: {ficha.umbral_dano_economico.value}")
    lineas.append(f"✅ Acciones a realizar: {ficha.acciones or '—'}")
    lineas.append(f"💬 Comentarios: {ficha.comentarios or '—'}")

    if ficha.latitud is not None and ficha.longitud is not None:
        lineas.append(f"🗺️ Ubicación: {ficha.latitud}, {ficha.longitud}")

    if faltantes:
        nombres = ", ".join(NOMBRES_CAMPOS_CLAVE[c] for c in faltantes)
        lineas.append("")
        lineas.append(f"⚠️ Faltan campos clave: {nombres}")

    return "\n".join(lineas)
