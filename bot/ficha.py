"""Cálculo de stand y formateo de las fichas legibles para Telegram."""
from __future__ import annotations

from .modelos import Aplicacion, CabeceraLote, EstadoAplicacion, Hibrido, RecorridaAudio, etiqueta_material, normalizar_texto

NOMBRES_CAMPOS_CLAVE = {
    "localidad": "Localidad",
    "lote": "Lote",
    "cultivo": "Cultivo",
    "ensayo": "Ensayo",
    "hibrido_variedad": "Híbrido/variedad",
    "stand_valor": "Stand de plantas",
    "estado_cultivo": "Estado del cultivo",
}

LIMITE_MENSAJE_TELEGRAM = 3500


def calcular_stand_pl_m_lineal(cantidad_plantas: float, metros: float) -> float:
    """Calcula plantas por metro lineal a partir de una cantidad de plantas
    contadas sobre una distancia en metros. Ej: 36 plantas en 5 metros -> 7.2."""
    if metros <= 0:
        raise ValueError("Los metros deben ser mayores a 0")
    return round(cantidad_plantas / metros, 2)


def _linea_lista(titulo: str, items: list, formateador, confirmado_ausente: bool) -> str:
    if not items:
        if confirmado_ausente:
            return f"{titulo}: ✅ sin presencia (confirmado)"
        return f"{titulo}: ❓ no informado"
    lineas = [f"{titulo}:"]
    for item in items:
        lineas.append(f"  • {formateador(item)}")
    return "\n".join(lineas)


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


def _producto_con_activo(a: Aplicacion) -> str:
    """'Roundup (glifosato)'; si el producto ya es el principio activo, solo 'glifosato'."""
    if a.principio_activo and normalizar_texto(a.principio_activo) != normalizar_texto(a.producto):
        return f"{a.producto} ({a.principio_activo})"
    return a.producto


def _dosis(a: Aplicacion) -> str:
    if a.dosis is None:
        return "dosis ❓"
    return f"{_numero(a.dosis)} {a.unidad or ''}".strip()


def formatear_aplicacion(a: Aplicacion) -> str:
    """'A aplicar: Roundup (glifosato) 2 l/ha · para rama negra · en presiembra · caldo 80 l/ha'."""
    partes = [f"{_producto_con_activo(a)} {_dosis(a)}"]
    if a.objetivo:
        partes.append(f"para {a.objetivo}")
    if a.momento:
        partes.append(a.momento)
    if a.coadyuvante:
        partes.append(f"+ {a.coadyuvante}")
    if a.volumen_caldo is not None:
        partes.append(f"caldo {_numero(a.volumen_caldo)} l/ha")
    estado = "Ya aplicado" if a.estado == EstadoAplicacion.REALIZADA else "A aplicar"
    return f"{estado}: " + " · ".join(partes)


def _corto_aplicacion(a: Aplicacion) -> str:
    texto = a.producto if a.dosis is None else f"{a.producto} {_dosis(a)}"
    return texto + (" (ya aplicado)" if a.estado == EstadoAplicacion.REALIZADA else "")


def _lineas_cabecera(cabecera: CabeceraLote) -> list[str]:
    lineas = [
        f"🗺️ Provincia: {cabecera.provincia or '—'}",
        f"📍 Localidad: {cabecera.localidad or '—'}",
        f"🌾 Lote: {cabecera.lote or '—'}",
        f"🌱 Cultivo: {cabecera.cultivo or '—'}",
        f"🧪 Ensayo: {cabecera.ensayo or '—'}",
    ]
    if cabecera.estadio_fenologico:
        lineas.append(f"📈 Estadio fenológico: {cabecera.estadio_fenologico}")
    if cabecera.latitud is not None and cabecera.longitud is not None:
        lineas.append(f"📌 Ubicación: {cabecera.latitud}, {cabecera.longitud}")
    return lineas


def _aviso_cabecera(cabecera: CabeceraLote) -> list[str]:
    faltantes = cabecera.campos_cabecera_faltantes()
    if not faltantes:
        return []
    nombres = ", ".join(NOMBRES_CAMPOS_CLAVE[c] for c in faltantes)
    return [f"⚠️ Faltan datos del lote: {nombres}"]


