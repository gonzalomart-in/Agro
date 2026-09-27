"""Catálogo de lotes por usuario y resolución de coincidencias.

La resolución "inteligente" (reconocer que "Lote 3" y "Lote Tres" son el
mismo lote aunque se los nombre distinto) la hace el LLM en extraccion.py,
que recibe el catálogo devuelto por `listar_lotes_para_prompt` y decide si
el lote mencionado coincide con uno existente (llenando `lote_id` en la
ficha) o si es nuevo (dejando `lote_id` en None).
"""
from __future__ import annotations

from .db import BaseDeDatos
from .modelos import Lote


async def listar_lotes_para_prompt(db: BaseDeDatos, telegram_user_id: int) -> list[Lote]:
    filas = await db.listar_lotes(telegram_user_id)
    return [
        Lote(
            id=fila["id"],
            nombre=fila["nombre"],
            localidad=fila["localidad"],
            cultivo_habitual=fila["cultivo_habitual"],
            ensayo_habitual=fila["ensayo_habitual"],
            cliente_id=fila["cliente_id"],
            cliente=fila["cliente"],
        )
        for fila in filas
    ]


async def resolver_o_crear_lote(
    db: BaseDeDatos,
    telegram_user_id: int,
    lote_id_sugerido: int | None,
    nombre_lote: str | None,
    localidad: str | None,
    cultivo: str | None,
    ensayo: str | None,
    cliente_id: int | None = None,
) -> int | None:
    """Devuelve el id del lote a asociar a la recorrida.

    Si el LLM ya identificó un lote existente (`lote_id_sugerido`), se
    valida que pertenezca al usuario y se usa ese. Si no hay coincidencia
    pero sí un nombre de lote, se crea uno nuevo en el catálogo del
    usuario (con el cliente resuelto, si lo hay). Si no hay nombre de
    lote, no hay nada que resolver.
    """
    if lote_id_sugerido is not None:
        lotes_usuario = await db.listar_lotes(telegram_user_id)
        if any(lote["id"] == lote_id_sugerido for lote in lotes_usuario):
            return lote_id_sugerido

    if not nombre_lote:
        return None

    return await db.crear_lote(
        telegram_user_id,
        nombre=nombre_lote,
        localidad=localidad,
        cultivo_habitual=cultivo,
        ensayo_habitual=ensayo,
        cliente_id=cliente_id,
    )
