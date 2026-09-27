"""Catálogo de clientes por usuario y resolución de coincidencias.

Un cliente es el productor/dueño del campo: un mismo cliente puede tener
varios lotes. Como con los lotes, la resolución "inteligente" (reconocer
que el técnico nombra un cliente ya cargado aunque lo diga distinto) la
hace el LLM en extraccion.py con el catálogo de `listar_clientes_para_prompt`.
"""
from __future__ import annotations

from .db import BaseDeDatos
from .modelos import Cliente, normalizar_texto


async def listar_clientes_para_prompt(db: BaseDeDatos, telegram_user_id: int) -> list[Cliente]:
    filas = await db.listar_clientes(telegram_user_id)
    return [Cliente(id=fila["id"], nombre=fila["nombre"]) for fila in filas]


async def resolver_o_crear_cliente(
    db: BaseDeDatos,
    telegram_user_id: int,
    cliente_id_sugerido: int | None,
    nombre_cliente: str | None,
) -> int | None:
    """Devuelve el id del cliente a asociar a un lote nuevo.

    Si el LLM ya identificó un cliente existente (`cliente_id_sugerido`),
    se valida que pertenezca al usuario y se usa ese. Si no, pero el
    nombre coincide con uno ya cargado (aunque esté escrito distinto), se
    usa ese. Si no hay ninguna coincidencia pero sí un nombre, se crea un
    cliente nuevo. Si no hay nombre de cliente, no hay nada que resolver.
    """
    clientes_usuario = await db.listar_clientes(telegram_user_id)
    if cliente_id_sugerido is not None:
        if any(cliente["id"] == cliente_id_sugerido for cliente in clientes_usuario):
            return cliente_id_sugerido

    if not nombre_cliente:
        return None

    existente = next(
        (c for c in clientes_usuario if normalizar_texto(c["nombre"]) == normalizar_texto(nombre_cliente)),
        None,
    )
    if existente is not None:
        return existente["id"]

    return await db.crear_cliente(telegram_user_id, nombre_cliente)
