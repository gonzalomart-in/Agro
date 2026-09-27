"""Handlers de Telegram: comandos, mensajes de audio/texto, botones inline."""
from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import acceso, catalogo as catalogo_mod, correcciones, exportar, extraccion, lotes as lotes_mod
from .config import Config, get_config
from .db import BaseDeDatos
from .ficha import formatear_borrador, formatear_cabecera, partir_texto, resumen_borrador
from .modelos import CabeceraLote, RecorridaAudio, etiqueta_material
from .transcripcion import precargar_modelo, transcribir_archivo
from .vocabulario_base import VOCABULARIO_BASE

logger = logging.getLogger(__name__)

# message_id del último mensaje con botones de cada chat, para quitárselos cuando llega uno nuevo
ULTIMO_MENSAJE_CON_BOTONES: dict[int, int] = {}


def _db(context: ContextTypes.DEFAULT_TYPE) -> BaseDeDatos:
    return context.bot_data["db"]


def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["config"]


async def _verificar_acceso(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if await acceso.tiene_acceso(_db(context), user_id):
        return True
    await update.message.reply_text(
        "No tenés acceso a este bot todavía. Pedile a un administrador que te "
        f"invite con tu ID de Telegram: `{user_id}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return False


def _teclado_borrador(
    borrador: RecorridaAudio, abierta: CabeceraLote | None, con_ver: bool = True
) -> InlineKeyboardMarkup:
    cantidad = len(borrador.hibridos)
    singular, plural = etiqueta_material(borrador.completar_con(abierta).cultivo)
    if borrador.solo_datos_del_lote():
        etiqueta = "✅ Guardar para todo el lote"
    elif cantidad == 0 and abierta is None:
        etiqueta = "✅ Abrir lote"
    elif cantidad == 1:
        etiqueta = f"✅ Guardar {singular}"
    elif cantidad > 1:
        etiqueta = f"✅ Guardar {cantidad} {plural}"
    else:
        etiqueta = "✅ Guardar"
    fila = [InlineKeyboardButton(etiqueta, callback_data="guardar_ficha")]
    if con_ver:
        fila.append(InlineKeyboardButton("📋 Ver borrador", callback_data="ver_borrador"))
    fila.append(InlineKeyboardButton("❌ Descartar", callback_data="descartar_ficha"))
    return InlineKeyboardMarkup([fila])


def _teclado_confirmar_descarte() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Sí, guardar para después", callback_data="borrador_si"),
            InlineKeyboardButton("No, descartar", callback_data="borrador_no"),
        ]]
    )


def _cabecera_de_visita(visita) -> CabeceraLote:
    datos = visita["cabecera"]
    datos = json.loads(datos) if isinstance(datos, str) else datos
    return CabeceraLote.model_validate(datos)


async def _lote_abierto(db: BaseDeDatos, user_id: int) -> tuple[asyncpg.Record | None, CabeceraLote | None]:
    visita = await db.visita_abierta(user_id)
    return visita, (_cabecera_de_visita(visita) if visita else None)


async def _cargar_borrador(db: BaseDeDatos, user_id: int) -> RecorridaAudio | None:
    datos = await db.obtener_borrador_actual(user_id)
    return RecorridaAudio.model_validate(datos) if datos is not None else None


async def _guardar_borrador_en_curso(db: BaseDeDatos, user_id: int, borrador: RecorridaAudio) -> None:
    await db.guardar_borrador_actual(user_id, borrador.model_dump(mode="json"))


async def _vocabulario(db: BaseDeDatos) -> list[catalogo_mod.EntradaCatalogo]:
    """Lo que cargó la gente con /agregar (para listar y detectar duplicados)."""
    filas = await db.listar_catalogo()
    return [
        catalogo_mod.EntradaCatalogo(f["tipo"], f["nombre"], list(f["sinonimos"])) for f in filas
    ]


