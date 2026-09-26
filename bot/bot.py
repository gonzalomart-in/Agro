"""Handlers de Telegram: comandos, mensajes de audio/texto, botones inline."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import acceso, exportar, extraccion, lotes as lotes_mod
from .config import Config, get_config
from .db import BaseDeDatos
from .ficha import formatear_ficha
from .modelos import RecorridaCampo
from .transcripcion import transcribir_archivo

logger = logging.getLogger(__name__)

# Estado en memoria de la ficha en curso por usuario (telegram_user_id -> RecorridaCampo)
FICHAS_EN_CURSO: dict[int, RecorridaCampo] = {}
# message_id del último mensaje de ficha con botones, para quitarle los botones al actualizarse
ULTIMO_MENSAJE_FICHA: dict[int, int] = {}


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


def _teclado_ficha() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ Guardar", callback_data="guardar_ficha"),
            InlineKeyboardButton("❌ Descartar", callback_data="descartar_ficha"),
        ]]
    )


def _teclado_borrador() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("Sí, guardar borrador", callback_data="borrador_si"),
            InlineKeyboardButton("No, descartar", callback_data="borrador_no"),
        ]]
    )


async def _quitar_botones_ficha_anterior(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    mensaje_id = ULTIMO_MENSAJE_FICHA.get(chat_id)
    if mensaje_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=mensaje_id, reply_markup=None)
    except Exception:
        pass


async def _mostrar_ficha(update: Update, context: ContextTypes.DEFAULT_TYPE, ficha: RecorridaCampo) -> None:
    chat_id = update.effective_chat.id
    await _quitar_botones_ficha_anterior(context, chat_id)
    mensaje = await update.effective_message.reply_text(
        formatear_ficha(ficha), parse_mode=ParseMode.MARKDOWN, reply_markup=_teclado_ficha()
    )
    ULTIMO_MENSAJE_FICHA[chat_id] = mensaje.message_id


async def _procesar_transcripcion(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    user_id = update.effective_user.id
    db = _db(context)
    config = _config(context)

    ficha_actual = FICHAS_EN_CURSO.get(user_id)
    catalogo_lotes = await lotes_mod.listar_lotes_para_prompt(db, user_id)

    try:
        ficha = await extraccion.extraer_recorrida(
            config, texto, lotes_existentes=catalogo_lotes, ficha_actual=ficha_actual
        )
    except extraccion.ExtraccionError:
        await update.effective_message.reply_text(
            "No pude extraer los datos de la recorrida de ese mensaje. "
            "¿Podés repetirlo de otra forma, mencionando los datos principales?"
        )
        return

    FICHAS_EN_CURSO[user_id] = ficha
    await _mostrar_ficha(update, context, ficha)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await update.message.reply_text(
        "¡Hola! Soy el bot de recorridas a campo. 🌾\n\n"
        "Mandame un audio contando la recorrida (localidad, lote, cultivo, ensayo, "
        "stand, malezas, plagas, enfermedades, umbral de daño, acciones y "
        "comentarios) y te arma la ficha para que la confirmes.\n\n"
        "Ejemplo: \"Estoy en el lote 3, localidad San Justo, cultivo soja... "
        "conté 36 plantas en 5 metros...\"\n\n"
        f"Tu ID de Telegram es `{user_id}` (pedíselo a un administrador si todavía no tenés acceso).\n\n"
        "Comandos: /ayuda /exportar /cancelar /borradores",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    if user_id not in FICHAS_EN_CURSO:
        await update.message.reply_text("No hay ninguna ficha en curso para cancelar.")
        return
    await update.message.reply_text(
        "¿Querés guardar esta ficha incompleta como borrador antes de descartarla?",
        reply_markup=_teclado_borrador(),
    )


async def cmd_borradores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _verificar_acceso(update, context):
        return
    user_id = update.effective_user.id
    db = _db(context)
    borradores = await db.listar_borradores(user_id)
    if not borradores:
        await update.message.reply_text("No tenés borradores guardados.")
        return

    botones = [
        [InlineKeyboardButton(
            f"Borrador #{b['id']} ({b['creado_en'].strftime('%d/%m %H:%M')})",
            callback_data=f"resumir_borrador:{b['id']}",
        )]
        for b in borradores
    ]
    await update.message.reply_text(
        "Tus borradores guardados:", reply_markup=InlineKeyboardMarkup(botones)
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

    with tempfile.TemporaryDirectory() as tmp_dir:
        ruta_local = str(Path(tmp_dir) / "audio.ogg")
        await archivo.download_to_drive(ruta_local)
        texto = await asyncio.to_thread(transcribir_archivo, ruta_local, _config(context))

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
    ficha = FICHAS_EN_CURSO.get(user_id)
    if ficha is None:
        await update.message.reply_text("No hay ninguna ficha en curso para agregarle la ubicación.")
        return
    ubicacion = update.message.location
    ficha.latitud = ubicacion.latitude
    ficha.longitud = ubicacion.longitude
    await _mostrar_ficha(update, context, ficha)


async def manejar_boton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    db = _db(context)

    if not await acceso.tiene_acceso(db, user_id):
        await query.edit_message_text("No tenés acceso a este bot.")
        return

    if query.data == "guardar_ficha":
        ficha = FICHAS_EN_CURSO.pop(user_id, None)
        if ficha is None:
            await query.edit_message_text("Esta ficha ya no está disponible.")
            return
        lote_id = await lotes_mod.resolver_o_crear_lote(
            db, user_id, ficha.lote_id, ficha.lote, ficha.localidad, ficha.cultivo, ficha.ensayo
        )
        registro_id = await db.guardar_recorrida(
            user_id, query.from_user.full_name, ficha, lote_id
        )
        await query.edit_message_text(f"✅ Recorrida guardada (registro #{registro_id}).")
        ULTIMO_MENSAJE_FICHA.pop(query.message.chat_id, None)

    elif query.data == "descartar_ficha":
        FICHAS_EN_CURSO.pop(user_id, None)
        await query.edit_message_text("❌ Ficha descartada.")
        ULTIMO_MENSAJE_FICHA.pop(query.message.chat_id, None)

    elif query.data == "borrador_si":
        ficha = FICHAS_EN_CURSO.pop(user_id, None)
        if ficha is not None:
            await db.guardar_borrador(user_id, ficha.model_dump(mode="json"))
        await query.edit_message_text("💾 Ficha guardada como borrador.")

    elif query.data == "borrador_no":
        FICHAS_EN_CURSO.pop(user_id, None)
        await query.edit_message_text("❌ Ficha descartada sin guardar borrador.")

    elif query.data.startswith("resumir_borrador:"):
        borrador_id = int(query.data.split(":", 1)[1])
        borradores = await db.listar_borradores(user_id)
        registro = next((b for b in borradores if b["id"] == borrador_id), None)
        if registro is None:
            await query.edit_message_text("Ese borrador ya no está disponible.")
            return
        import json as _json

        datos = registro["datos"]
        datos = _json.loads(datos) if isinstance(datos, str) else datos
        ficha = RecorridaCampo.model_validate(datos)
        FICHAS_EN_CURSO[user_id] = ficha
        await db.eliminar_borrador(borrador_id, user_id)
        await query.edit_message_text("Borrador cargado como ficha en curso:")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=formatear_ficha(ficha),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_teclado_ficha(),
        )


async def manejar_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error no manejado", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Ocurrió un error inesperado procesando tu mensaje. Ya quedó registrado en los logs."
        )


async def _post_init(application: Application) -> None:
    config = get_config()
    application.bot_data["config"] = config
    application.bot_data["db"] = await BaseDeDatos.conectar(config)
    logger.info("Bot inicializado y conectado a la base de datos.")


def construir_aplicacion() -> Application:
    config = get_config()
    application = Application.builder().token(config.telegram_bot_token).post_init(_post_init).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("ayuda", cmd_ayuda))
    application.add_handler(CommandHandler("cancelar", cmd_cancelar))
    application.add_handler(CommandHandler("borradores", cmd_borradores))
    application.add_handler(CommandHandler("invitar", cmd_invitar))
    application.add_handler(CommandHandler("revocar", cmd_revocar))
    application.add_handler(CommandHandler("exportar", cmd_exportar))

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
