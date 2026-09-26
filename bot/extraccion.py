"""Extracción de datos estructurados desde una transcripción, usando Ollama.

Se hace en hasta tres pasos, porque un modelo chico se confunde si tiene que
llenar todo de una vez y además tarda mucho en escribir campos vacíos:

1. Datos del lote y de cada híbrido (híbrido, stand, estado). Siempre.
2. Malezas, plagas y enfermedades (y los "no hay"). Solo si el audio parece
   hablar de eso.
3. Umbral de daño, acciones y comentarios. Solo si el audio los menciona.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from . import catalogo as catalogo_mod
from .config import Config
from .modelos import (
    CabeceraLote,
    Enfermedad,
    Hibrido,
    Lote,
    Maleza,
    Plaga,
    RecorridaAudio,
    Relevamiento,
    UmbralDanoEconomico,
    UnidadStand,
    _clave_lote,
    normalizar_texto,
)

logger = logging.getLogger(__name__)


# El texto común va primero: Ollama reutiliza lo que ya procesó cuando el comienzo del pedido es igual.
PROMPT_COMUN = """\
Sos un asistente que extrae datos de recorridas agronómicas a campo a partir \
de la transcripción de un audio de un técnico. Respondé SIEMPRE en español y \
devolvé únicamente JSON válido según el esquema.

El técnico habla de forma libre e informal, sin orden ni formato fijo. Leé el \
texto entero y completá todo lo que menciona, aunque sea de pasada.

Reglas generales:
- No inventes datos: lo que no se dice, se omite.
- Escribí en el JSON solamente los campos que el audio menciona. Omití por \
completo los que no se dicen: no escribas null, ni listas vacías, ni false.
- Los números con coma decimal ("3,5") se devuelven con punto (3.5).
- Se te puede pasar un vocabulario precargado (híbridos, malezas, plagas, \
enfermedades, con sus sinónimos): usalo para reconocer de qué se habla y \
devolvé siempre el nombre oficial, no el sinónimo. Corregí los errores \
evidentes de transcripción.
- Se te puede pasar el "Contexto del lote en curso": el lote que el técnico \
ya está recorriendo. Nunca copies esas palabras como valor de un campo.
- Extraé SOLO lo que dice este audio.
"""

INSTRUCCION_PASO_1 = """\

PASO 1: DATOS DEL LOTE Y DE CADA HÍBRIDO.
Completá los datos del lote y la lista "hibridos": un item por cada \
híbrido/variedad, sin repetir ninguno.
- "provincia" va separada de "localidad": "Roberts, Buenos Aires" es \
localidad "Roberts" y provincia "Buenos Aires".
- "cultivo" en minúscula (maíz, soja, trigo, girasol, sorgo, cebada...).
- "ensayo" es el nombre o código del ensayo en general; solo si lo dicen. \
"tratamiento" es el tratamiento o cultivar particular dentro del ensayo.
- "hibrido_variedad" es el material específico que se recorre: un híbrido \
(maíz, girasol, sorgo; ej. "9939") o una variedad (soja, trigo, cebada; ej. \
"DM 46i20", "ST 38EA23"). Es siempre un código o nombre comercial, distinto \
del nombre del cultivo. Una palabra como "variedad" o "híbrido" no es un nombre.
- Cada dato de un híbrido va en el híbrido que se nombra en esa misma frase, \
esté antes o después del dato: "stand del 9939, 3 plantas al metro", "3,5 \
plantas al metro en el 9939" y "el 9939 tiene 3,5" significan lo mismo. Si \
el técnico sigue dando datos sin nombrar otro híbrido, son del último nombrado.
- "stand_valor": si dice "N plantas al metro" o "por metro", es N; si dice \
"N plantas en M metros", es N/M. Siempre es por metro lineal.
- "estado_cultivo": cómo está el cultivo (bueno, regular, con manchas...).
- Si hay stand o estado pero no se nombra ningún híbrido, devolvé un único \
item de "hibridos" sin "hibrido_variedad". Si el audio solo habla del lote, \
omití "hibridos".
- NO incluyas malezas, plagas, enfermedades, acciones ni comentarios: se \
procesan aparte.
- Si te pasan un catálogo de lotes y el lote mencionado coincide con uno \
(aunque se lo nombre distinto, p. ej. "Lote 3" y "Lote Tres"), completá \
"lote_id" con su id y "lote" con su nombre del catálogo.

Ejemplo. Audio: "Roberts, Buenos Aires. Lote El Ombú, soja, V4. El 5010 \
tiene 2,5 plantas al metro y en ese hay yuyo colorado al 10%. El 5020 tiene 3 \
plantas al metro, muy buen estado."
Respuesta: {"provincia":"Buenos Aires","localidad":"Roberts",\
"lote":"El Ombú","cultivo":"soja","estadio_fenologico":"V4",\
"hibridos":[{"hibrido_variedad":"5010","stand_valor":2.5},\
{"hibrido_variedad":"5020","stand_valor":3,"estado_cultivo":"muy bueno"}]}
"""

# Sin ejemplo a propósito: un modelo chico copia la respuesta del ejemplo aunque el audio sea otro.
INSTRUCCION_PASO_2 = """\

