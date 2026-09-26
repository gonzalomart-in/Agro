"""Transcripción de audio a texto con faster-whisper, forzando español."""
from __future__ import annotations

import logging

from faster_whisper import WhisperModel

from .catalogo import quitar_eco_de_pista
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


def precargar_modelo(config: Config) -> None:
    """Carga Whisper en memoria (y lo descarga la primera vez) para que el primer audio no espere."""
    _obtener_modelo(config)


def transcribir_archivo(ruta_audio: str, config: Config, pista_extra: str = "") -> str:
    """Transcribe un archivo de audio a texto en español. Función bloqueante:
    llamar siempre con `asyncio.to_thread` desde el bot.

    `pista_extra` (vocabulario precargado) va al final de la pista porque
    Whisper solo conserva los últimos ~224 tokens.
    """
    modelo = _obtener_modelo(config)
    pista = f"{VOCABULARIO_AGRONOMICO} {pista_extra}".strip()
    segmentos, _info = modelo.transcribe(
        ruta_audio,
        language="es",
        initial_prompt=pista,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    texto = " ".join(segmento.text.strip() for segmento in segmentos).strip()
    limpio = quitar_eco_de_pista(texto, pista)
    if limpio != texto:
        logger.warning("Saqué de la transcripción lo que Whisper copió de la pista: %r -> %r", texto, limpio)
    return limpio
