"""Tests de acceso.py usando una base de datos falsa en memoria (no requiere Postgres)."""
import pytest

from bot import acceso


class BaseDeDatosFalsa:
    """Implementa la porción de la interfaz de BaseDeDatos que usa acceso.py."""

    def __init__(self):
        self.usuarios: dict[int, dict] = {}

    async def usuario_permitido(self, telegram_user_id: int):
        return self.usuarios.get(telegram_user_id)

    async def es_admin(self, telegram_user_id: int) -> bool:
        registro = self.usuarios.get(telegram_user_id)
        return bool(registro and registro["es_admin"])

    async def invitar_usuario(self, telegram_user_id, agregado_por, telegram_user_name=None):
        self.usuarios[telegram_user_id] = {
            "telegram_user_id": telegram_user_id,
            "es_admin": False,
            "agregado_por": agregado_por,
            "telegram_user_name": telegram_user_name,
        }

    async def revocar_usuario(self, telegram_user_id) -> bool:
        if telegram_user_id in self.usuarios:
            del self.usuarios[telegram_user_id]
            return True
        return False


@pytest.fixture
def db():
    fake = BaseDeDatosFalsa()
    fake.usuarios[111] = {"telegram_user_id": 111, "es_admin": True, "agregado_por": 111, "telegram_user_name": "admin"}
    return fake


@pytest.mark.asyncio
async def test_admin_tiene_acceso(db):
    assert await acceso.tiene_acceso(db, 111) is True


@pytest.mark.asyncio
async def test_usuario_no_registrado_no_tiene_acceso(db):
    assert await acceso.tiene_acceso(db, 999) is False


@pytest.mark.asyncio
async def test_admin_puede_invitar(db):
    await acceso.invitar(db, 111, 222, "tecnico2")
    assert await acceso.tiene_acceso(db, 222) is True
    assert await acceso.es_admin(db, 222) is False


@pytest.mark.asyncio
async def test_no_admin_no_puede_invitar(db):
    await acceso.invitar(db, 111, 222, "tecnico2")
    with pytest.raises(acceso.SinPermisoError):
        await acceso.invitar(db, 222, 333, "tecnico3")


@pytest.mark.asyncio
async def test_admin_puede_revocar(db):
    await acceso.invitar(db, 111, 222, "tecnico2")
    revocado = await acceso.revocar(db, 111, 222)
    assert revocado is True
    assert await acceso.tiene_acceso(db, 222) is False


@pytest.mark.asyncio
async def test_no_admin_no_puede_revocar(db):
    await acceso.invitar(db, 111, 222, "tecnico2")
    with pytest.raises(acceso.SinPermisoError):
        await acceso.revocar(db, 222, 111)


@pytest.mark.asyncio
async def test_usuario_no_permitido_es_rechazado_al_revocar(db):
    revocado = await acceso.revocar(db, 111, 999)
    assert revocado is False