PASO 2: MALEZAS, PLAGAS Y ENFERMEDADES.
Te paso los híbridos ya detectados. Listá cada maleza, plaga o enfermedad \
que menciona el técnico, y cada vez que dice que NO hay.
- "hallazgos": un item por cada maleza, plaga o enfermedad mencionada. \
"tipo" es maleza, plaga o enfermedad. "nombre" es como se llama. "hibrido" \
es el híbrido al que se refiere (usá el nombre exacto de la lista); omitilo \
si habla del lote entero. "porcentaje" es un valor en % (cobertura de una \
maleza, daño de una plaga o incidencia de una enfermedad). "por_metro" es la \
cantidad por metro lineal. "detalle" es el tamaño de la maleza, la \
severidad de la enfermedad u otra aclaración.
- "porcentaje" y "por_metro" solo si el técnico dice el número. Si solo dice \
que hay presencia, omitilos.
- "ausencias": un item por cada "no hay". "tipo" es malezas, plagas o \
enfermedades, según lo que dice que no hay ("no hay síntomas de \
enfermedades" es enfermedades). "hibrido" solo si lo dice de un híbrido \
puntual; si es de todo el lote, omitilo.
- Si no menciona ninguna, devolvé {}.
"""

INSTRUCCION_PASO_3 = """\