async def _vocabulario_completo(db: BaseDeDatos) -> list[catalogo_mod.EntradaCatalogo]:
    """Lo que se usa para procesar audios: primero lo cargado a mano (tiene prioridad), después
    las localidades de recorridas ya guardadas y el vocabulario técnico que el bot ya trae."""
    usadas = [catalogo_mod.EntradaCatalogo("localidad", nombre) for nombre in await db.listar_localidades_usadas()]
    return [*await _vocabulario(db), *usadas, *VOCABULARIO_BASE]


async def _quitar_botones_anteriores(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    mensaje_id = ULTIMO_MENSAJE_CON_BOTONES.get(chat_id)
    if mensaje_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=mensaje_id, reply_markup=None)
    except Exception:
        pass


async def _enviar_con_botones(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    partes: list[str],
    teclado: InlineKeyboardMarkup,
) -> None:
    """Manda uno o más mensajes; los botones van solo en el último."""
    await _quitar_botones_anteriores(context, chat_id)
    mensaje = None
    for i, parte in enumerate(partes):
        es_ultimo = i == len(partes) - 1
        mensaje = await context.bot.send_message(
            chat_id=chat_id, text=parte, reply_markup=teclado if es_ultimo else None
        )
    ULTIMO_MENSAJE_CON_BOTONES[chat_id] = mensaje.message_id


async def _mostrar_resumen(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    borrador: RecorridaAudio,
    agregados: list[str],
    actualizados: list[str],
) -> None:
    _, abierta = await _lote_abierto(_db(context), update.effective_user.id)
    await _enviar_con_botones(
        context,
        update.effective_chat.id,
        partir_texto(resumen_borrador(borrador, abierta, agregados, actualizados)),
        _teclado_borrador(borrador, abierta),
    )


async def _mostrar_borrador_completo(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, borrador: RecorridaAudio
) -> None:
    _, abierta = await _lote_abierto(_db(context), user_id)
    await _enviar_con_botones(
        context,
        chat_id,
        partir_texto(formatear_borrador(borrador, abierta)),
        _teclado_borrador(borrador, abierta, con_ver=False),
    )


