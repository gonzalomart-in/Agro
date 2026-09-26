"""Chequeo de permisos y gestión de usuarios permitidos (altas/bajas por admins)."""
from __future__ import annotations

from .db import BaseDeDatos


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