PASO 3: UMBRAL, ACCIONES Y COMENTARIOS.
Extraé únicamente lo que el técnico dice de forma explícita sobre:
- "umbral_dano_economico": superado, cercano o no_superado.
- "acciones": qué se recomienda hacer (aplicar, controlar, resembrar...).
- "comentarios": observaciones libres del técnico.
Si no dice nada de eso, devolvé un JSON vacío: {}. No completes nada por su cuenta.
"""

# Si el audio no menciona nada de esto, se saltea el paso 2 (la mayoría de los audios de stand).
_PATRON_RELEVAMIENTOS = re.compile(
    r"maleza|plaga|enfermedad|sintoma|cobertura|incidencia|severidad|oruga|isoca|chinche|"
    r"trips|pulgon|arana|cogollero|gusano|roya|tizon|mancha|yuyo|rama negra|capin|"
    r"\bhay\b|\bsin\b|presencia|ninguno|ninguna|dano|%|por ciento"
)
# Y el paso 3 solo corre si aparece alguna de estas palabras.
_PATRON_OBSERVACIONES = re.compile(r"umbral|accion|aplic|recomend|comentario|observ|control|conviene|hay que")

# Las respuestas de relleno de un modelo chico ("no hay acciones", "sin comentarios") no son datos.
_PATRON_RELLENO = re.compile(r"^(no hay|no se|no|sin|ninguna?|nada|no corresponde)\b")


_SIN_DE = {"malezas": "sin_malezas", "plagas": "sin_plagas", "enfermedades": "sin_enfermedades"}


class ExtraccionError(Exception):
    pass


class _HibridoBasico(BaseModel):
    hibrido_variedad: str | None = None
    tratamiento: str | None = None
    stand_valor: float | None = None
    estado_cultivo: str | None = None


class _Paso1(CabeceraLote):
    hibridos: list[_HibridoBasico] = Field(default_factory=list)


class _Hallazgo(BaseModel):
    tipo: Literal["maleza", "plaga", "enfermedad"]
    nombre: str
    hibrido: str | None = None
    porcentaje: float | None = None
    por_metro: float | None = None
    detalle: str | None = None


class _Ausencia(BaseModel):
    tipo: Literal["malezas", "plagas", "enfermedades"]
    hibrido: str | None = None


class _Paso2(BaseModel):
    hallazgos: list[_Hallazgo] = Field(default_factory=list)
    ausencias: list[_Ausencia] = Field(default_factory=list)


class _Paso3(BaseModel):
    umbral_dano_economico: UmbralDanoEconomico = UmbralDanoEconomico.NO_EVALUADO
    acciones: str | None = None
    comentarios: str | None = None


_PASOS = {
    1: (_Paso1, INSTRUCCION_PASO_1, ("latitud", "longitud")),
    2: (_Paso2, INSTRUCCION_PASO_2, ()),
    3: (_Paso3, INSTRUCCION_PASO_3, ()),
}

_CLAVES_QUE_SOBRAN_EN_EL_ESQUEMA = ("title", "default", "description")


def _simplificar_esquema(nodo):
    """Saca del esquema lo que empuja al modelo a escribir campos vacíos: las variantes
    "o null" y los valores por defecto. Así puede omitir lo que no se dijo."""
    if isinstance(nodo, list):
        return [_simplificar_esquema(x) for x in nodo]
    if not isinstance(nodo, dict):
        return nodo
    if "anyOf" in nodo:
        ramas = [r for r in nodo["anyOf"] if r.get("type") != "null"]
        if len(ramas) == 1:
            nodo = {**{k: v for k, v in nodo.items() if k != "anyOf"}, **ramas[0]}
    return {
        k: _simplificar_esquema(v)
        for k, v in nodo.items()
        if k not in _CLAVES_QUE_SOBRAN_EN_EL_ESQUEMA
    }


def _esquema_json(paso: int) -> dict:
    """Esquema compacto que se le da a Ollama; la respuesta se valida después
    contra el modelo del paso (que completa con valores por defecto lo omitido)."""
    modelo, _instruccion, campos_a_quitar = _PASOS[paso]
    esquema = modelo.model_json_schema()
    for campo in campos_a_quitar:
        esquema["properties"].pop(campo, None)
    return _simplificar_esquema(esquema)


def _sin_tildes_y_minusculas(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def menciona_relevamientos(transcripcion: str, vocabulario: list[catalogo_mod.EntradaCatalogo]) -> bool:
    """True si el audio parece hablar de malezas, plagas o enfermedades (o de que no hay)."""
    texto = _sin_tildes_y_minusculas(transcripcion)
    if _PATRON_RELEVAMIENTOS.search(texto):
        return True
    for entrada in vocabulario:
        if entrada.tipo in ("maleza", "plaga", "enfermedad"):
            for nombre in [entrada.nombre, *entrada.sinonimos]:
                clave = _sin_tildes_y_minusculas(nombre).strip()
                if len(clave) >= 3 and clave in texto:
                    return True
    return False


def menciona_observaciones(transcripcion: str) -> bool:
    """True si el audio parece hablar de umbral de daño, acciones a realizar o comentarios."""
    return bool(_PATRON_OBSERVACIONES.search(_sin_tildes_y_minusculas(transcripcion)))


def _vocabulario_para_paso_1(vocabulario: list[catalogo_mod.EntradaCatalogo]) -> str:
    return catalogo_mod.formatear_para_prompt(
        [e for e in vocabulario if e.tipo in ("hibrido", "ensayo", "nota")]
    )


def _prompt_paso_1(
    transcripcion: str,
    lotes_existentes: list[Lote],
    cabecera_abierta: CabeceraLote | None,
    vocabulario: list[catalogo_mod.EntradaCatalogo],
) -> str:
    partes = []
    if lotes_existentes:
        catalogo = [lote.model_dump() for lote in lotes_existentes]
        partes.append(f"Catálogo de lotes existentes del usuario:\n{json.dumps(catalogo, ensure_ascii=False)}")
    texto_vocabulario = _vocabulario_para_paso_1(vocabulario)
    if texto_vocabulario:
        partes.append(texto_vocabulario)
    if cabecera_abierta is not None:
        partes.append(
            "Contexto del lote en curso:\n"
            f"{cabecera_abierta.model_dump_json(exclude_none=True)}"
        )
    partes.append(f"Transcripción del audio del técnico:\n{transcripcion}")
    return "\n\n".join(partes)


def _prompt_paso_2(transcripcion: str, hibridos: list[str]) -> str:
    """Sin vocabulario a propósito: un modelo chico toma esos nombres como candidatos y
    los inventa. Los nombres se unifican por código después (`unificar_nombres`)."""
    partes = []
    partes.append(f"Híbridos detectados: {', '.join(hibridos) if hibridos else '(ninguno con nombre)'}")
    partes.append(f"Transcripción del audio del técnico:\n{transcripcion}")
    return "\n\n".join(partes)


def _prompt_paso_3(transcripcion: str) -> str:
    return f"Transcripción del audio del técnico:\n{transcripcion}"


def _keep_alive(valor: str) -> str | int:
    """Ollama acepta una duración ("30m") o segundos como número (-1 = siempre cargado)."""
    return int(valor) if re.fullmatch(r"-?\d+", valor.strip()) else valor


async def _llamar_ollama(config: Config, paso: int, prompt_usuario: str, max_tokens: int = 1500) -> str:
    _modelo, instruccion, _campos = _PASOS[paso]
    payload = {
        "model": config.ollama_model,
        "messages": [
            {"role": "system", "content": PROMPT_COMUN + instruccion},
            {"role": "user", "content": prompt_usuario},
        ],
        "format": _esquema_json(paso),
        "stream": False,
        "keep_alive": _keep_alive(config.ollama_keep_alive),
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": max_tokens},
    }
    async with httpx.AsyncClient(timeout=300.0) as client:
        respuesta = await client.post(f"{config.ollama_host}/api/chat", json=payload)
        respuesta.raise_for_status()
        data = respuesta.json()
        return data["message"]["content"]


async def _pedir(config: Config, paso: int, prompt_usuario: str):
    """Hace el pedido de un paso y devuelve la respuesta validada; reintenta una vez."""
    modelo = _PASOS[paso][0]
    ultimo_error: Exception | None = None
    for intento in range(2):
        try:
            contenido = await _llamar_ollama(config, paso, prompt_usuario)
            return modelo.model_validate(json.loads(contenido))
        except (json.JSONDecodeError, ValidationError, KeyError, httpx.HTTPError) as exc:
            logger.warning(
                "Intento %d del paso %d falló (%s): %s", intento + 1, paso, type(exc).__name__, exc
            )
            ultimo_error = exc
    raise ExtraccionError(
        f"No se pudo extraer una ficha válida (paso {paso}) tras 2 intentos: "
        f"{type(ultimo_error).__name__}: {ultimo_error}"
    )


def _sin_textos_vacios(datos: dict) -> dict:
    """El modelo a veces escribe "" en vez de omitir el campo. Un "" en el lote impediría que
    otro audio lo complete después (`combinar` solo rellena lo que falta)."""
    return {k: v for k, v in datos.items() if not (isinstance(v, str) and not v.strip())}


def _audio_desde_paso_1(paso1: _Paso1) -> RecorridaAudio:
    hibridos = []
    for h in paso1.hibridos:
        datos = _sin_textos_vacios(h.model_dump(exclude_none=True))
        if h.stand_valor is not None:
            datos["stand_unidad"] = UnidadStand.PL_M_LINEAL
        hibridos.append(Hibrido(**datos))
    cabecera = _sin_textos_vacios({campo: getattr(paso1, campo) for campo in CabeceraLote.model_fields})
    return RecorridaAudio(**cabecera, hibridos=hibridos)


def _detalle_util(detalle: str | None) -> str | None:
    """Descarta el "detalle" cuando solo repite un número ("5% de cobertura", "0,5 por metro")."""
    if detalle is None or re.search(r"\d|%", detalle):
        return None
    return detalle.strip() or None


def _relevamiento_de(hallazgo: _Hallazgo) -> Relevamiento:
    detalle = _detalle_util(hallazgo.detalle)
    if hallazgo.tipo == "maleza":
        item = Maleza(nombre=hallazgo.nombre, porcentaje_cobertura=hallazgo.porcentaje, tamano=detalle)
        return Relevamiento(malezas=[item])
    if hallazgo.tipo == "plaga":
        por_metro, porcentaje = hallazgo.por_metro, hallazgo.porcentaje
        # el modelo suele poner "0,5 por metro" en el campo de porcentaje
        if por_metro is None and porcentaje is not None and "metro" in (hallazgo.detalle or "").lower():
            por_metro, porcentaje = porcentaje, None
        item = Plaga(
            nombre=hallazgo.nombre, cantidad_por_metro_lineal=por_metro, porcentaje_dano=porcentaje, observacion=detalle
        )
        return Relevamiento(plagas=[item])
    item = Enfermedad(nombre=hallazgo.nombre, porcentaje_incidencia=hallazgo.porcentaje, severidad=detalle)
    return Relevamiento(enfermedades=[item])


def _nombre_esta_en_el_texto(nombre: str, transcripcion: str) -> bool:
    """Guarda contra inventos: un hallazgo solo vale si alguna palabra de su nombre aparece
    en lo que dijo el técnico (se compara el comienzo de la palabra, para plurales)."""
    texto = _sin_tildes_y_minusculas(transcripcion)
    palabras = [p for p in re.findall(r"[a-z0-9]+", _sin_tildes_y_minusculas(nombre)) if len(p) >= 4]
    return any(p[:5] in texto for p in palabras) if palabras else _sin_tildes_y_minusculas(nombre) in texto


_PATRON_NO_HAY = re.compile(r"\bno hay\b|\bsin\b|\bausencia\b|\blibre\b|\bno se (observ|detect|encontr)")


# corta en frases sin partir los decimales ("0,5", "3.2")
_CORTE_DE_FRASES = re.compile(r"(?<!\d)[.,]|[.,](?!\d)|[;\n]")
_PATRON_REFERENCIA = re.compile(r"\b(ese|esa|este|esta|ahi|alli|mismo|misma)\b")


def _frases(transcripcion: str) -> list[str]:
    return _CORTE_DE_FRASES.split(transcripcion)


_NEGACION = (
    r"no hay|no se \w+|no (?:tiene|tengo|vi|veo|encontre|aparece\w*)|sin|ausencia de|libre de|"
    r"ningun\w*|nada de|tampoco(?: hay)?"
)
_PALABRAS_DE_AUSENCIA = {
    "malezas": r"male[zs]as?|yuyos?",
    "plagas": r"plagas?|insectos?|bichos?",
    "enfermedades": r"enfermedad(?:es)?|sintomas?|hongos?|patogenos?",
}
# entre el "no hay" y lo que no hay puede haber otras palabras ("no hay presencia de malezas"),
# pero no un cambio de tema ("no hay plagas pero hay malezas")
_PALABRA_INTERMEDIA = r"\W+(?!pero\b|aunque\b|hay\b|si\b)\w+"
_ORACIONES = re.compile(r"(?<!\d)\.|\.(?!\d)|[;\n]")


def _se_dice_que_no_hay(transcripcion: str, tipo: str) -> bool:
    """Guarda contra inventos: un modelo chico agrega "no hay malezas/plagas/enfermedades" aunque
    el técnico no lo diga. Solo vale si en una misma oración hay una negación y la palabra
    ("no hay malezas", "sin síntomas de enfermedades", "plagas, ninguna")."""
    palabras = _PALABRAS_DE_AUSENCIA[tipo]
    negacion_antes = re.compile(rf"\b(?:{_NEGACION})(?:{_PALABRA_INTERMEDIA}){{0,5}}?\W+(?:{palabras})\b")
    negacion_despues = re.compile(rf"\b(?:{palabras})(?:{_PALABRA_INTERMEDIA}){{0,2}}?\W+(?:{_NEGACION})\b")
    return any(
        negacion_antes.search(oracion) or negacion_despues.search(oracion)
        for oracion in _ORACIONES.split(_sin_tildes_y_minusculas(transcripcion))
    )


def _ausencia_es_de_hibrido(transcripcion: str, hibrido: str) -> bool:
    """Un "no hay" es de un híbrido puntual solo si ese híbrido aparece en la misma frase.
    Si no ("no hay enfermedades en ninguno"), vale para todo el lote."""
    clave = normalizar_texto(hibrido)
    for frase in _frases(transcripcion):
        if clave and clave in normalizar_texto(frase) and _PATRON_NO_HAY.search(_sin_tildes_y_minusculas(frase)):
            return True
    return False


def _hallazgo_es_de_hibrido(transcripcion: str, nombre_hallazgo: str, hibrido: str) -> bool:
    """Una maleza, plaga o enfermedad es de un híbrido puntual si ese híbrido aparece en la misma
    frase, o si la frase habla de "ese" y la anterior nombra al híbrido. Si no ("hay rama negra
    al 5%" dicho al final de la lista), vale para todo el lote."""
    clave = normalizar_texto(hibrido)
    palabras = [p for p in re.findall(r"[a-z0-9]+", _sin_tildes_y_minusculas(nombre_hallazgo)) if len(p) >= 4]
    frases = _frases(transcripcion)
    for i, frase in enumerate(frases):
        texto = _sin_tildes_y_minusculas(frase)
        if palabras and not any(p[:5] in texto for p in palabras):
            continue
        if clave and clave in normalizar_texto(frase):
            return True
        if i > 0 and _PATRON_REFERENCIA.search(texto) and clave in normalizar_texto(frases[i - 1]):
            return True
    return False


_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")
_NUMEROS_EN_PALABRAS = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100,
}
# la frase donde se dice el número tiene que hablar de un porcentaje o de una cantidad por metro
_CONTEXTO_PORCENTAJE = re.compile(r"%|por ?ciento|incidencia|cobertura|dano|severidad|afectad|cubr")
_CONTEXTO_POR_METRO = re.compile(r"metro")