def formatear_cabecera(cabecera: CabeceraLote) -> str:
    """Datos del lote, sin híbridos (para /lote)."""
    lineas = ["📋 Datos del lote\n"]
    lineas.extend(_lineas_cabecera(cabecera))
    avisos = _aviso_cabecera(cabecera)
    if avisos:
        lineas.append("")
        lineas.extend(avisos)
    return "\n".join(lineas)


def formatear_hibrido(hibrido: Hibrido, numero: int, etiqueta: str = "híbrido") -> str:
    """Bloque de texto con los datos de un híbrido (o variedad, según el cultivo)."""
    lineas = [f"🧬 {etiqueta.capitalize()} {numero}: {hibrido.hibrido_variedad or '— (no dijo cuál)'}"]
    if hibrido.tratamiento:
        lineas.append(f"🔬 Tratamiento: {hibrido.tratamiento}")
    if hibrido.stand_valor is not None:
        unidad = hibrido.stand_unidad.value if hibrido.stand_unidad else ""
        lineas.append(f"🔢 Stand: {hibrido.stand_valor} {unidad}".strip())
    else:
        lineas.append("🔢 Stand: —")
    lineas.append(f"🌿 Estado del cultivo: {hibrido.estado_cultivo or '—'}")
    lineas.append(_linea_lista("🌾 Malezas", hibrido.malezas, _formatear_maleza, hibrido.sin_malezas))
    lineas.append(_linea_lista("🐛 Plagas", hibrido.plagas, _formatear_plaga, hibrido.sin_plagas))
    lineas.append(
        _linea_lista(
            "🦠 Enfermedades", hibrido.enfermedades, _formatear_enfermedad, hibrido.sin_enfermedades
        )
    )
    lineas.append(f"⚠️ Umbral de daño económico: {hibrido.umbral_dano_economico.value}")
    if hibrido.acciones:
        lineas.append(f"✅ Acciones a realizar: {hibrido.acciones}")
    if hibrido.aplicaciones:
        lineas.append("🧴 Productos:")
        lineas.extend(f"  • {formatear_aplicacion(a)}" for a in hibrido.aplicaciones)
    if hibrido.comentarios:
        lineas.append(f"💬 Comentarios: {hibrido.comentarios}")

    avisos = []
    faltantes = hibrido.campos_hibrido_faltantes()
    if faltantes:
        avisos.append("Faltan: " + ", ".join(NOMBRES_CAMPOS_CLAVE[c] for c in faltantes))
    sin_informar = hibrido.relevamientos_sin_informar()
    if sin_informar:
        avisos.append(f"Falta confirmar si hay o no hay: {', '.join(sin_informar)}")
    for aviso in avisos:
        lineas.append(f"⚠️ {aviso}")
    return "\n".join(lineas)


def formatear_borrador(
    audio: RecorridaAudio, abierta: CabeceraLote | None = None
) -> str:
    """Todo lo entendido de un audio, para confirmar antes de guardar.

    `abierta` es el lote ya abierto (si hay): sus datos mandan sobre los del audio.
    """
    cabecera = audio.completar_con(abierta)
    singular, plural = etiqueta_material(cabecera.cultivo)
    lineas = []
    if abierta is not None:
        lineas.append(f"📂 Lote abierto (los datos siguientes se aplican a todos los {plural})\n")
    else:
        lineas.append("📋 Datos del lote (se va a abrir un lote nuevo al guardar)\n")
    lineas.extend(_lineas_cabecera(cabecera))
    lineas.extend(_aviso_cabecera(cabecera))

    if audio.es_otro_lote_que(abierta):
        lineas.append("")
        lineas.append(
            f"⚠️ El borrador es del lote «{audio.lote}» pero el lote abierto es «{abierta.lote}». "
            "Mandá /cerrar y después confirmá esto para abrir el lote nuevo."
        )

    if not audio.hibridos:
        lineas.append("")
        lineas.append(f"Todavía no hay ningún {singular} en el borrador.")
        generales = _linea_generales(audio, con_productos=False)
        if generales:
            lineas.append(generales)
        if audio.aplicaciones:
            lineas.append("🧴 Productos para todo el lote:")
            lineas.extend(f"  • {formatear_aplicacion(a)}" for a in audio.aplicaciones)
    for numero, hibrido in enumerate(audio.hibridos_efectivos(), start=1):
        lineas.append("")
        lineas.append(formatear_hibrido(hibrido, numero, singular))
    return "\n".join(lineas)


