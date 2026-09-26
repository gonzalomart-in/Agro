"""Extracción de datos estructurados desde una transcripción, usando Ollama."""
from __future__ import annotations

import json
import logging

import httpx
from pydantic import ValidationError

from .config import Config
from .modelos import Lote, RecorridaCampo

logger = logging.getLogger(__name__)

PROMPT_SISTEMA = """\
Sos un asistente que extrae datos estructurados de recorridas agronómicas a \
campo a partir de la transcripción de un audio de un técnico. Respondé \
SIEMPRE en el idioma español y devolvé únicamente JSON válido según el \
esquema indicado.

Tu tarea es leer la transcripción con mucha atención, frase por frase, y \
completar en el JSON TODOS los datos que el técnico haya mencionado \
explícitamente, aunque los mencione de forma breve o informal. Es un error \
grave dejar un campo en null si el dato aparece en la transcripción, por \
ejemplo el nombre de la localidad, el número o nombre del lote, o el \
ensayo: repasá el texto buscando específicamente cada uno de esos datos \
antes de responder.

Reglas:
- No inventes datos que no fueron dichos: si y solo si un campo realmente \
no se menciona en absoluto, usá null (o lista vacía para \
malezas/plagas/enfermedades). Pero si el dato está en el texto, por más \
breve que sea la mención, tenés que completarlo.
- Corregí errores evidentes de transcripción en nombres de malezas, plagas \
y enfermedades (por ejemplo, vocabulario agronómico argentino mal \
transcripto), pero no inventes nombres que no fueron mencionados.
- El cultivo se normaliza siempre en minúscula (maíz, soja, trigo, \
girasol, sorgo, cebada, etc.).
- "hibrido_variedad" es el híbrido o variedad específico del cultivo (ej. \
"DM 53i54", "ACA 315"), distinto del nombre genérico del cultivo.
- "ensayo" es el nombre o código del ensayo en general (ej. "E1", "densidad \
2025"). "tratamiento" es el tratamiento o cultivar particular dentro de ese \
ensayo, ya que un mismo ensayo puede tener varios tratamientos o cultivares \
distintos evaluándose en paralelo. No confundas uno con el otro.
- Si el técnico da el stand como "N plantas en M metros", calculá el valor \
como N/M y usá la unidad "pl/m lineal".
- Se te puede pasar un catálogo de lotes ya existentes del usuario (con su \
id, nombre, localidad y cultivo habitual). Si el lote mencionado en el \
audio coincide con uno del catálogo (aunque se lo nombre distinto, p. ej. \
"Lote 3" y "Lote Tres"), completá "lote_id" con ese id y "lote" con el \
nombre tal como está en el catálogo. Si no coincide con ninguno, dejá \
"lote_id" en null.
- Si estás corrigiendo o completando una ficha ya existente (te pasamos el \
JSON actual), conservá todos los campos que no cambian y aplicá solo la \
corrección o el agregado indicado en el nuevo mensaje.
"""


class ExtraccionError(Exception):
    pass


def _esquema_json() -> dict:
    return RecorridaCampo.model_json_schema()


def _construir_prompt_usuario(
    transcripcion: str,
    lotes_existentes: list[Lote],
    ficha_actual: RecorridaCampo | None,
) -> str:
    partes = []
    if lotes_existentes:
        catalogo = [lote.model_dump() for lote in lotes_existentes]
        partes.append(f"Catálogo de lotes existentes del usuario:\n{json.dumps(catalogo, ensure_ascii=False)}")
    if ficha_actual is not None:
        partes.append(
            "Ficha actual (a corregir/completar con el nuevo mensaje):\n"
            f"{ficha_actual.model_dump_json()}"
        )
        partes.append(f"Nuevo mensaje del técnico:\n{transcripcion}")
    else:
        partes.append(f"Transcripción del audio del técnico:\n{transcripcion}")
    return "\n\n".join(partes)


async def _llamar_ollama(config: Config, prompt_usuario: str) -> str:
    payload = {
        "model": config.ollama_model,
        "messages": [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": prompt_usuario},
        ],
        "format": _esquema_json(),
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        respuesta = await client.post(f"{config.ollama_host}/api/chat", json=payload)
        respuesta.raise_for_status()
        data = respuesta.json()
        return data["message"]["content"]


async def extraer_recorrida(
    config: Config,
    transcripcion: str,
    lotes_existentes: list[Lote] | None = None,
    ficha_actual: RecorridaCampo | None = None,
) -> RecorridaCampo:
    """Extrae (o corrige) una ficha de recorrida a partir de una transcripción.

    Reintenta una vez si la respuesta del LLM no valida contra
    `RecorridaCampo`; si vuelve a fallar, propaga `ExtraccionError`.
    """
    lotes_existentes = lotes_existentes or []
    prompt_usuario = _construir_prompt_usuario(transcripcion, lotes_existentes, ficha_actual)

    ultimo_error: Exception | None = None
    for intento in range(2):
        try:
            contenido = await _llamar_ollama(config, prompt_usuario)
            datos = json.loads(contenido)
            ficha = RecorridaCampo.model_validate(datos)
            ficha.transcripcion_original = (
                (ficha_actual.transcripcion_original + "\n" + transcripcion)
                if ficha_actual and ficha_actual.transcripcion_original
                else transcripcion
            )
            return ficha
        except (json.JSONDecodeError, ValidationError, KeyError, httpx.HTTPError) as exc:
            logger.warning(
                "Intento %d de extracción falló (%s): %s", intento + 1, type(exc).__name__, exc
            )
            ultimo_error = exc

    raise ExtraccionError(
        f"No se pudo extraer una ficha válida tras 2 intentos: "
        f"{type(ultimo_error).__name__}: {ultimo_error}"
    )