def _numeros_en(frase: str) -> list[float]:
    texto = _sin_tildes_y_minusculas(frase)
    numeros = [float(n.replace(",", ".")) for n in _NUMERO.findall(texto)]
    numeros += [valor for palabra, valor in _NUMEROS_EN_PALABRAS.items() if re.search(rf"\b{palabra}\b", texto)]
    return numeros


def _frases_del_hallazgo(transcripcion: str, nombre: str, otros_nombres: list[str]) -> list[str]:
    """Las frases donde se nombra el hallazgo, más la siguiente de cada una ("hay mancha marrón,
    con 20% de incidencia"), salvo que esa siguiente ya hable de otra maleza, plaga o enfermedad."""
    frases = _frases(transcripcion)
    elegidas: list[str] = []
    for i, frase in enumerate(frases):
        if not _nombre_esta_en_el_texto(nombre, frase):
            continue
        elegidas.append(frase)
        if i + 1 < len(frases) and not any(_nombre_esta_en_el_texto(o, frases[i + 1]) for o in otros_nombres):
            elegidas.append(frases[i + 1])
    return elegidas


def _numero_dicho(valor: float, frases: list[str], contexto: re.Pattern, permite_division: bool = False) -> bool:
    """True si `valor` se dice en alguna de las `frases` y esa frase habla de `contexto`.
    Con `permite_division`, también vale "3 isocas en 2 metros" para 1,5 por metro."""
    for frase in frases:
        if not contexto.search(_sin_tildes_y_minusculas(frase)):
            continue
        numeros = _numeros_en(frase)
        if any(abs(n - valor) < 0.01 for n in numeros):
            return True
        if permite_division and any(abs(a / b - valor) < 0.01 for a in numeros for b in numeros if b):
            return True
    return False


