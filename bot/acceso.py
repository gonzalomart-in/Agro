"""Chequeo de permisos, gestión de usuarios permitidos (altas/bajas por admins) y
links de acceso al panel web."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .db import BaseDeDatos

DURACION_ACCESO_PANEL = timedelta(hours=12)


class SinPermisoError(Exception):
    """Se lanza cuando un usuario no admin intenta ejecutar una acción de admin."""


async def tiene_acceso(db: BaseDeDatos, telegram_user_id: int) -> bool:
    registro = await db.usuario_permitido(telegram_user_id)
    return registro is not None


async def es_admin(db: BaseDeDatos, telegram_user_id: int) -> bool:
    return await db.es_admin(telegram_user_id)


async def invitar(
    db: BaseDeDatos,
    solicitante_id: int,
    nuevo_usuario_id: int,
    nuevo_usuario_nombre: str | None = None,
) -> None:
    if not await es_admin(db, solicitante_id):
        raise SinPermisoError("Solo un administrador puede invitar usuarios.")
    await db.invitar_usuario(nuevo_usuario_id, agregado_por=solicitante_id, telegram_user_name=nuevo_usuario_nombre)


async def revocar(db: BaseDeDatos, solicitante_id: int, usuario_id: int) -> bool:
    if not await es_admin(db, solicitante_id):
        raise SinPermisoError("Solo un administrador puede revocar usuarios.")
    return await db.revocar_usuario(usuario_id)


# ---- panel web: se entra con un link personal que da el bot con /panel ----

@dataclass(frozen=True)
class UsuarioPanel:
    telegram_user_id: int
    es_admin: bool
    vence_en: datetime


def _huella(token: str) -> str:
    """En la base se guarda solo la huella del token: quien lea la tabla no puede entrar con eso."""
    return hashlib.sha256(token.encode()).hexdigest()


async def crear_link_panel(db: BaseDeDatos, telegram_user_id: int, panel_url: str) -> str:
    token = secrets.token_urlsafe(32)
    vence_en = datetime.now(timezone.utc) + DURACION_ACCESO_PANEL
    await db.crear_acceso_panel(telegram_user_id, _huella(token), vence_en)
    return f"{panel_url.rstrip('/')}/?acceso={token}"


async def usuario_del_panel(db: BaseDeDatos, token: str | None) -> UsuarioPanel | None:
    """El usuario dueño del link, o None si el link no existe, venció o le sacaron el acceso al bot."""
    if not token:
        return None
    fila = await db.usuario_de_acceso_panel(_huella(token))
    if fila is None:
        return None
    return UsuarioPanel(fila["telegram_user_id"], bool(fila["es_admin"]), fila["vence_en"])


async def cerrar_acceso_panel(db: BaseDeDatos, token: str) -> None:
    await db.borrar_acceso_panel(_huella(token))