_UNIDAD_CORTA = {"pl/m lineal": "pl/m", "pl/m2": "pl/m²", "pl/ha": "pl/ha"}


def _numero(valor: float) -> str:
    return f"{valor:g}"


def _corto_maleza(m) -> str:
    texto = m.nombre
    if m.porcentaje_cobertura is not None:
        texto += f" {_numero(m.porcentaje_cobertura)}%"
    if m.tamano:
        texto += f" ({m.tamano})"
    return texto


def _corto_plaga(p) -> str:
    texto = p.nombre
    if p.cantidad_por_metro_lineal is not None:
        texto += f" {_numero(p.cantidad_por_metro_lineal)}/m"
    if p.porcentaje_dano is not None:
        texto += f" daño {_numero(p.porcentaje_dano)}%"
    return texto


def _corto_enfermedad(e) -> str:
    texto = e.nombre
    if e.porcentaje_incidencia is not None:
        texto += f" {_numero(e.porcentaje_incidencia)}%"
    if e.severidad:
        texto += f" ({e.severidad})"
    return texto


def _resumen_de_lista(items: list, corto, confirmado_ausente: bool) -> str:
    if items:
        return ", ".join(corto(i) for i in items)
    return "✅ no hay" if confirmado_ausente else "❓"


def _recortar(texto: str, largo: int = 40) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


def _linea_hibrido_compacta(numero: int, h: Hibrido, marca: str = "") -> str:
    """Una línea con todo lo cargado de un híbrido: para revisar sin abrir el borrador."""
    if h.stand_valor is not None:
        unidad = _UNIDAD_CORTA.get(h.stand_unidad.value, h.stand_unidad.value) if h.stand_unidad else ""
        stand = f"stand {_numero(h.stand_valor)} {unidad}".strip()
    else:
        stand = "stand ❓"
    partes = [
        stand,
        f"estado {h.estado_cultivo}" if h.estado_cultivo else "estado ❓",
        "🌾 " + _resumen_de_lista(h.malezas, _corto_maleza, h.sin_malezas),
        "🐛 " + _resumen_de_lista(h.plagas, _corto_plaga, h.sin_plagas),
        "🦠 " + _resumen_de_lista(h.enfermedades, _corto_enfermedad, h.sin_enfermedades),
    ]
    if h.umbral_dano_economico.value != "no_evaluado":
        partes.append(f"umbral {h.umbral_dano_economico.value}")
    if h.acciones:
        partes.append(f"acciones: {_recortar(h.acciones)}")
    if h.aplicaciones:
        partes.append("🧴 " + ", ".join(_corto_aplicacion(a) for a in h.aplicaciones))
    if h.comentarios:
        partes.append(f"💬 {_recortar(h.comentarios)}")
    return f"{numero}. {marca}{h.hibrido_variedad or 'sin nombre'} — " + " · ".join(partes)


def _linea_generales(audio: RecorridaAudio, con_productos: bool = True) -> str:
    """Lo que el técnico dijo del lote entero (vale para los híbridos que no informaron lo suyo)."""
    partes = []
    for icono, items, corto, sin in (
        ("🌾", audio.malezas, _corto_maleza, audio.sin_malezas),
        ("🐛", audio.plagas, _corto_plaga, audio.sin_plagas),
        ("🦠", audio.enfermedades, _corto_enfermedad, audio.sin_enfermedades),
    ):
        if items or sin:
            partes.append(f"{icono} {_resumen_de_lista(items, corto, sin)}")
    if audio.acciones:
        partes.append(f"acciones: {_recortar(audio.acciones)}")
    if audio.aplicaciones and con_productos:
        partes.append("🧴 " + ", ".join(_corto_aplicacion(a) for a in audio.aplicaciones))
    if audio.comentarios:
        partes.append(f"💬 {_recortar(audio.comentarios)}")
    return ("🌐 Para todo el lote: " + " · ".join(partes)) if partes else ""


