"""Tests de clientes.py usando una base de datos falsa en memoria (no requiere Postgres)."""
import pytest

from bot import clientes as clientes_mod


class BaseDeDatosFalsa:
    def __init__(self):
        self._siguiente_id = 1
        self.clientes: dict[int, list[dict]] = {}

    async def listar_clientes(self, telegram_user_id: int):
        return self.clientes.get(telegram_user_id, [])

    async def crear_cliente(self, telegram_user_id, nombre):
        cliente_id = self._siguiente_id
        self._siguiente_id += 1
        self.clientes.setdefault(telegram_user_id, []).append(
            {"id": cliente_id, "telegram_user_id": telegram_user_id, "nombre": nombre}
        )
        return cliente_id


@pytest.fixture
def db():
    return BaseDeDatosFalsa()


@pytest.mark.asyncio
async def test_catalogo_vacio_al_inicio(db):
    assert await clientes_mod.listar_clientes_para_prompt(db, 111) == []


@pytest.mark.asyncio
async def test_cliente_nuevo_se_crea(db):
    cliente_id = await clientes_mod.resolver_o_crear_cliente(db, 111, None, "Don Justo")
    assert cliente_id is not None
    catalogo = await clientes_mod.listar_clientes_para_prompt(db, 111)
    assert [c.nombre for c in catalogo] == ["Don Justo"]


@pytest.mark.asyncio
async def test_cliente_mencionado_con_nombre_distinto_se_reconoce_por_nombre(db):
    """'Don Justo' y 'don justo' son el mismo, aunque el id sugerido no venga."""
    primer_id = await clientes_mod.resolver_o_crear_cliente(db, 111, None, "Don Justo")
    segundo_id = await clientes_mod.resolver_o_crear_cliente(db, 111, None, "don justo")
    assert segundo_id == primer_id
    assert len(await db.listar_clientes(111)) == 1


@pytest.mark.asyncio
async def test_id_sugerido_que_no_pertenece_al_usuario_se_ignora(db):
    cliente_id = await clientes_mod.resolver_o_crear_cliente(db, 111, 9999, "Don Justo")
    assert cliente_id != 9999
    assert len(await db.listar_clientes(111)) == 1


@pytest.mark.asyncio
async def test_sin_nombre_de_cliente_no_crea_nada(db):
    assert await clientes_mod.resolver_o_crear_cliente(db, 111, None, None) is None
    assert await db.listar_clientes(111) == []
