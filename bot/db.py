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

CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    nombre TEXT NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS clientes_usuario_nombre
    ON clientes (telegram_user_id, lower(nombre));

CREATE TABLE IF NOT EXISTS lotes (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    nombre TEXT NOT NULL,
    localidad TEXT,
    cultivo_habitual TEXT,
    ensayo_habitual TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE lotes ADD COLUMN IF NOT EXISTS cliente_id INTEGER REFERENCES clientes(id);

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
    transcripcion_original TEXT
);

CREATE TABLE IF NOT EXISTS borradores (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    datos JSONB NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS visitas (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    cabecera JSONB NOT NULL,
    abierta BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    cerrada_en TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS visitas_una_abierta_por_usuario
    ON visitas (telegram_user_id) WHERE abierta;

CREATE TABLE IF NOT EXISTS borrador_actual (
    telegram_user_id BIGINT PRIMARY KEY,
    datos JSONB NOT NULL,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalogo (
    id SERIAL PRIMARY KEY,
    tipo TEXT NOT NULL,
    nombre TEXT NOT NULL,
    sinonimos TEXT[] NOT NULL DEFAULT '{}',
    agregado_por BIGINT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS catalogo_tipo_nombre
    ON catalogo (tipo, lower(nombre));

ALTER TABLE recorridas ADD COLUMN IF NOT EXISTS provincia TEXT;
ALTER TABLE recorridas ADD COLUMN IF NOT EXISTS sin_malezas BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE recorridas ADD COLUMN IF NOT EXISTS sin_plagas BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE recorridas ADD COLUMN IF NOT EXISTS sin_enfermedades BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE recorridas ADD COLUMN IF NOT EXISTS visita_id INTEGER REFERENCES visitas(id);
-- productos a aplicar o ya aplicados, con su dosis (lista de bot.modelos.Aplicacion)
ALTER TABLE recorridas ADD COLUMN IF NOT EXISTS aplicaciones JSONB NOT NULL DEFAULT '[]';

-- links de acceso al panel web (se guarda solo la huella del token, nunca el token)
CREATE TABLE IF NOT EXISTS accesos_panel (
    token_hash TEXT PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    vence_en TIMESTAMPTZ NOT NULL
);
"""

# Lo que se puede cambiar desde el panel web (los nombres de columna salen de acá, nunca del usuario).
CAMPOS_RECORRIDA_EDITABLES = (
    "provincia", "localidad", "lote", "cultivo", "ensayo", "tratamiento", "estadio_fenologico",
    "hibrido_variedad", "stand_valor", "estado_cultivo", "umbral_dano_economico", "acciones", "comentarios",
    "malezas", "plagas", "enfermedades", "sin_malezas", "sin_plagas", "sin_enfermedades", "aplicaciones",
)
_CAMPOS_JSON = ("malezas", "plagas", "enfermedades", "aplicaciones")
CAMPOS_LOTE_EDITABLES = ("nombre", "localidad", "cultivo_habitual", "ensayo_habitual", "cliente_id")
CAMPOS_CLIENTE_EDITABLES = ("nombre",)


def _set_de_cambios(cambios: dict, permitidos: tuple[str, ...], primer_parametro: int) -> tuple[str, list]:
    """'col1 = $3, col2 = $4' y sus valores, solo con columnas permitidas."""
    desconocidos = set(cambios) - set(permitidos)
    if desconocidos:
        raise ValueError(f"No se pueden cambiar: {', '.join(sorted(desconocidos))}")
    columnas = list(cambios)
    sets = ", ".join(f"{c} = ${i + primer_parametro}" for i, c in enumerate(columnas))
    valores = [json.dumps(cambios[c]) if c in _CAMPOS_JSON else cambios[c] for c in columnas]
    return sets, valores


class BaseDeDatos:
    """Wrapper fino sobre un pool de asyncpg con las queries del bot."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @classmethod
    async def conectar(cls, config: Config) -> "BaseDeDatos":
        return await cls.conectar_a(config.database_url, config.admin_user_ids)

    @classmethod
    async def conectar_a(cls, database_url: str, admin_user_ids: list[int] | None = None) -> "BaseDeDatos":
        """Como `conectar`, pero sin la configuración del bot (la usa el panel web).

        Neon apaga la base a los 5 minutos sin uso y corta las conexiones abiertas: las que quedan
        sin usar se cierran al minuto, así nunca se usa una conexión ya cortada (la próxima consulta
        abre una nueva y despierta la base)."""
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5, max_inactive_connection_lifetime=60)
        instancia = cls(pool)
        await instancia._migrar()
        await instancia._sembrar_admins(admin_user_ids or [])
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

    # ---- clientes ----

    async def listar_clientes(self, telegram_user_id: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                "SELECT * FROM clientes WHERE telegram_user_id = $1 ORDER BY lower(nombre)",
                telegram_user_id,
            )

    async def crear_cliente(self, telegram_user_id: int, nombre: str) -> int:
        """Crea el cliente, o devuelve el id del que ya existía con ese nombre para ese usuario."""
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                """
                INSERT INTO clientes (telegram_user_id, nombre) VALUES ($1, $2)
                ON CONFLICT (telegram_user_id, lower(nombre)) DO UPDATE SET nombre = clientes.nombre
                RETURNING id
                """,
                telegram_user_id,
                nombre,
            )
            return fila["id"]

    async def listar_clientes_panel(self, de_usuario: int | None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                "SELECT * FROM clientes WHERE ($1::bigint IS NULL OR telegram_user_id = $1) ORDER BY lower(nombre)",
                de_usuario,
            )

    async def actualizar_cliente(self, cliente_id: int, cambios: dict, de_usuario: int | None) -> bool:
        if not cambios:
            return True
        sets, valores = _set_de_cambios(cambios, CAMPOS_CLIENTE_EDITABLES, 3)
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                f"UPDATE clientes SET {sets} WHERE id = $1 AND ($2::bigint IS NULL OR telegram_user_id = $2)",
                cliente_id,
                de_usuario,
                *valores,
            )
        return resultado != "UPDATE 0"

    # ---- lotes ----

    async def listar_lotes(self, telegram_user_id: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                """
                SELECT l.*, c.nombre AS cliente
                FROM lotes l LEFT JOIN clientes c ON c.id = l.cliente_id
                WHERE l.telegram_user_id = $1 ORDER BY l.nombre
                """,
                telegram_user_id,
            )

    async def crear_lote(
        self,
        telegram_user_id: int,
        nombre: str,
        localidad: str | None = None,
        cultivo_habitual: str | None = None,
        ensayo_habitual: str | None = None,
        cliente_id: int | None = None,
    ) -> int:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                """
                INSERT INTO lotes (telegram_user_id, nombre, localidad, cultivo_habitual, ensayo_habitual, cliente_id)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                telegram_user_id,
                nombre,
                localidad,
                cultivo_habitual,
                ensayo_habitual,
                cliente_id,
            )
            return fila["id"]

    # ---- borrador en curso (lo que se va acumulando de los audios, aún sin confirmar) ----

    async def guardar_borrador_actual(self, telegram_user_id: int, datos: dict) -> None:
        async with self.pool.acquire() as con:
            await con.execute(
                """
                INSERT INTO borrador_actual (telegram_user_id, datos) VALUES ($1, $2)
                ON CONFLICT (telegram_user_id) DO UPDATE SET datos = EXCLUDED.datos, actualizado_en = now()
                """,
                telegram_user_id,
                json.dumps(datos),
            )

    async def obtener_borrador_actual(self, telegram_user_id: int) -> dict | None:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                "SELECT datos FROM borrador_actual WHERE telegram_user_id = $1", telegram_user_id
            )
        if fila is None:
            return None
        datos = fila["datos"]
        return json.loads(datos) if isinstance(datos, str) else datos

    async def borrar_borrador_actual(self, telegram_user_id: int) -> None:
        async with self.pool.acquire() as con:
            await con.execute(
                "DELETE FROM borrador_actual WHERE telegram_user_id = $1", telegram_user_id
            )

    # ---- catálogo (vocabulario precargado, compartido por todos los usuarios) ----

    async def agregar_catalogo(
        self, tipo: str, nombre: str, sinonimos: list[str], agregado_por: int | None
    ) -> bool:
        """Agrega una entrada; si ya existía (mismo tipo y nombre) le actualiza los sinónimos.
        Devuelve True si era nueva."""
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                """
                INSERT INTO catalogo (tipo, nombre, sinonimos, agregado_por)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (tipo, lower(nombre)) DO UPDATE
                    SET sinonimos = ARRAY(SELECT DISTINCT unnest(catalogo.sinonimos || EXCLUDED.sinonimos))
                RETURNING (xmax = 0) AS nueva
                """,
                tipo,
                nombre,
                sinonimos,
                agregado_por,
            )
            return fila["nueva"]

    async def agregar_sinonimos(self, tipo: str, nombre: str, sinonimos: list[str]) -> bool:
        """Suma sinónimos a una entrada existente sin pisar los que ya tenía.
        Devuelve False si esa entrada ya no existe."""
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                """
                UPDATE catalogo SET sinonimos = ARRAY(SELECT DISTINCT unnest(sinonimos || $3::text[]))
                WHERE tipo = $1 AND lower(nombre) = lower($2)
                """,
                tipo,
                nombre,
                sinonimos,
            )
            return resultado != "UPDATE 0"

    async def listar_catalogo(self, tipo: str | None = None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            if tipo is None:
                return await con.fetch("SELECT * FROM catalogo ORDER BY tipo, lower(nombre)")
            return await con.fetch(
                "SELECT * FROM catalogo WHERE tipo = $1 ORDER BY lower(nombre)", tipo
            )

    async def listar_localidades_usadas(self) -> list[str]:
        """Localidades de las recorridas ya guardadas (de todos los usuarios): el técnico las vio
        en la ficha antes de confirmar, así que sirven para reconocerlas en los audios siguientes."""
        async with self.pool.acquire() as con:
            filas = await con.fetch(
                "SELECT DISTINCT localidad FROM recorridas WHERE coalesce(trim(localidad), '') <> ''"
            )
        return [fila["localidad"] for fila in filas]

    async def quitar_catalogo(self, tipo: str, nombre: str) -> bool:
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                "DELETE FROM catalogo WHERE tipo = $1 AND lower(nombre) = lower($2)",
                tipo,
                nombre,
            )
            return resultado != "DELETE 0"

    # ---- visitas (un lote abierto = varias recorridas, una por híbrido) ----

    async def abrir_visita(self, telegram_user_id: int, cabecera: dict) -> int:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                "INSERT INTO visitas (telegram_user_id, cabecera) VALUES ($1, $2) RETURNING id",
                telegram_user_id,
                json.dumps(cabecera),
            )
            return fila["id"]

    async def visita_abierta(self, telegram_user_id: int) -> asyncpg.Record | None:
        async with self.pool.acquire() as con:
            return await con.fetchrow(
                "SELECT * FROM visitas WHERE telegram_user_id = $1 AND abierta",
                telegram_user_id,
            )

    async def contar_recorridas_visita(self, visita_id: int) -> int:
        async with self.pool.acquire() as con:
            return await con.fetchval(
                "SELECT count(*) FROM recorridas WHERE visita_id = $1", visita_id
            )

    async def cerrar_visita(self, visita_id: int, telegram_user_id: int) -> int:
        """Cierra la visita y devuelve cuántas recorridas (híbridos) se guardaron en ella."""
        async with self.pool.acquire() as con:
            await con.execute(
                """
                UPDATE visitas SET abierta = FALSE, cerrada_en = now()
                WHERE id = $1 AND telegram_user_id = $2
                """,
                visita_id,
                telegram_user_id,
            )
        return await self.contar_recorridas_visita(visita_id)

    # ---- recorridas ----

    async def guardar_recorrida(
        self,
        telegram_user_id: int,
        telegram_user_name: str | None,
        ficha: RecorridaCampo,
        lote_id: int | None,
        visita_id: int | None = None,
    ) -> int:
        async with self.pool.acquire() as con:
            fila = await con.fetchrow(
                """
                INSERT INTO recorridas (
                    telegram_user_id, telegram_user_name, lote_id,
                    localidad, lote, cultivo, hibrido_variedad, ensayo, tratamiento, estadio_fenologico,
                    stand_valor, stand_unidad, estado_cultivo,
                    malezas, plagas, enfermedades, umbral_dano_economico,
                    acciones, comentarios, transcripcion_original,
                    provincia, sin_malezas, sin_plagas, sin_enfermedades, visita_id, aplicaciones
                ) VALUES (
                    $1, $2, $3,
                    $4, $5, $6, $7, $8, $9, $10,
                    $11, $12, $13,
                    $14, $15, $16, $17,
                    $18, $19, $20,
                    $21, $22, $23, $24, $25, $26
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
                ficha.transcripcion_original,
                ficha.provincia,
                ficha.sin_malezas,
                ficha.sin_plagas,
                ficha.sin_enfermedades,
                visita_id,
                json.dumps([a.model_dump(mode="json") for a in ficha.aplicaciones]),
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

    # ---- panel web ----
    # `de_usuario` limita todo a los registros de ese usuario; None es un administrador (ve todo).

    async def listar_recorridas_panel(
        self, de_usuario: int | None, desde: datetime | None = None
    ) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                """
                SELECT * FROM recorridas
                WHERE ($1::bigint IS NULL OR telegram_user_id = $1)
                  AND ($2::timestamptz IS NULL OR fecha_hora >= $2)
                ORDER BY fecha_hora DESC, id DESC
                """,
                de_usuario,
                desde,
            )

    async def actualizar_recorrida(self, recorrida_id: int, cambios: dict, de_usuario: int | None) -> bool:
        """Devuelve False si la recorrida no existe o no es de ese usuario."""
        if not cambios:
            return True
        sets, valores = _set_de_cambios(cambios, CAMPOS_RECORRIDA_EDITABLES, 3)
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                f"UPDATE recorridas SET {sets} WHERE id = $1 AND ($2::bigint IS NULL OR telegram_user_id = $2)",
                recorrida_id,
                de_usuario,
                *valores,
            )
        return resultado != "UPDATE 0"

    async def eliminar_recorrida(self, recorrida_id: int, de_usuario: int | None) -> bool:
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                "DELETE FROM recorridas WHERE id = $1 AND ($2::bigint IS NULL OR telegram_user_id = $2)",
                recorrida_id,
                de_usuario,
            )
        return resultado != "DELETE 0"

    async def listar_lotes_panel(self, de_usuario: int | None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as con:
            return await con.fetch(
                """
                SELECT l.*, c.nombre AS cliente
                FROM lotes l LEFT JOIN clientes c ON c.id = l.cliente_id
                WHERE ($1::bigint IS NULL OR l.telegram_user_id = $1) ORDER BY lower(l.nombre)
                """,
                de_usuario,
            )

    async def actualizar_lote(self, lote_id: int, cambios: dict, de_usuario: int | None) -> bool:
        if not cambios:
            return True
        sets, valores = _set_de_cambios(cambios, CAMPOS_LOTE_EDITABLES, 3)
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                f"UPDATE lotes SET {sets} WHERE id = $1 AND ($2::bigint IS NULL OR telegram_user_id = $2)",
                lote_id,
                de_usuario,
                *valores,
            )
        return resultado != "UPDATE 0"

    async def nombres_de_usuarios(self) -> dict[int, str]:
        """Nombre para mostrar de cada usuario: el de su última recorrida o, si no tiene, el de la invitación."""
        async with self.pool.acquire() as con:
            invitados = await con.fetch(
                "SELECT telegram_user_id, telegram_user_name FROM usuarios_permitidos WHERE telegram_user_name IS NOT NULL"
            )
            de_recorridas = await con.fetch(
                """
                SELECT DISTINCT ON (telegram_user_id) telegram_user_id, telegram_user_name FROM recorridas
                WHERE telegram_user_name IS NOT NULL ORDER BY telegram_user_id, fecha_hora DESC
                """
            )
        nombres = {f["telegram_user_id"]: f["telegram_user_name"] for f in invitados}
        nombres.update({f["telegram_user_id"]: f["telegram_user_name"] for f in de_recorridas})
        return nombres

    async def actualizar_entrada_catalogo(self, entrada_id: int, nombre: str, sinonimos: list[str]) -> bool:
        """Lanza asyncpg.UniqueViolationError si ya hay otra entrada del mismo tipo con ese nombre."""
        async with self.pool.acquire() as con:
            resultado = await con.execute(
                "UPDATE catalogo SET nombre = $2, sinonimos = $3 WHERE id = $1", entrada_id, nombre, sinonimos
            )
        return resultado != "UPDATE 0"

    async def quitar_catalogo_por_id(self, entrada_id: int) -> bool:
        async with self.pool.acquire() as con:
            resultado = await con.execute("DELETE FROM catalogo WHERE id = $1", entrada_id)
        return resultado != "DELETE 0"

    async def unir_catalogo(self, principal_id: int, sinonimos: list[str], otros_ids: list[int]) -> None:
        """Deja una sola entrada: la principal se queda con `sinonimos` y las otras se borran."""
        async with self.pool.acquire() as con:
            async with con.transaction():
                await con.execute("UPDATE catalogo SET sinonimos = $2 WHERE id = $1", principal_id, sinonimos)
                await con.execute("DELETE FROM catalogo WHERE id = ANY($1::int[]) AND id <> $2", otros_ids, principal_id)

    async def crear_acceso_panel(self, telegram_user_id: int, token_hash: str, vence_en: datetime) -> None:
        async with self.pool.acquire() as con:
            await con.execute("DELETE FROM accesos_panel WHERE vence_en < now()")
            await con.execute(
                "INSERT INTO accesos_panel (token_hash, telegram_user_id, vence_en) VALUES ($1, $2, $3)",
                token_hash,
                telegram_user_id,
                vence_en,
            )

    async def usuario_de_acceso_panel(self, token_hash: str) -> asyncpg.Record | None:
        """El usuario de un acceso vigente, solo si todavía tiene permiso en el bot."""
        async with self.pool.acquire() as con:
            return await con.fetchrow(
                """
                SELECT u.telegram_user_id, u.es_admin, a.vence_en
                FROM accesos_panel a JOIN usuarios_permitidos u USING (telegram_user_id)
                WHERE a.token_hash = $1 AND a.vence_en > now()
                """,
                token_hash,
            )

    async def borrar_acceso_panel(self, token_hash: str) -> None:
        async with self.pool.acquire() as con:
            await con.execute("DELETE FROM accesos_panel WHERE token_hash = $1", token_hash)
