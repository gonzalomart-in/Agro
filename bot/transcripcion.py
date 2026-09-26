"""Transcripción de audio a texto con faster-whisper, forzando español."""
from __future__ import annotations

import logging

from faster_whisper import WhisperModel

from .config import Config

logger = logging.getLogger(__name__)

VOCABULARIO_AGRONOMICO = (
    "rama negra, sorgo de Alepo, yuyo colorado, Amaranthus, capín, raigrás, "
    "chinche, isoca, cogollero, Spodoptera, Diatraea, chicharrita, Dalbulus, "
    "trips, arañuela, roya, tizón, mancha ojo de rana, Cercospora, "
    "estadios V y R, umbral de daño económico."
)

_modelo: WhisperModel | None = None


def _obtener_modelo(config: Config) -> WhisperModel:
    global _modelo
    if _modelo is None:
        logger.info("Cargando modelo Whisper '%s' (CPU)...", config.whisper_model)
        _modelo = WhisperModel(config.whisper_model, device="cpu", compute_type="int8")
    return _modelo


def transcribir_archivo(ruta_audio: str, config: Config) -> str:
    """Transcribe un archivo de audio a texto en español. Función bloqueante:
    llamar siempre con `asyncio.to_thread` desde el bot."""
    modelo = _obtener_modelo(config)
    segmentos, _info = modelo.transcribe(
        ruta_audio,
        language="es",
        initial_prompt=VOCABULARIO_AGRONOMICO,
    )
    texto = " ".join(segmento.text.strip() for segmento in segmentos)
    return texto.strip()
