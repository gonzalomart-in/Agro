import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bot.config import Config
from bot.extraccion import ExtraccionError, extraer_recorrida
from bot.modelos import Lote, RecorridaCampo


def _config_prueba() -> Config:
    return Config(
        telegram_bot_token="test",
        database_url="postgresql://x",
        ollama_host="http://ollama-test:11434",
        ollama_model="qwen2.5:3b",
        whisper_model="base",
        admin_user_ids=[],
    )


def _respuesta_ollama(contenido_dict: dict):
    return {"message": {"content": json.dumps(contenido_dict)}}


@pytest.mark.asyncio
async def test_extraccion_exitosa_primer_intento():
    datos_llm = {
        "localidad": "San Justo",
        "lote": "Lote 3",
        "cultivo": "soja",
        "ensayo": "E1",
        "stand_valor": 7.2,
        "stand_unidad": "pl/m lineal",
        "estado_cultivo": "bueno",
    }
    with patch("bot.extraccion._llamar_ollama", new=AsyncMock(return_value=json.dumps(datos_llm))):
        ficha = await extraer_recorrida(_config_prueba(), "transcripcion de prueba")

    assert isinstance(ficha, RecorridaCampo)
    assert ficha.localidad == "San Justo"
    assert ficha.stand_valor == 7.2
    assert ficha.transcripcion_original == "transcripcion de prueba"


@pytest.mark.asyncio
async def test_extraccion_reintenta_si_json_invalido_y_luego_funciona():
    datos_ok = {"localidad": "Rafaela", "cultivo": "maiz"}
    mock = AsyncMock(side_effect=["no es json valido", json.dumps(datos_ok)])
    with patch("bot.extraccion._llamar_ollama", new=mock):
        ficha = await extraer_recorrida(_config_prueba(), "otra transcripcion")

    assert ficha.localidad == "Rafaela"
    assert mock.call_count == 2


@pytest.mark.asyncio
async def test_extraccion_falla_tras_dos_intentos():
    mock = AsyncMock(side_effect=httpx.ConnectError("no se pudo conectar"))
    with patch("bot.extraccion._llamar_ollama", new=mock):
        with pytest.raises(ExtraccionError):
            await extraer_recorrida(_config_prueba(), "transcripcion")
    assert mock.call_count == 2


@pytest.mark.asyncio
async def test_extraccion_pasa_catalogo_de_lotes_al_prompt():
    lotes = [Lote(id=1, nombre="Lote 3", localidad="San Justo", cultivo_habitual="soja")]
    datos = {"localidad": "San Justo", "lote": "Lote 3", "lote_id": 1, "cultivo": "soja"}

    prompts_capturados = []

    async def fake_llamar(config, prompt_usuario):
        prompts_capturados.append(prompt_usuario)
        return json.dumps(datos)

    with patch("bot.extraccion._llamar_ollama", new=fake_llamar):
        ficha = await extraer_recorrida(_config_prueba(), "hablando del lote tres", lotes_existentes=lotes)

    assert ficha.lote_id == 1
    assert "Lote 3" in prompts_capturados[0]


@pytest.mark.asyncio
async def test_extraccion_correccion_conserva_transcripcion_previa():
    ficha_actual = RecorridaCampo(
        localidad="Ceres", cultivo="sorgo", ensayo="E2", transcripcion_original="mensaje original"
    )
    datos_corregidos = {"localidad": "Ceres", "cultivo": "sorgo", "ensayo": "E3"}

    with patch("bot.extraccion._llamar_ollama", new=AsyncMock(return_value=json.dumps(datos_corregidos))):
        ficha = await extraer_recorrida(
            _config_prueba(), "el ensayo es E3, no E2", ficha_actual=ficha_actual
        )

    assert ficha.ensayo == "E3"
    assert "mensaje original" in ficha.transcripcion_original
    assert "el ensayo es E3" in ficha.transcripcion_original