async def _procesar_transcripcion(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    """Extrae lo que dice UN audio (o texto) y lo suma al borrador en curso."""
    user_id = update.effective_user.id
    db = _db(context)
    config = _config(context)

    _, abierta = await _lote_abierto(db, user_id)
    catalogo_lotes = await lotes_mod.listar_lotes_para_prompt(db, user_id)
    vocabulario = await _vocabulario_completo(db)

    await update.effective_message.reply_text("🧠 Interpretando los datos... (puede tardar un minuto)")
    try:
        nuevo = await extraccion.extraer_recorrida(
            config,
            texto,
            lotes_existentes=catalogo_lotes,
            cabecera_abierta=abierta,
            vocabulario=vocabulario,
        )
    except extraccion.ExtraccionError:
        logger.exception("Falló la extracción")
        await update.effective_message.reply_text(
            "No pude extraer los datos de ese mensaje. Revisá que Ollama esté corriendo y "
            "tenga el modelo descargado (mirá la ventana del bot). "
            "Si es solo ese audio, probá repetirlo o mandá el texto."
        )
        return

    if not nuevo.tiene_datos():
        await update.effective_message.reply_text(
            "No encontré datos de recorrida en ese mensaje, así que no lo sumé al borrador."
        )
        return

    borrador = await _cargar_borrador(db, user_id) or RecorridaAudio()
    if nuevo.es_otro_lote_que(borrador):
        await update.effective_message.reply_text(
            f"Ese mensaje es del lote «{nuevo.lote}» pero el borrador en curso es del lote "
            f"«{borrador.lote}». No lo sumé.\n"
            "Guardá o descartá el borrador actual (botones o /borrador) y después "
            "reenviá ese audio, o pegá su transcripción como texto."
        )
        return
    agregados, actualizados = borrador.combinar(nuevo)

    await _guardar_borrador_en_curso(db, user_id, borrador)
    await _mostrar_resumen(update, context, borrador, agregados, actualizados)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        "¡Hola! Soy el bot de recorridas a campo. 🌾\n\n"
        "Cómo se usa:\n"
        "1) Mandá uno o varios audios (aunque los grabes sin señal y lleguen todos juntos). "
        "Cada uno se procesa por separado y se suma a un borrador. Podés contar el lote "
        "(provincia, localidad, lote, cultivo, ensayo, estadio) y los híbridos con su stand, "
        "estado, malezas, plagas y enfermedades, hablando libremente. Decí también cuando "
        "NO hay presencia de algo, y qué productos recomendás aplicar (o ya se aplicaron) con su dosis.\n"
        "2) Mirá el borrador con /borrador y corregí lo que haga falta con /corregir.\n"
        "3) Confirmá con el botón verde: se abre el lote y se guardan los híbridos.\n"
        "4) Cuando termines el lote, mandá /cerrar; el próximo audio abre un lote nuevo.\n\n"
        "Ejemplo: \"Localidad Roberts, Buenos Aires. Lote Las Lilas. Maíz, ensayo "
        "comparativo de rendimiento, V2. El 9939 tiene 3 plantas al metro. "
        "En el 9937 tengo 2,9 plantas al metro. No hay enfermedades en ninguno.\"\n\n"
        "Para que entienda mejor tu vocabulario, precargá híbridos, malezas y demás "
        "con /agregar (ver /catalogo).\n\n"
        f"Tu ID de Telegram es `{user_id}` (pedíselo a un administrador si todavía no tenés acceso).\n\n"
        "Comandos: /borrador /corregir /eliminar /lote /cerrar /cancelar /borradores "
        "/agregar /catalogo /quitar /exportar /panel /ayuda",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_borrador(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    borrador = await _cargar_borrador(_db(context), user_id)
    if borrador is None:
        await update.message.reply_text("No hay ningún borrador en curso. Mandá un audio para empezar.")
        return
    await _mostrar_borrador_completo(context, update.effective_chat.id, user_id, borrador)


async def cmd_corregir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)
    borrador = await _cargar_borrador(db, user_id)
    if borrador is None:
        await update.message.reply_text("No hay ningún borrador en curso para corregir.")
        return
    argumentos = update.message.text.partition(" ")[2]
    try:
        mensaje = correcciones.aplicar_correccion(borrador, argumentos)
    except correcciones.ErrorCorreccion as exc:
        await update.message.reply_text(str(exc))
        return
    await _guardar_borrador_en_curso(db, user_id, borrador)
    await update.message.reply_text(mensaje + "\nVer el resultado: /borrador")


async def cmd_eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)
    borrador = await _cargar_borrador(db, user_id)
    if borrador is None:
        await update.message.reply_text("No hay ningún borrador en curso.")
        return
    argumentos = update.message.text.partition(" ")[2]
    try:
        mensaje = correcciones.eliminar_hibrido(borrador, argumentos)
    except correcciones.ErrorCorreccion as exc:
        await update.message.reply_text(str(exc))
        return
    await _guardar_borrador_en_curso(db, user_id, borrador)
    await update.message.reply_text(mensaje + "\nVer el resultado: /borrador")


async def cmd_lote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    db = _db(context)
    visita, abierta = await _lote_abierto(db, update.effective_user.id)
    if visita is None:
        await update.message.reply_text(
            "No hay ningún lote abierto. Mandá un audio con los datos del lote para abrir uno."
        )
        return
    cantidad = await db.contar_recorridas_visita(visita["id"])
    await update.message.reply_text(
        formatear_cabecera(abierta)
        + f"\n\n🧬 {etiqueta_material(abierta.cultivo)[1].capitalize()} guardados en este lote: {cantidad}\n"
        "Mandá más audios del lote, o /cerrar para terminarlo."
    )