def _sin_numeros_inventados(hallazgo: _Hallazgo, transcripcion: str, otros_nombres: list[str]) -> _Hallazgo:
    """Un modelo chico completa números que no se dijeron (p. ej. 100% de incidencia para "hay
    presencia de mancha marrón"). El porcentaje o la cantidad por metro solo quedan si ese número
    se dice cerca del nombre; una cantidad por metro puesta como porcentaje se pasa a su lugar."""
    if hallazgo.porcentaje is None and hallazgo.por_metro is None:
        return hallazgo
    frases = _frases_del_hallazgo(transcripcion, hallazgo.nombre, otros_nombres)
    porcentaje, por_metro = hallazgo.porcentaje, hallazgo.por_metro
    if porcentaje is not None and not _numero_dicho(porcentaje, frases, _CONTEXTO_PORCENTAJE):
        if hallazgo.tipo == "plaga" and por_metro is None and _numero_dicho(porcentaje, frases, _CONTEXTO_POR_METRO):
            por_metro = porcentaje
        porcentaje = None
    if por_metro is not None and not _numero_dicho(por_metro, frases, _CONTEXTO_POR_METRO, permite_division=True):
        por_metro = None
    if (porcentaje, por_metro) != (hallazgo.porcentaje, hallazgo.por_metro):
        logger.warning(
            "Corrijo los números de «%s»: %s%% y %s/m pasan a %s%% y %s/m (no se dijeron así)",
            hallazgo.nombre, hallazgo.porcentaje, hallazgo.por_metro, porcentaje, por_metro,
        )
    return hallazgo.model_copy(update={"porcentaje": porcentaje, "por_metro": por_metro})


# ---- datos del paso 1 que el modelo agrega sin que se digan ----

def _se_dijo(valor: str, transcripcion: str) -> bool:
    """True si `valor` aparece en lo dicho, tolerando errores de transcripción ("Rancawa" por
    "Rancagua", "Lotte Marelli" por "Lote Marelli")."""
    clave = normalizar_texto(valor)
    if not clave:
        return False
    if clave in normalizar_texto(transcripcion):
        return True
    if len(clave) < 4:
        return False
    palabras = [normalizar_texto(p) for p in re.findall(r"\w+", transcripcion)]
    cantidad = len(valor.split())
    for tamano in {max(1, cantidad - 1), cantidad, cantidad + 1}:
        for i in range(len(palabras) - tamano + 1):
            if SequenceMatcher(None, clave, "".join(palabras[i:i + tamano])).ratio() >= 0.8:
                return True
    return False