def resumen_borrador(
    audio: RecorridaAudio,
    abierta: CabeceraLote | None,
    agregados: list[str],
    actualizados: list[str],
) -> str:
    """Resumen corto que se muestra después de cada audio (el detalle está en /borrador)."""
    cabecera = audio.completar_con(abierta)
    singular, plural = etiqueta_material(cabecera.cultivo)
    hibridos = audio.hibridos_efectivos()

    lineas = []
    novedades = []
    if agregados:
        novedades.append(f"+{len(agregados)} {singular}(s): {', '.join(agregados)}")
    if actualizados:
        novedades.append(f"actualicé: {', '.join(actualizados)}")
    lineas.append("🎙️ Audio procesado" + (f" — {'; '.join(novedades)}" if novedades else "."))

    lugar = ", ".join(x for x in (cabecera.localidad, cabecera.provincia) if x)
    detalle_lote = " · ".join(x for x in (lugar, cabecera.cultivo, cabecera.ensayo) if x)
    prefijo = "📂 Lote abierto" if abierta is not None else "📋 Lote"
    lineas.append(f"{prefijo}: {cabecera.lote or '—'}" + (f" ({detalle_lote})" if detalle_lote else ""))
    if abierta is None and audio.lote:
        lineas[-1] += " — se abre al guardar"

    generales = _linea_generales(audio)
    if generales:
        lineas.append(generales)

    if hibridos:
        lineas.append(f"🧬 Borrador ({len(hibridos)} {singular}(s)):")
        for numero, hibrido in enumerate(hibridos, start=1):
            nombre = hibrido.hibrido_variedad or "sin nombre"
            marca = "🆕 " if nombre in agregados else "✏️ " if nombre in actualizados else ""
            lineas.append(_linea_hibrido_compacta(numero, hibrido, marca))
    else:
        lineas.append(f"🧬 Borrador: todavía sin {plural}")

    avisos = list(_aviso_cabecera(cabecera))
    if audio.es_otro_lote_que(abierta):
        avisos.append(
            f"⚠️ El borrador es del lote «{audio.lote}» pero el lote abierto es «{abierta.lote}»: "
            "mandá /cerrar antes de guardar."
        )
    sin_datos = [h for h in hibridos if h.campos_hibrido_faltantes()]
    if sin_datos:
        avisos.append(f"⚠️ {plural.capitalize()} con datos incompletos (stand, estado o nombre): {len(sin_datos)}")
    for lista in ("malezas", "plagas", "enfermedades"):
        cantidad = sum(1 for h in hibridos if lista in h.relevamientos_sin_informar())
        if cantidad:
            avisos.append(f"⚠️ Falta confirmar si hay o no hay {lista} en {cantidad} {singular}(s)")
    lineas.extend(avisos)
    if any(a.startswith("⚠️ Falta confirmar") for a in avisos):
        lineas.append("Decilo en un audio (\"no hay plagas en ninguno\") o usá /corregir todos sin plagas.")

    lineas.append("")
    lineas.append(f"Ver todo: /borrador · Corregir: /corregir · Sacar uno: /eliminar")
    return "\n".join(lineas)


def partir_texto(texto: str, limite: int = LIMITE_MENSAJE_TELEGRAM) -> list[str]:
    """Parte un texto largo en mensajes de hasta `limite` caracteres, cortando entre bloques."""
    partes: list[str] = []
    actual = ""
    for bloque in texto.split("\n\n"):
        candidato = f"{actual}\n\n{bloque}" if actual else bloque
        if len(candidato) <= limite:
            actual = candidato
            continue
        if actual:
            partes.append(actual)
        actual = bloque
    if actual:
        partes.append(actual)
    return partes or [texto]