async def cmd_cerrar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)
    visita, abierta = await _lote_abierto(db, user_id)
    if visita is None:
        await update.message.reply_text("No hay ningún lote abierto para cerrar.")
        return
    cantidad = await db.cerrar_visita(visita["id"], user_id)
    mensaje = (
        f"🔒 Lote cerrado: {abierta.lote or 'sin nombre'}. "
        f"Se guardaron {cantidad} {etiqueta_material(abierta.cultivo)[0]}(s).\n"
        "El próximo audio que mandes abre un lote nuevo."
    )
    if await db.obtener_borrador_actual(user_id) is not None:
        mensaje += "\nTenés un borrador sin confirmar: si lo guardás, abre un lote nuevo con esos datos."
    await update.message.reply_text(mensaje)


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    if await _db(context).obtener_borrador_actual(update.effective_user.id) is None:
        await update.message.reply_text("No hay ningún borrador en curso para cancelar.")
        return
    await update.message.reply_text(
        "¿Querés guardar este borrador para retomarlo después (con /borradores) antes de descartarlo?",
        reply_markup=_teclado_confirmar_descarte(),
    )


async def cmd_borradores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)
    borradores = await db.listar_borradores(user_id)
    if not borradores:
        await update.message.reply_text("No tenés borradores guardados para después.")
        return

    botones = [
        [InlineKeyboardButton(
            f"Borrador #{b['id']} ({b['creado_en'].strftime('%d/%m %H:%M')})",
            callback_data=f"resumir_borrador:{b['id']}",
        )]
        for b in borradores
    ]
    await update.message.reply_text(
        "Tus borradores guardados para después:", reply_markup=InlineKeyboardMarkup(botones)
    )


async def cmd_agregar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    texto = update.message.text.partition(" ")[2].strip() if update.message.text else ""
    primera_linea, _, resto = texto.partition("\n")
    palabra_tipo, _, resto_primera_linea = primera_linea.strip().partition(" ")
    tipo = catalogo_mod.tipo_valido(palabra_tipo)
    if tipo is None:
        await update.message.reply_text(
            "Uso: /agregar <tipo> <nombre>\n"
            "Tipos: hibrido (o variedad), maleza, plaga, enfermedad, producto, ensayo, localidad, termino, nota\n\n"
            "Podés cargar varios de una vez, uno por línea, y agregar sinónimos con \"=\":\n"
            "/agregar maleza\n"
            "rama negra = conyza, buva\n"
            "yuyo colorado = amaranthus\n\n"
            "Ejemplos: /agregar hibrido 9939 (en soja: /agregar variedad DM46i20)\n"
            "/agregar localidad Rancagua (para que no la escriba mal)\n"
            "/agregar producto Roundup Full II = randap (la marca, y cómo la escribe mal)\n"
            "/agregar nota el testigo es el híbrido 9939\n\n"
            "Para corregir una palabra que Whisper escribe mal, poné la correcta y después "
            "cómo la escribe mal:\n"
            "/agregar termino variedad = válida, valida"
        )
        return

    entradas = catalogo_mod.parsear_entradas(tipo, resto_primera_linea + "\n" + resto)
    if not entradas:
        await update.message.reply_text("No encontré nada para agregar. Escribí el nombre después del tipo.")
        return

    db = _db(context)
    user_id = update.effective_user.id
    existentes = await _vocabulario(db)
    agregadas: list[str] = []
    ya_estaban: list[str] = []
    pendientes: list[dict] = []
    for e in entradas:
        exacta, parecidas = catalogo_mod.buscar_coincidencias(e.tipo, e.nombre, existentes)
        if exacta is not None:
            sinonimos = catalogo_mod.sinonimos_utiles(exacta.nombre, e.sinonimos)
            if sinonimos:
                await db.agregar_sinonimos(e.tipo, exacta.nombre, sinonimos)
            ya_estaban.append(e.nombre if e.nombre == exacta.nombre else f"{e.nombre} (ya está como «{exacta.nombre}»)")
        elif parecidas:
            pendientes.append({"entrada": e, "candidatas": parecidas[:3]})
        else:
            await db.agregar_catalogo(e.tipo, e.nombre, e.sinonimos, user_id)
            existentes.append(e)
            agregadas.append(e.nombre)

    lineas = []
    if agregadas:
        lineas.append(f"✅ Cargué en {catalogo_mod.TITULOS[tipo].lower()}: {', '.join(agregadas)}.")
    if ya_estaban:
        lineas.append(f"ℹ️ Ya estaban cargados (no los dupliqué): {', '.join(ya_estaban)}.")
    if pendientes:
        lineas.append(f"🔎 Me falta confirmar {len(pendientes)}: se parecen a algo que ya hay.")
    if agregadas or ya_estaban:
        lineas.append("Las voy a usar desde el próximo audio.")
    await update.message.reply_text("\n".join(lineas))

    if pendientes:
        context.bot_data.setdefault("pendientes_catalogo", {})[user_id] = pendientes
        await _preguntar_catalogo(context, update.effective_chat.id, user_id)