def _palabras_dichas(valor: str, transcripcion: str) -> bool:
    """Para textos libres que el modelo reescribe ("muy buen estado" -> "muy bueno"): alcanza
    con que alguna palabra importante aparezca (se compara el comienzo, por plurales y género)."""
    texto = _sin_tildes_y_minusculas(transcripcion)
    palabras = [p for p in re.findall(r"[a-z0-9]+", _sin_tildes_y_minusculas(valor)) if len(p) >= 4]
    if not palabras:
        return normalizar_texto(valor) in normalizar_texto(transcripcion)
    return any(p[:4] in texto for p in palabras)


def _sin_letras_iniciales(clave: str) -> str:
    """'st9939' -> '9939', 'dm46i20' -> '46i20': el código como suele dictarse."""
    return re.sub(r"^[a-z]+(?=\d)", "", clave)


def _claves_de_hibrido(nombre: str, vocabulario: list[catalogo_mod.EntradaCatalogo]) -> set[str]:
    """Las formas en que se puede haber dicho un híbrido: su nombre, sus sinónimos del catálogo
    y el código sin las letras del comienzo."""
    clave = catalogo_mod.clave_hibrido(nombre)
    nombres = {nombre}
    for entrada in vocabulario:
        textos = [entrada.nombre, *entrada.sinonimos]
        if entrada.tipo == "hibrido" and clave in {catalogo_mod.clave_hibrido(t) for t in textos}:
            nombres.update(textos)
    claves = set()
    for texto in nombres:
        c = catalogo_mod.clave_hibrido(texto)
        claves |= {c, _sin_letras_iniciales(c)}
    return {c for c in claves if len(c) >= 3}


_CODIGO = re.compile(r"\b\w*\d\w*\b")


def _un_caracter_distinto(a: str, b: str) -> bool:
    return len(a) == len(b) >= 4 and sum(x != y for x, y in zip(a, b)) <= 1


def _formas_del_hibrido(claves: set[str], transcripcion: str, claves_de_otros: set[str]) -> set[str]:
    """Cómo aparece el híbrido en lo dicho (vacío si no aparece). Tolera un carácter mal
    transcripto en el código ("BRV7172" dicho, "7272" en el catálogo), salvo que ese código
    sea el de otro híbrido del mismo audio."""
    texto = normalizar_texto(transcripcion)
    exactas = {c for c in claves if c in texto}
    if exactas:
        return exactas
    codigos = {normalizar_texto(t) for t in _CODIGO.findall(transcripcion)}
    codigos |= {_sin_letras_iniciales(c) for c in codigos}
    return {
        codigo for codigo in codigos - claves_de_otros
        if any(re.search(r"\d", c) and _un_caracter_distinto(c, codigo) for c in claves)
    }


def _frases_del_hibrido(transcripcion: str, formas: set[str], formas_de_otros: set[str]) -> list[str]:
    """Las frases que hablan del híbrido: desde que se lo nombra hasta que se nombra otro."""
    elegidas, siguiendo = [], False
    for frase in _frases(transcripcion):
        clave = normalizar_texto(frase)
        if any(f in clave for f in formas):
            siguiendo = True
        elif any(f in clave for f in formas_de_otros):
            siguiendo = False
        if siguiendo:
            elegidas.append(frase)
    return elegidas


# números sueltos: no los que son parte de un código ("46" y "20" en "DM46i20" no son un stand)
_NUMERO_SUELTO = re.compile(r"(?<![a-z\d])\d+(?:[.,]\d+)?(?![a-z\d])")
_CONTEXTO_STAND = re.compile(r"planta|metro|stand")


def _stand_dicho(valor: float, frases: list[str]) -> bool:
    """El stand vale si se dijo ese número, o si sale de dividir dos dichos ("7 plantas en 2 metros")."""
    numeros = []
    for frase in frases:
        texto = _sin_tildes_y_minusculas(frase)
        numeros += [float(n.replace(",", ".")) for n in _NUMERO_SUELTO.findall(texto)]
        numeros += [v for palabra, v in _NUMEROS_EN_PALABRAS.items() if re.search(rf"\b{palabra}\b", texto)]
    numeros = [n for n in numeros if n < 1000]
    if any(abs(n - valor) < 0.01 for n in numeros):
        return True
    return any(b and abs(a / b - valor) < 0.01 for a in numeros for b in numeros)


_CAMPOS_DEL_LOTE_A_CONTROLAR = ("provincia", "localidad", "cultivo", "ensayo", "estadio_fenologico")

# un estadio es V4, R3, R5.5, VE, VT... o una etapa con nombre; no un código de híbrido ("La 46EA25")
_ESTADIO_CON_CODIGO = re.compile(r"^(v|r)\s*-?\s*(\d{1,2}([.,]\d)?|e|t|n)$")
_ESTADIO_CON_NOMBRE = re.compile(
    r"emergencia|siembra|macoll|encan|espig|flora|antesis|llenado|grano|madurez|cosecha|hoja|nudo|boton|panoj|"
    r"vegetativ|reproductiv"
)
# "Lotte Marelli", "Lote-Marelli": la palabra "lote" (bien o mal transcripta) no es parte del nombre
_PALABRA_LOTE = re.compile(r"^\s*lot+e?\s*[-:.]?\s+|^\s*lot+e?\s*-\s*", re.IGNORECASE)


