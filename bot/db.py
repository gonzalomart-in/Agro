"""Capa de acceso a PostgreSQL (asyncpg)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import asyncpg

from .config import Config
from .modelos import RecorridaCampo

logger = logging.getLogger(__name__)

ESQUEMA_SQL = """
CREATE TABLE IF NOT EXISTS usuarios_permitidos (
    telegram_user_id BIGINT PRIMARY KEY,
    telegram_user_name TEXT,
    es_admin BOOLEAN NOT NULL DEFAULT FALSE,
    agregado_por BIGINT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS lotes (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    nombre TEXT NOT NULL,
    localidad TEXT,
    cultivo_habitual TEXT,
    ensayo_habitual TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS recorridas (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    telegram_user_name TEXT,
    lote_id INTEGER REFERENCES lotes(id),
    fecha_hora TIMESTAMPTZ NOT NULL DEFAULT now(),
    localidad TEXT,
    lote TEXT,
    cultivo TEXT,
    hibrido_variedad TEXT,
    ensayo TEXT,
    tratamiento TEXT,
    estadio_fenologico TEXT,
    stand_valor DOUBLE PRECISION,
    stand_unidad TEXT,
    estado_cultivo TEXT,
    malezas JSONB NOT NULL DEFAULT '[]',
    plagas JSONB NOT NULL DEFAULT '[]',
    enfermedades JSONB NOT NULL DEFAULT '[]',
    umbral_dano_economico TEXT NOT NULL DEFAULT 'no_evaluado',
    acciones TEXT,
    comentarios TEXT,
    latitud DOUBLE PRECISION,
    longitud DOUBLE PRECISION,
    transcripcion_original TEXT
);

CREATE TABLE IF NOT EXISTS borradores (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    datos JSONB NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class BaseDeDatos:
    """Wrapper fino sobre un pool de asyncpg con las queries del bot."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def conectar(cls, config: Config) -> "BaseDeDatos":
        pool = await asyncpg.create_pool(config.database_url)
        instancia = cls(pool)
        await instancia._migrar()
        await instancia._sembrar_admins(config.admin_user_ids)
        return instancia

    async def cerrar(self) -> None:
        await self.pool.close()

    async def _migrar(self) -> None:
        async with self.pool.acquire() as con:
            await con.execute(ESQUEMA_SQL)

    async def _sembrar_admins(self, admin_ids: list[int]) -> None:
        if not admin_ids:
            return
        async with self.pool.acquire() as con:
            for admin_id in admin_ids:
                await con.execute(
                    """
                    INSERT INTO usuarios_permitidos (telegram_user_id, es_admin, agregado_por)
                    VALUES ($1, TRUE, $1)
                    ON CONFLICT (telegram_user_id) DO UPDATE SET es_admin = TRUE
                    """,
                    admin_id,
                )

    # ---- usuarios_permitidos ----

    async def usuario_permitido(self, telegram_user_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as con:
            return await con.fetchrow(
                "SELECT * FROM usuarios_permitidos WHERE telegram_user_id = $1",
                telegram_user_id,
            )

    async def es_admin(self, telegram_user_id: int) -> bool:
        registro = await self.usuario_permitido(telegram_user_id)
        return bool(registro and registro["es_admin"])

    async def invitar_usuario(
        self, telegram_user_id: int, agregado_por: int, telegram_user_name: str | None = None
    ) -> None:
        async with self.pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO usuarios_permitidos (telegram_user_id, telegram_user_name, es_admin, agregado_por)
                VALUES ($1, $2, FALSE, $3)
                ON CONFLICT (telegram_user_id) DO UPDATE
                    SET telegram_user_name = EXCLUDED.telegram_user_name
                """,
                telegram_user_id,
                telegram_user_name,
                agregado_por,
            )

    async def revocar_usuario(self, telegram_user_id: int) -> bool:
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                "DELETE FROM usuarios_permitidos WHERE telegram_user_id = $1",
                telegram_user_id,
            )
            return resultado != "DELETE 0"

    # ---- lotes ----

    async def listar_lotes(self, telegram_user_id: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                "SELECT * FROM lotes WHERE telegram_user_id = $1 ORDER BY nombre",
                telegram_user_id,
            )

    async def crear_lote(
        self,
        telegram_user_id: int,
        nombre: str,
        localidad: str | None = None,
        cultivo_habitual: str | None = None,
        ensayo_habitual: str | None = None,
    ) -> int:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                """
                INSERT INTO lotes (telegram_user_id, nombre, localidad, cultivo_habitual, ensayo_habitual)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                telegram_user_id,
                nombre,
                localidad,
                cultivo_habitual,
                ensayo_habitual,
            )
            return fila["id"]

    # ---- recorridas ----

    async def guardar_recorrida(
        self,
        telegram_user_id: int,
        telegram_user_name: str | None,
        ficha: RecorridaCampo,
        lote_id: int | None,
    ) -> int:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                """
                INSERT INTO recorridas (
                    telegram_user_id, telegram_user_name, lote_id,
                    localidad, lote, cultivo, hibrido_variedad, ensayo, tratamiento, estadio_fenologico,
                    stand_valor, stand_unidad, estado_cultivo,
                    malezas, plagas, enfermedades, umbral_dano_economico,
                    acciones, comentarios, latitud, longitud, transcripcion_original
                ) VALUES (
                    $1, $2, $3,
                    $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13,
                    $14, $15, $16, $17,
                    $18, $19, $20, $21, $22
                )
                RETURNING id
                """,
                telegram_user_id,
                telegram_user_name,
                lote_id,
                ficha.localidad,
                ficha.lote,
                ficha.cultivo,
                ficha.hibrido_variedad,
                ficha.ensayo,
                ficha.tratamiento,
                ficha.estadio_fenologico,
                ficha.stand_valor,
                ficha.stand_unidad.value if ficha.stand_unidad else None,
                ficha.estado_cultivo,
                json.dumps([m.model_dump() for m in ficha.malezas]),
                json.dumps([p.model_dump() for p in ficha.plagas]),
                json.dumps([e.model_dump() for e in ficha.enfermedades]),
                ficha.umbral_dano_economico.value,
                ficha.acciones,
                ficha.comentarios,
                ficha.latitud,
                ficha.longitud,
                ficha.transcripcion_original,
            )
            return fila["id"]

    async def listar_recorridas(
        self, telegram_user_id: int, desde: datetime | None = None
    ) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            if desde is not None:
                return await con.fetch(
                    """
                    SELECT * FROM recorridas
                    WHERE telegram_user_id = $1 AND fecha_hora >= $2
                    ORDER BY fecha_hora
                    """,
                    telegram_user_id,
                    desde,
                )
            return await con.fetch(
                "SELECT * FROM recorridas WHERE telegram_user_id = $1 ORDER BY fecha_hora",
                telegram_user_id,
            )

    # ---- borradores ----

    async def guardar_borrador(self, telegram_user_id: int, datos: dict) -> int:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                "INSERT INTO borradores (telegram_user_id, datos) VALUES ($1, $2) RETURNING id",
                telegram_user_id,
                json.dumps(datos),
            )
            return fila["id"]

    async def listar_borradores(self, telegram_user_id: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                "SELECT * FROM borradores WHERE telegram_user_id = $1 ORDER BY creado_en DESC",
                telegram_user_id,
            )

    async def eliminar_borrador(self, borrador_id: int, telegram_user_id: int) -> None:
        async with self.pool.acquire() as con:
            await con.execute(
                "DELETE FROM borradores WHERE id = $1 AND telegram_user_id = $2",
                borrador_id,
                telegram_user_id,
            )