def _texto_pregunta_catalogo(pendiente: dict) -> str:
    e = pendiente["entrada"]
    parecidas = "\n".join(
        f"  • {c.nombre}" + (f" (también: {', '.join(c.sinonimos)})" if c.sinonimos else "")
        for c in pendiente["candidatas"]
    )
    return (
        f"🔎 Querés cargar «{e.nombre}» ({catalogo_mod.TITULOS[e.tipo].lower()}), pero ya hay algo parecido:\n"
        f"{parecidas}\n\n"
        "¿Es lo mismo escrito de otra forma? Si lo es, lo guardo como sinónimo para que no quede duplicado."
    )


async def _preguntar_catalogo(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> None:
    cola = context.bot_data.get("pendientes_catalogo", {}).get(user_id)
    if not cola:
        return
    pendiente = cola[0]
    botones = [
        [InlineKeyboardButton(f"Sí, es lo mismo que {c.nombre}", callback_data=f"cat_sin:{i}")]
        for i, c in enumerate(pendiente["candidatas"])
    ]
    botones.append([InlineKeyboardButton("No, es distinto: cargarlo aparte", callback_data="cat_nuevo")])
    botones.append([InlineKeyboardButton("Cancelar (no cargar)", callback_data="cat_cancelar")])
    await context.bot.send_message(
        chat_id=chat_id,
        text=_texto_pregunta_catalogo(pendiente),
        reply_markup=InlineKeyboardMarkup(botones),
    )


async def _responder_catalogo(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    db = _db(context)
    cola = context.bot_data.get("pendientes_catalogo", {}).get(user_id)
    if not cola:
        await query.edit_message_text("Esta consulta ya no está disponible.")
        return
    pendiente = cola.pop(0)
    e = pendiente["entrada"]

    if query.data == "cat_cancelar":
        cola.clear()
        await query.edit_message_text(f"Cancelado: no cargué «{e.nombre}» ni lo que quedaba pendiente.")
        return

    if query.data.startswith("cat_sin:"):
        oficial = pendiente["candidatas"][int(query.data.split(":", 1)[1])].nombre
        sinonimos = catalogo_mod.sinonimos_utiles(oficial, [e.nombre, *e.sinonimos])
        if await db.agregar_sinonimos(e.tipo, oficial, sinonimos):
            mensaje = f"✅ «{e.nombre}» quedó como sinónimo de «{oficial}»."
        else:
            await db.agregar_catalogo(e.tipo, e.nombre, e.sinonimos, user_id)
            mensaje = f"«{oficial}» ya no existía, así que cargué «{e.nombre}» como entrada nueva."
    else:
        await db.agregar_catalogo(e.tipo, e.nombre, e.sinonimos, user_id)
        mensaje = f"✅ Cargué «{e.nombre}» como entrada aparte."
    await query.edit_message_text(mensaje)
    await _preguntar_catalogo(context, query.message.chat_id, user_id)


async def cmd_catalogo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    entradas = await _vocabulario(_db(context))
    for parte in partir_texto(catalogo_mod.formatear_listado(entradas)):
        await update.message.reply_text(parte)


async def cmd_quitar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    texto = update.message.text.partition(" ")[2].strip() if update.message.text else ""
    palabra_tipo, _, nombre = texto.partition(" ")
    tipo = catalogo_mod.tipo_valido(palabra_tipo)
    if tipo is None or not nombre.strip():
        await update.message.reply_text("Uso: /quitar <tipo> <nombre>\nEjemplo: /quitar hibrido 9939")
        return
    if await _db(context).quitar_catalogo(tipo, nombre.strip()):
        await update.message.reply_text(f"🗑️ Quité «{nombre.strip()}» del vocabulario.")
    else:
        await update.message.reply_text("No encontré esa entrada. Mirá /catalogo para ver cómo está escrita.")


async def cmd_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    config = _config(context)
    if not config.panel_url:
        await update.message.reply_text("El panel web todavía no está configurado (falta PANEL_URL en el .env del bot).")
        return
    link = await acceso.crear_link_panel(_db(context), update.effective_user.id, config.panel_url)
    horas = int(acceso.DURACION_ACCESO_PANEL.total_seconds() // 3600)
    await update.message.reply_text(
        f"🖥️ Tu acceso al panel web (vale {horas} horas):\n{link}\n\n"
        "Es personal: no lo reenvíes, porque quien lo abra entra como vos. "
        "Cuando venza, pedí otro con /panel.",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def cmd_invitar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    solicitante_id = update.effective_user.id
    db = _db(context)
    if not context.args:
        await update.message.reply_text("Uso: /invitar <id_telegram>")
        return
    try:
        nuevo_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID de Telegram debe ser un número.")
        return
    try:
        await acceso.invitar(db, solicitante_id, nuevo_id)
    except acceso.SinPermisoError:
        await update.message.reply_text("Solo un administrador puede invitar usuarios.")
        return
    await update.message.reply_text(f"Usuario {nuevo_id} invitado correctamente.")


async def cmd_revocar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    solicitante_id = update.effective_user.id
    db = _db(context)
    if not context.args:
        await update.message.reply_text("Uso: /revocar <id_telegram>")
        return
    try:
        usuario_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El ID de Telegram debe ser un número.")
        return
    try:
        revocado = await acceso.revocar(db, solicitante_id, usuario_id)
    except acceso.SinPermisoError:
        await update.message.reply_text("Solo un administrador puede revocar usuarios.")
        return
    if revocado:
        await update.message.reply_text(f"Acceso de {usuario_id} revocado.")
    else:
        await update.message.reply_text(f"El usuario {usuario_id} no estaba en la lista de permitidos.")


async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)

    dias = None
    if context.args:
        try:
            dias = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Uso: /exportar [días]")
            return

    desde = exportar.calcular_fecha_desde(dias)
    registros = await db.listar_recorridas(user_id, desde=desde)
    registros_dict = [dict(r) for r in registros]
    buffer = await asyncio.to_thread(exportar.generar_excel, registros_dict)

    nombre_archivo = f"recorridas_{dias}dias.xlsx" if dias else "recorridas_todas.xlsx"
    await update.message.reply_document(document=buffer, filename=nombre_archivo)


async def manejar_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    archivo_telegram = update.message.voice or update.message.audio
    archivo = await archivo_telegram.get_file()

    await update.message.reply_text("Transcribiendo... 🎙️")

    db = _db(context)
    user_id = update.effective_user.id
    vocabulario = await _vocabulario_completo(db)
    _, abierta = await _lote_abierto(db, user_id)
    borrador = await _cargar_borrador(db, user_id)
    cultivo = (borrador.cultivo if borrador else None) or (abierta.cultivo if abierta else None)
    with tempfile.TemporaryDirectory() as tmp_dir:
        ruta_local = str(Path(tmp_dir) / "audio.ogg")
        await archivo.download_to_drive(ruta_local)
        texto = await asyncio.to_thread(
            transcribir_archivo,
            ruta_local,
            _config(context),
            catalogo_mod.texto_para_whisper(vocabulario, cultivo),
        )
    texto = catalogo_mod.normalizar_transcripcion(texto, vocabulario)

    await update.message.reply_text(f"📝 Transcripción:\n{texto}")
    await _procesar_transcripcion(update, context, texto)


async def manejar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    await _procesar_transcripcion(update, context, update.message.text)


async def manejar_ubicacion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)
    borrador = await _cargar_borrador(db, user_id)
    if borrador is None:
        await update.message.reply_text("No hay ningún borrador en curso para agregarle la ubicación.")
        return
    ubicacion = update.message.location
    borrador.latitud = ubicacion.latitude
    borrador.longitud = ubicacion.longitude
    await _guardar_borrador_en_curso(db, user_id, borrador)
    await update.message.reply_text("📌 Ubicación agregada al borrador.")


async def _guardar_borrador_confirmado(query, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    db = _db(context)
    borrador = await _cargar_borrador(db, user_id)
    if borrador is None:
        await query.edit_message_text("Este borrador ya no está disponible.")
        return

    visita, abierta = await _lote_abierto(db, user_id)
    if borrador.es_otro_lote_que(abierta):
        await query.message.reply_text(
            f"El borrador es del lote «{borrador.lote}» pero el lote abierto es «{abierta.lote}». "
            "Mandá /cerrar y después tocá de nuevo el botón verde (o /borrador)."
        )
        return

    cabecera = borrador.completar_con(abierta)
    if visita is None:
        if not cabecera.lote:
            await query.message.reply_text(
                "No sé de qué lote es esto. Usá /corregir lote <nombre> "
                "(y localidad, cultivo, ensayo si podés) y confirmá de nuevo."
            )
            return
        cabecera.lote_id = await lotes_mod.resolver_o_crear_lote(
            db, user_id, cabecera.lote_id, cabecera.lote, cabecera.localidad, cabecera.cultivo, cabecera.ensayo
        )
        visita_id = await db.abrir_visita(user_id, cabecera.model_dump(mode="json"))
        lote_recien_abierto = True
    else:
        visita_id = visita["id"]
        lote_recien_abierto = False

    nombres = []
    for ficha in borrador.fichas(cabecera):
        await db.guardar_recorrida(
            user_id, query.from_user.full_name, ficha, ficha.lote_id, visita_id
        )
        nombres.append(ficha.hibrido_variedad or "sin nombre")

    await db.borrar_borrador_actual(user_id)
    ULTIMO_MENSAJE_CON_BOTONES.pop(query.message.chat_id, None)

    lineas = []
    if lote_recien_abierto:
        lineas.append(f"📂 Lote abierto: {cabecera.lote}.")
    if nombres and borrador.solo_datos_del_lote():
        total = await db.contar_recorridas_visita(visita_id)
        lineas.append(f"✅ Guardé los datos para todo el lote. Llevás {total} registro(s) en este lote.")
    elif nombres:
        total = await db.contar_recorridas_visita(visita_id)
        lineas.append(
            f"✅ Guardé {len(nombres)} {etiqueta_material(cabecera.cultivo)[0]}(s): {', '.join(nombres)}. "
            f"Llevás {total} en este lote."
        )
    else:
        lineas.append(f"No había {etiqueta_material(cabecera.cultivo)[1]} para guardar.")
    lineas.append("Mandá más audios de este lote o /cerrar para terminarlo.")
    await query.edit_message_text("\n".join(lineas))


async def manejar_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db = _db(context)

    if not await acceso.tiene_acceso(db, user_id):
        await query.edit_message_text("No tenés acceso a este bot.")
        return

    if query.data.startswith("cat_"):
        await _responder_catalogo(query, context, user_id)

    elif query.data == "guardar_ficha":
        await _guardar_borrador_confirmado(query, context, user_id)

    elif query.data == "ver_borrador":
        borrador = await _cargar_borrador(db, user_id)
        if borrador is None:
            await query.edit_message_text("Este borrador ya no está disponible.")
            return
        await _mostrar_borrador_completo(context, query.message.chat_id, user_id, borrador)

    elif query.data == "descartar_ficha":
        await db.borrar_borrador_actual(user_id)
        await query.edit_message_text("❌ Borrador descartado. Si hay un lote abierto, sigue abierto.")
        ULTIMO_MENSAJE_CON_BOTONES.pop(query.message.chat_id, None)

    elif query.data == "borrador_si":
        borrador = await _cargar_borrador(db, user_id)
        if borrador is not None:
            await db.guardar_borrador(user_id, borrador.model_dump(mode="json"))
            await db.borrar_borrador_actual(user_id)
        await query.edit_message_text("💾 Borrador guardado para después (lo retomás con /borradores).")

    elif query.data == "borrador_no":
        await db.borrar_borrador_actual(user_id)
        await query.edit_message_text("❌ Borrador descartado sin guardar.")

    elif query.data.startswith("resumir_borrador:"):
        borrador_id = int(query.data.split(":", 1)[1])
        borradores = await db.listar_borradores(user_id)
        registro = next((b for b in borradores if b["id"] == borrador_id), None)
        if registro is None:
            await query.edit_message_text("Ese borrador ya no está disponible.")
            return

        datos = registro["datos"]
        datos = json.loads(datos) if isinstance(datos, str) else datos
        recuperado = RecorridaAudio.model_validate(datos)
        actual = await _cargar_borrador(db, user_id)
        if actual is None:
            borrador = recuperado
        elif recuperado.es_otro_lote_que(actual):
            await query.edit_message_text(
                f"Ese borrador es del lote «{recuperado.lote}» y el que tenés en curso es del lote "
                f"«{actual.lote}». Guardá o descartá el actual primero."
            )
            return
        else:
            borrador = actual
            borrador.combinar(recuperado)
        await _guardar_borrador_en_curso(db, user_id, borrador)
        await db.eliminar_borrador(borrador_id, user_id)
        await query.edit_message_text("Borrador retomado.")
        await _mostrar_borrador_completo(context, query.message.chat_id, user_id, borrador)


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error no manejado", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Ocurrió un error inesperado procesando tu mensaje. Ya quedó registrado en los logs."
        )


async def _precalentar(config: Config) -> None:
    """Deja listos Whisper y el modelo de Ollama antes del primer audio."""
    try:
        await asyncio.to_thread(precargar_modelo, config)
        logger.info("Whisper '%s' listo.", config.whisper_model)
    except Exception as exc:
        logger.warning("No se pudo precargar Whisper: %s", exc)
    await extraccion.precalentar(config)


async def _post_init(application: Application) -> None:
    config = get_config()
    application.bot_data["config"] = config
    application.bot_data["db"] = await BaseDeDatos.conectar(config)
    logger.info("Bot inicializado y conectado a la base de datos.")
    # se guarda la referencia para que Python no descarte la tarea a mitad de camino
    application.bot_data["precalentamiento"] = asyncio.create_task(_precalentar(config))


def construir_aplicacion() -> Application:
    config = get_config()
    application = Application.builder().token(config.telegram_bot_token).post_init(_post_init).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("ayuda", cmd_ayuda))
    application.add_handler(CommandHandler("borrador", cmd_borrador))
    application.add_handler(CommandHandler("corregir", cmd_corregir))
    application.add_handler(CommandHandler("eliminar", cmd_eliminar))
    application.add_handler(CommandHandler("lote", cmd_lote))
    application.add_handler(CommandHandler("cerrar", cmd_cerrar))
    application.add_handler(CommandHandler("cancelar", cmd_cancelar))
    application.add_handler(CommandHandler("borradores", cmd_borradores))
    application.add_handler(CommandHandler("agregar", cmd_agregar))
    application.add_handler(CommandHandler("catalogo", cmd_catalogo))
    application.add_handler(CommandHandler("quitar", cmd_quitar))
    application.add_handler(CommandHandler("invitar", cmd_invitar))
    application.add_handler(CommandHandler("revocar", cmd_revocar))
    application.add_handler(CommandHandler("exportar", cmd_exportar))
    application.add_handler(CommandHandler("panel", cmd_panel))

    application.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, manejar_audio))
    application.add_handler(MessageHandler(filters.LOCATION, manejar_ubicacion))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    application.add_handler(CallbackQueryHandler(manejar_boton))

    application.add_error_handler(manejar_error)
    return application


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    application = construir_aplicacion()
    application.run_polling()


if __name__ == "__main__":
    main()