def _es_estadio(valor: str) -> bool:
    texto = _sin_tildes_y_minusculas(valor).strip()
    return bool(_ESTADIO_CON_CODIGO.match(texto) or _ESTADIO_CON_NOMBRE.search(texto))


def _nombre_de_lote(nombre: str) -> str:
    """'Lotte-Marelli' -> 'Marelli'. Si el nombre es solo un número ("Lote 3"), se deja entero."""
    sin_palabra = _PALABRA_LOTE.sub("", nombre).strip()
    return nombre.strip() if not sin_palabra or sin_palabra.isdigit() else sin_palabra


def _sin_datos_inventados(
    audio: RecorridaAudio,
    transcripcion: str,
    vocabulario: list[catalogo_mod.EntradaCatalogo],
    lotes_existentes: list[Lote],
) -> None:
    """Un modelo chico completa datos que no se dijeron: copia el ejemplo del pedido, deduce la
    provincia, inventa un híbrido con su stand... Todo lo del paso 1 que no aparece en lo dicho
    se saca, en el propio `audio`. Mejor un ❓ que el técnico completa que un dato falso."""
    for campo in _CAMPOS_DEL_LOTE_A_CONTROLAR:
        valor = getattr(audio, campo)
        if valor and not _se_dijo(valor, transcripcion):
            logger.warning("Descarto %s «%s»: no se dijo en el audio", campo, valor)
            setattr(audio, campo, None)
    if audio.estadio_fenologico and not _es_estadio(audio.estadio_fenologico):
        logger.warning("Descarto el estadio «%s»: no es un estadio fenológico", audio.estadio_fenologico)
        audio.estadio_fenologico = None
    if audio.lote:
        audio.lote = _nombre_de_lote(audio.lote)
        # "Lote 3" dicho y "Lote Tres" en el catálogo: vale si el modelo lo reconoció del catálogo
        del_catalogo = next((lote for lote in lotes_existentes if audio.lote_id is not None and lote.id == audio.lote_id), None)
        if not _se_dijo(audio.lote, transcripcion) and not (del_catalogo and "lote" in _sin_tildes_y_minusculas(transcripcion)):
            logger.warning("Descarto el lote «%s»: no se dijo en el audio", audio.lote)
            audio.lote = audio.lote_id = None
        elif del_catalogo is not None:
            audio.lote = del_catalogo.nombre
    if audio.lote and audio.lote_id is None:
        # si ya está en el catálogo de lotes con otro formato ("Lote Marelli"), se usa ese
        mismo = next((lote for lote in lotes_existentes if _clave_lote(lote.nombre) == _clave_lote(audio.lote)), None)
        if mismo is not None:
            audio.lote, audio.lote_id = mismo.nombre, mismo.id

    claves = [
        _claves_de_hibrido(h.hibrido_variedad, vocabulario) if h.hibrido_variedad else set() for h in audio.hibridos
    ]
    formas = [
        _formas_del_hibrido(c, transcripcion, set().union(*(o for j, o in enumerate(claves) if j != i))) if c else set()
        for i, c in enumerate(claves)
    ]
    conservados = []
    for i, h in enumerate(audio.hibridos):
        if h.hibrido_variedad:
            if not formas[i]:
                logger.warning("Descarto el híbrido «%s»: no se nombra en el audio", h.hibrido_variedad)
                continue
            de_otros = set().union(*(f for j, f in enumerate(formas) if j != i))
            frases = _frases_del_hibrido(transcripcion, formas[i], de_otros)
        else:
            frases = [f for f in _frases(transcripcion) if _CONTEXTO_STAND.search(_sin_tildes_y_minusculas(f))]
        if h.stand_valor is not None and not _stand_dicho(h.stand_valor, frases):
            logger.warning("Descarto el stand %s de «%s»: no se dijo", h.stand_valor, h.hibrido_variedad or "sin nombre")
            h.stand_valor = h.stand_unidad = None
        for campo in ("estado_cultivo", "tratamiento"):
            valor = getattr(h, campo)
            if valor and not _palabras_dichas(valor, transcripcion):
                logger.warning("Descarto %s «%s»: no se dijo en el audio", campo, valor)
                setattr(h, campo, None)
        if h.hibrido_variedad or h.stand_valor is not None or h.estado_cultivo or h.tratamiento:
            conservados.append(h)
    audio.hibridos = conservados


def _nombre_detectado(nombre: str, conocidos: list[str]) -> str:
    """Lleva el nombre que escribió el modelo al del híbrido ya detectado en el paso 1
    ("ST9939" y "9939" son el mismo). Si no hay una coincidencia clara, lo deja como está."""
    clave = normalizar_texto(nombre)
    exactos = [c for c in conocidos if normalizar_texto(c) == clave]
    if exactos:
        return exactos[0]
    parecidos = [
        c for c in conocidos
        if len(normalizar_texto(c)) >= 3 and (normalizar_texto(c) in clave or clave in normalizar_texto(c))
    ]
    return parecidos[0] if len(parecidos) == 1 else nombre


