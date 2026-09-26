"""Tests de lotes.py usando una base de datos falsa en memoria (no requiere Postgres)."""
import pytest

from bot import lotes as lotes_mod


class BaseDeDatosFalsa:
    def __init__(self):
        self._siguiente_id = 1
        self.lotes: dict[int, list[dict]] = {}

    async def listar_lotes(self, telegram_user_id: int):
        return self.lotes.get(telegram_user_id, [])

    async def crear_lote(self, telegram_user_id, nombre, localidad=None, cultivo_habitual=None, ensayo_habitual=None):
        lote_id = self._siguiente_id
        self._siguiente_id += 1
        fila = {
            "id": lote_id,
            "telegram_user_id": telegram_user_id,
            "nombre": nombre,
            "localidad": localidad,
            "cultivo_habitual": cultivo_habitual,
            "ensayo_habitual": ensayo_habitual,
        }
        self.lotes.setdefault(telegram_user_id, []).append(fila)
        return lote_id


@pytest.fixture
def db():
    return BaseDeDatosFalsa()


@pytest.mark.asyncio
async def test_catalogo_vacio_al_inicio(db):
    catalogo = await lotes_mod.listar_lotes_para_prompt(db, 111)
    assert catalogo == []


@pytest.mark.asyncio
async def test_lote_mencionado_con_nombre_distinto_se_reconoce_por_id(db):
    """Simula que el LLM ya identificó que 'Lote Tres' es el mismo 'Lote 3' existente."""
    lote_id = await lotes_mod.resolver_o_crear_lote(db, 111, None, "Lote 3", "San Justo", "soja", "E1")

    lote_id_reconocido = await lotes_mod.resolver_o_crear_lote(
        db, 111, lote_id, "Lote Tres", "San Justo", "soja", "E1"
    )

    assert lote_id_reconocido == lote_id
    assert len(await db.listar_lotes(111)) == 1


@pytest.mark.asyncio
async def test_nombre_sin_relacion_crea_lote_nuevo(db):
    await lotes_mod.resolver_o_crear_lote(db, 111, None, "Lote 3", "San Justo", "soja", "E1")
    await lotes_mod.resolver_o_crear_lote(db, 111, None, "Lote Norte", "San Justo", "soja", "E1")

    catalogo = await lotes_mod.listar_lotes_para_prompt(db, 111)
    assert {lote.nombre for lote in catalogo} == {"Lote 3", "Lote Norte"}


@pytest.mark.asyncio
async def test_id_sugerido_que_no_pertenece_al_usuario_crea_lote_nuevo(db):
    lote_id = await lotes_mod.resolver_o_crear_lote(db, 111, 9999, "Lote 5", None, None, None)
    assert lote_id != 9999
    assert len(await db.listar_lotes(111)) == 1


@pytest.mark.asyncio
async def test_sin_nombre_de_lote_no_crea_nada(db):
    lote_id = await lotes_mod.resolver_o_crear_lote(db, 111, None, None, None, None, None)
    assert lote_id is None
    assert await db.listar_lotes(111) == []