def _audio_desde_paso_2(paso2: _Paso2, transcripcion: str, conocidos: list[str] | None = None) -> RecorridaAudio:
    """Lo del paso 2 como un RecorridaAudio para sumarlo con `combinar`. Lo que se
    atribuye a un híbrido va a ese híbrido; lo que no, queda como dato general."""
    audio = RecorridaAudio()
    por_hibrido: dict[str, Hibrido] = {}

    def destino(nombre_hibrido: str | None) -> Relevamiento:
        if not nombre_hibrido:
            return audio
        nombre_hibrido = _nombre_detectado(nombre_hibrido, conocidos or [])
        clave = normalizar_texto(nombre_hibrido)
        if clave not in por_hibrido:
            por_hibrido[clave] = Hibrido(hibrido_variedad=nombre_hibrido)
            audio.hibridos.append(por_hibrido[clave])
        return por_hibrido[clave]

    for hallazgo in paso2.hallazgos:
        # a veces el modelo cruza los campos y pone el híbrido como nombre de la maleza/plaga
        if hallazgo.hibrido and normalizar_texto(hallazgo.nombre) == normalizar_texto(hallazgo.hibrido):
            if not _detalle_util(hallazgo.detalle):
                continue
            hallazgo = hallazgo.model_copy(update={"nombre": hallazgo.detalle.strip(), "detalle": None})
        if not _nombre_esta_en_el_texto(hallazgo.nombre, transcripcion):
            logger.warning("Descarto «%s»: el modelo lo mencionó pero no está en el audio", hallazgo.nombre)
            continue
        otros_nombres = [h.nombre for h in paso2.hallazgos if normalizar_texto(h.nombre) != normalizar_texto(hallazgo.nombre)]
        hallazgo = _sin_numeros_inventados(hallazgo, transcripcion, otros_nombres)
        de_hibrido = _nombre_detectado(hallazgo.hibrido, conocidos or []) if hallazgo.hibrido else None
        if de_hibrido and not _hallazgo_es_de_hibrido(transcripcion, hallazgo.nombre, de_hibrido):
            de_hibrido = None
        destino(de_hibrido).fusionar_relevamientos(_relevamiento_de(hallazgo))
    for ausencia in paso2.ausencias:
        if not _se_dice_que_no_hay(transcripcion, ausencia.tipo):
            logger.warning("Descarto «no hay %s»: el modelo lo agregó pero no está en el audio", ausencia.tipo)
            continue
        de_hibrido = ausencia.hibrido if ausencia.hibrido and _ausencia_es_de_hibrido(transcripcion, ausencia.hibrido) else None
        relevamiento = destino(de_hibrido)
        # si en este mismo audio se nombró algo de ese tipo, el "no hay" no lo puede borrar
        if getattr(relevamiento, ausencia.tipo):
            logger.warning("Descarto «no hay %s»: en el mismo audio se nombró uno", ausencia.tipo)
            continue
        relevamiento.fusionar_relevamientos(Relevamiento(**{_SIN_DE[ausencia.tipo]: True}))
    return audio


def _texto_util(texto: str | None) -> str | None:
    """None si el texto es de relleno ("No hay acciones recomendadas")."""
    if texto is None or _PATRON_RELLENO.match(_sin_tildes_y_minusculas(texto).strip()):
        return None
    return texto.strip() or None


def _audio_desde_paso_3(paso3: _Paso3) -> RecorridaAudio:
    return RecorridaAudio(
        umbral_dano_economico=paso3.umbral_dano_economico,
        acciones=_texto_util(paso3.acciones),
        comentarios=_texto_util(paso3.comentarios),
    )


async def extraer_recorrida(
    config: Config,
    transcripcion: str,
    lotes_existentes: list[Lote] | None = None,
    cabecera_abierta: CabeceraLote | None = None,
    vocabulario: list[catalogo_mod.EntradaCatalogo] | None = None,
) -> RecorridaAudio:
    """Extrae lo que dice UN audio: datos del lote, datos generales y lista de híbridos.

    Cada audio se extrae por separado; el bot los suma después (`RecorridaAudio.combinar`).
    Si falla algún paso tras reintentar, propaga `ExtraccionError` (no se descarta nada en silencio).
    """
    vocabulario = vocabulario or []
    paso1 = await _pedir(
        config, 1, _prompt_paso_1(transcripcion, lotes_existentes or [], cabecera_abierta, vocabulario)
    )
    audio = _audio_desde_paso_1(paso1)
    _sin_datos_inventados(audio, transcripcion, vocabulario, lotes_existentes or [])
    catalogo_mod.unificar_nombres(audio, vocabulario)

    if menciona_relevamientos(transcripcion, vocabulario):
        nombres = [h.hibrido_variedad for h in audio.hibridos if h.hibrido_variedad]
        paso2 = await _pedir(config, 2, _prompt_paso_2(transcripcion, nombres))
        extra = _audio_desde_paso_2(paso2, transcripcion, nombres)
        catalogo_mod.unificar_nombres(extra, vocabulario)
        audio.combinar(extra)

    if menciona_observaciones(transcripcion):
        audio.combinar(_audio_desde_paso_3(await _pedir(config, 3, _prompt_paso_3(transcripcion))))

    audio.transcripcion_original = transcripcion
    return audio


async def precalentar(config: Config) -> None:
    """Carga el modelo en memoria y procesa el texto común de antemano, para que el
    primer audio no pague ese tiempo. Si falla, no pasa nada: se hará en el primer audio."""
    try:
        await _llamar_ollama(config, 1, "Transcripción del audio del técnico:\nhola", max_tokens=1)
        logger.info("Modelo de Ollama precargado.")
    except Exception as exc:
        logger.warning("No se pudo precargar el modelo de Ollama: %s", exc)
