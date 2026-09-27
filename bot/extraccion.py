"""Extracción de datos estructurados desde una transcripción, usando Ollama.

Se hace en hasta cuatro pasos, porque un modelo chico se confunde si tiene que
llenar todo de una vez y además tarda mucho en escribir campos vacíos:

1. Datos del lote y de cada híbrido (híbrido, stand, estado). Siempre.
2. Malezas, plagas y enfermedades (y los "no hay"). Solo si el audio parece
   hablar de eso.
3. Umbral de daño, acciones y comentarios. Solo si el audio los menciona.
4. Productos a aplicar (o ya aplicados) con su dosis. Solo si el audio nombra
   algún producto o habla de aplicar.
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
from . import productos as productos_mod
from .config import Config
from .modelos import (
    Aplicacion,
    CabeceraLote,
    Cliente,
    Enfermedad,
    EstadoAplicacion,
    Hibrido,
    Lote,
    Maleza,
    Plaga,
    RecorridaAudio,
    Relevamiento,
    UmbralDanoEconomico,
    UnidadStand,
    _clave_lote,
    fusionar_aplicaciones,
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

# Paso aparte para el cliente (no en el paso 1): agregar el campo ahí hacía que el modelo
# chico dejara de extraer bien localidad/lote/cultivo apenas el audio nombraba un cliente
# (confirmado probando varios audios con distintos nombres). Corre solo si parece que se
# nombra un cliente, igual que los pasos 2 a 4.
INSTRUCCION_PASO_CLIENTE = """\

PASO: CLIENTE.
Decime si el técnico nombra al cliente o productor dueño del campo (p. ej. \
"cliente Don Justo", "en lo de Pérez", "el campo es de la familia Gómez").
- "cliente" es su nombre, solo si lo dice así. No confundas con el lote, la \
localidad o el técnico que graba el audio.
- Si te pasan un catálogo de clientes y coincide con uno (aunque se lo nombre \
distinto), completá "cliente_id" con su id y "cliente" con su nombre del catálogo.
- Si no nombra ningún cliente, devolvé {}.
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

# Sin ejemplo a propósito, como el paso 2: el modelo copiaría el producto y la dosis del ejemplo.
INSTRUCCION_PASO_4 = """\

PASO 4: PRODUCTOS A APLICAR O YA APLICADOS.
Te paso los híbridos ya detectados. Listá cada producto fitosanitario \
(herbicida, insecticida, fungicida, coadyuvante...) que el técnico recomienda \
aplicar o dice que ya se aplicó.
- "aplicaciones": un item por producto. "producto" es el nombre comercial o el \
principio activo, tal como lo dice el técnico. Cada producto va en un item \
aparte, aunque se nombren juntos en la misma frase. Incluí también los \
productos que ya se aplicaron.
- "dosis" es la cantidad por hectárea y "unidad" su unidad: l/ha, cc/ha, g/ha o \
kg/ha. Solo si el técnico dice el número.
- "objetivo": la maleza, plaga o enfermedad que se quiere controlar, si la dice.
- "momento": solamente cuándo aplicar o cuándo se aplicó, si lo dice. No pongas \
ahí dosis, litros ni caldo.
- "coadyuvante": el aceite, surfactante u otro coadyuvante que se agrega al \
producto, con su dosis si la dice.
- "volumen_caldo": litros de caldo (agua) por hectárea, si lo dice.
- "ya_aplicado": true solo si dice que el producto ya se aplicó. Si es una \
recomendación, omitilo.
- "hibrido": el híbrido al que se refiere (usá el nombre exacto de la lista); \
omitilo si es para todo el lote.
- Si no menciona ningún producto, devolvé {}.
"""

# Si el audio no menciona nada de esto, se saltea el paso 2 (la mayoría de los audios de stand).
_PATRON_RELEVAMIENTOS = re.compile(
    r"maleza|plaga|enfermedad|sintoma|cobertura|incidencia|severidad|oruga|isoca|chinche|"
    r"trips|pulgon|arana|cogollero|gusano|roya|tizon|mancha|yuyo|rama negra|capin|"
    r"\bhay\b|\bsin\b|presencia|ninguno|ninguna|dano|%|por ciento"
)
# Y el paso 3 solo corre si aparece alguna de estas palabras.
_PATRON_OBSERVACIONES = re.compile(r"umbral|accion|aplic|recomend|comentario|observ|control|conviene|hay que")
# El paso del cliente corre solo si aparece alguna de estas palabras (ver `menciona_cliente`).
_PATRON_CLIENTE = re.compile(r"cliente|productor|en lo de\b|due[ñn]o del campo")
# El paso 4 corre si aparece alguna de estas palabras o se nombra un producto (ver `menciona_aplicaciones`).
_PATRON_APLICACIONES = re.compile(
    r"aplic|pulveriz|fumig|dosis|herbicida|insecticida|fungicida|coadyuvante|curasemilla|caldo|"
    r"\bl/ha\b|\bcc\b|centimetros? cubicos|litros? (?:por|a la|la|/) ?(?:ha|hectarea)|"
    r"(?:gramos?|kilos?|litros?) por hectarea"
)

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


class _PasoCliente(BaseModel):
    cliente: str | None = None
    cliente_id: int | None = None


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


class _AplicacionExtraida(BaseModel):
    producto: str
    dosis: float | None = None
    unidad: str | None = None
    objetivo: str | None = None
    momento: str | None = None
    coadyuvante: str | None = None
    volumen_caldo: float | None = None
    ya_aplicado: bool = False
    hibrido: str | None = None


class _Paso4(BaseModel):
    aplicaciones: list[_AplicacionExtraida] = Field(default_factory=list)


_PASOS = {
    1: (_Paso1, INSTRUCCION_PASO_1, ("latitud", "longitud", "cliente", "cliente_id")),
    2: (_Paso2, INSTRUCCION_PASO_2, ()),
    3: (_Paso3, INSTRUCCION_PASO_3, ()),
    4: (_Paso4, INSTRUCCION_PASO_4, ()),
    5: (_PasoCliente, INSTRUCCION_PASO_CLIENTE, ()),
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


def menciona_aplicaciones(transcripcion: str, vocabulario: list[catalogo_mod.EntradaCatalogo]) -> bool:
    """True si el audio habla de aplicar algo o nombra un producto fitosanitario."""
    if _PATRON_APLICACIONES.search(_sin_tildes_y_minusculas(transcripcion)):
        return True
    return productos_mod.nombra_algun_producto(transcripcion, vocabulario)


def menciona_cliente(transcripcion: str, clientes_existentes: list[Cliente]) -> bool:
    """True si el audio parece nombrar a un cliente/productor: dice una palabra como "cliente" o
    "productor", o nombra a alguno ya cargado en el catálogo del usuario."""
    texto = _sin_tildes_y_minusculas(transcripcion)
    if _PATRON_CLIENTE.search(texto):
        return True
    return any(_se_dijo(c.nombre, transcripcion) for c in clientes_existentes)


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


def _prompt_paso_cliente(transcripcion: str, clientes_existentes: list[Cliente]) -> str:
    partes = []
    if clientes_existentes:
        catalogo = [c.model_dump() for c in clientes_existentes]
        partes.append(f"Catálogo de clientes existentes del usuario:\n{json.dumps(catalogo, ensure_ascii=False)}")
    partes.append(f"Transcripción del audio del técnico:\n{transcripcion}")
    return "\n\n".join(partes)


def _prompt_paso_4(transcripcion: str, hibridos: list[str]) -> str:
    """Sin la lista de productos a propósito (son miles, y un modelo chico copiaría nombres de ahí):
    los nombres se buscan en el registro de SENASA después (`productos.identificar`)."""
    return _prompt_paso_2(transcripcion, hibridos)


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


def _cliente_sin_inventar(
    audio: RecorridaAudio, transcripcion: str, clientes_existentes: list[Cliente]
) -> None:
    """Como `_sin_datos_inventados`, pero para lo que devuelve el paso del cliente: solo vale
    si se dijo, o si el modelo lo reconoció del catálogo y el audio menciona la palabra "cliente"."""
    if audio.cliente:
        del_catalogo = next(
            (c for c in clientes_existentes if audio.cliente_id is not None and c.id == audio.cliente_id), None
        )
        if not _se_dijo(audio.cliente, transcripcion) and not (
            del_catalogo and "cliente" in _sin_tildes_y_minusculas(transcripcion)
        ):
            logger.warning("Descarto el cliente «%s»: no se dijo en el audio", audio.cliente)
            audio.cliente = audio.cliente_id = None
        elif del_catalogo is not None:
            audio.cliente = del_catalogo.nombre
    if audio.cliente and audio.cliente_id is None:
        mismo = next(
            (c for c in clientes_existentes if normalizar_texto(c.nombre) == normalizar_texto(audio.cliente)), None
        )
        if mismo is not None:
            audio.cliente, audio.cliente_id = mismo.nombre, mismo.id


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


def _repartidor(audio: RecorridaAudio, conocidos: list[str]):
    """Devuelve `destino(nombre_hibrido)`: dónde va un dato de ese híbrido dentro de `audio` (el
    híbrido, que se agrega la primera vez) o, si no es de ningún híbrido, el propio `audio`."""
    por_hibrido: dict[str, Hibrido] = {}

    def destino(nombre_hibrido: str | None) -> RecorridaAudio | Hibrido:
        if not nombre_hibrido:
            return audio
        nombre_hibrido = _nombre_detectado(nombre_hibrido, conocidos)
        clave = normalizar_texto(nombre_hibrido)
        if clave not in por_hibrido:
            por_hibrido[clave] = Hibrido(hibrido_variedad=nombre_hibrido)
            audio.hibridos.append(por_hibrido[clave])
        return por_hibrido[clave]

    return destino


def _audio_desde_paso_2(paso2: _Paso2, transcripcion: str, conocidos: list[str] | None = None) -> RecorridaAudio:
    """Lo del paso 2 como un RecorridaAudio para sumarlo con `combinar`. Lo que se
    atribuye a un híbrido va a ese híbrido; lo que no, queda como dato general."""
    audio = RecorridaAudio()
    destino = _repartidor(audio, conocidos or [])

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


# ---- paso 4: productos y dosis ----

_NUMEROS_DE_DOSIS = {
    **_NUMEROS_EN_PALABRAS, "un": 1, "una": 1, "uno": 1, "once": 11, "doce": 12, "veinticinco": 25,
    "ciento": 100, "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300,
    "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700, "setecientas": 700,
    "ochocientos": 800, "ochocientas": 800, "novecientos": 900, "novecientas": 900, "mil": 1000,
}
_MEDIO = ("medio", "media")
# "un" y "medio" solo cuentan como cantidad pegados a una unidad ("un litro", "medio litro"):
# sueltos son muy comunes ("aplicar un herbicida")
_SOLO_CON_UNIDAD = {"un", "una", "uno", *_MEDIO}
_UNIDADES_DE_DOSIS = (
    ("cc/ha", r"cc|cm3|cms?|centimetros?|mililitros?|ml"),
    ("kg/ha", r"kilos?|kilogramos?|kgs?"),
    ("l/ha", r"litros?|lts?|l"),
    ("g/ha", r"gramos?|grs?|g"),
)
_TOKEN_DE_CANTIDAD = re.compile(r"\d+(?:[.,]\d+)?|[a-z0-9]+(?:/ha)?")
_CONTEXTO_CALDO = re.compile(r"caldo|volumen|agua|mojado")
_VOLUMEN_DE_CALDO = re.compile(r"[\w,.]+\s+(?:litros?|lts?|l)\s+de\s+(?:caldo|agua)", re.IGNORECASE)
# un "momento" que en realidad es la dosis o el caldo ("80 litros de caldo")
_NO_ES_MOMENTO = re.compile(r"litro|caldo|\bcc\b|gramo|kilo|l/ha|dosis|%")
# lo que dice que se habla de aplicar (antes de nombrar el producto) o una dosis (después)
_CONTEXTO_APLICACION = re.compile(
    r"aplic|pulveriz|fumig|recomiend|recomendamos|conviene|hay que|habria que|usar|usamos|tratar|entrar con|"
    r"pasar|pasamos|dosis|litro|\bcc\b|gramo|kilo|l/ha"
)
_NO_APLICAR = re.compile(r"\bno\b|\bni\b|sin necesidad|resistente|tolerante")
_MOMENTO_DICHO = re.compile(
    r"hace \w+ (?:d[ií]as?|semanas?)|antes de (?:la )?(?:siembra|[VR]\s?\d+)|en (?:pre|post)[\s-]?(?:siembra|emergencia)|"
    r"en barbecho|la semana pasada",
    re.IGNORECASE,
)
_YA_APLICADO = re.compile(
    r"\b(?:se (?:le |les )?(?:aplico|aplicaron|hizo|paso|pasaron|pulverizo|fumigo|dio)|"
    r"aplicamos|aplicaron|aplique|pulverizamos|pulverizaron|fumigamos|fumigaron|pasamos|pasaron|"
    r"ya (?:se |le |les )?(?:aplic|pas|hizo|hicieron|pulveriz|fumig)\w*|fue(?:ron)? aplicad\w*|"
    r"esta aplicad\w*|hace \w+ dias?)\b"
)


def _unidad_de_token(token: str) -> str | None:
    for codigo, patron in _UNIDADES_DE_DOSIS:
        if re.fullmatch(rf"(?:{patron})(?:/ha)?", token):
            return codigo
    return None


def _unidad_normalizada(unidad: str | None) -> str | None:
    """'litros por hectárea' -> 'l/ha', 'CC' -> 'cc/ha'. None si no es una unidad conocida."""
    primera = _TOKEN_DE_CANTIDAD.search(_sin_tildes_y_minusculas(unidad or ""))
    return _unidad_de_token(primera.group()) if primera else None


def _cantidades_con_unidad(frase: str) -> list[tuple[float, str | None]]:
    """Las cantidades que se dicen en `frase`, con la unidad que les sigue (si la hay):
    "2 litros y medio" -> (2.5, "l/ha"), "medio litro" -> (0.5, "l/ha"), "500 cc" -> (500, "cc/ha"),
    "doscientos cincuenta gramos" -> (250, "g/ha")."""
    tokens = _TOKEN_DE_CANTIDAD.findall(_sin_tildes_y_minusculas(frase))
    cantidades = []
    for i, token in enumerate(tokens):
        if token in _MEDIO:
            valor = 0.5
        elif re.fullmatch(r"\d+(?:[.,]\d+)?", token):
            valor = float(token.replace(",", "."))
        elif token in _NUMEROS_DE_DOSIS and not (i and tokens[i - 1] in _NUMEROS_DE_DOSIS):
            valor = _NUMEROS_DE_DOSIS[token]
        else:
            continue
        j = i + 1
        if token not in _SOLO_CON_UNIDAD and not token[0].isdigit():
            # "doscientos cincuenta" = 250, "mil quinientos" = 1500, "dos mil" = 2000
            while j < len(tokens) and tokens[j] in _NUMEROS_DE_DOSIS and tokens[j] not in _SOLO_CON_UNIDAD:
                siguiente = _NUMEROS_DE_DOSIS[tokens[j]]
                valor = valor * siguiente if siguiente > valor else valor + siguiente
                j += 1
        if tokens[j:j + 1] == ["y"] and tokens[j + 1:j + 2] and tokens[j + 1] in _MEDIO:
            valor, j = valor + 0.5, j + 2
        k = j + 1 if tokens[j:j + 1] == ["de"] else j
        unidad = _unidad_de_token(tokens[k]) if k < len(tokens) else None
        if unidad and tokens[k + 1:k + 2] == ["y"] and tokens[k + 2:k + 3] and tokens[k + 2] in _MEDIO:
            valor += 0.5  # "un litro y medio"
        if unidad or token not in _SOLO_CON_UNIDAD:
            cantidades.append((valor, unidad))
    return cantidades


def _dosis_dicha(valor: float, frases: list[str]) -> tuple[float, str | None] | None:
    """La dosis tal como se dijo en `frases` (cantidad y unidad), o None si no se dijo.
    Si el modelo pasó "500 cc" a 0,5 litros, vale lo que dijo el técnico."""
    candidatas = [c for frase in frases for c in _cantidades_con_unidad(frase)]
    for cantidad, unidad in candidatas:
        if abs(cantidad - valor) < 0.01:
            return cantidad, unidad
    for cantidad, unidad in candidatas:
        if unidad and (abs(cantidad - valor * 1000) < 0.5 or abs(cantidad * 1000 - valor) < 0.5):
            return cantidad, unidad
    return None


def _producto_dicho(nombre: str, texto: str) -> bool:
    return _nombre_esta_en_el_texto(nombre, texto) or _se_dijo(nombre, texto)


def _frases_del_producto(
    transcripcion: str, producto: str, otros_productos: list[str], con_anterior: bool = False
) -> list[str]:
    """Las frases donde se nombra el producto, más la siguiente de cada una ("glifosato, a 2 litros
    por hectárea"), salvo que esa siguiente ya nombre otro producto. Con `con_anterior`, también la
    frase de antes ("hay cogollero, conviene aplicar Coragen")."""
    frases = _frases(transcripcion)
    elegidas: list[str] = []
    for i, frase in enumerate(frases):
        if not _producto_dicho(producto, frase):
            continue
        if con_anterior and i > 0 and frases[i - 1] not in elegidas:
            elegidas.append(frases[i - 1])
        elegidas.append(frase)
        if i + 1 < len(frases) and not any(_producto_dicho(o, frases[i + 1]) for o in otros_productos):
            elegidas.append(frases[i + 1])
    return elegidas


def _producto_es_de_hibrido(transcripcion: str, producto: str, hibrido: str) -> bool:
    """El producto es de un híbrido puntual si ese híbrido se nombra en la misma oración ("En el 9939
    hay cogollero, conviene aplicar Coragen") o en la frase anterior con un "ese"."""
    clave = normalizar_texto(hibrido)
    for oracion in _ORACIONES.split(transcripcion):
        if clave and clave in normalizar_texto(oracion) and _producto_dicho(producto, oracion):
            return True
    return _hallazgo_es_de_hibrido(transcripcion, producto, hibrido)


def _aplicacion_de(
    extraida: _AplicacionExtraida,
    transcripcion: str,
    otros_productos: list[str],
    vocabulario: list[catalogo_mod.EntradaCatalogo],
) -> Aplicacion:
    """La aplicación que dijo el técnico, sin lo que el modelo agregó por su cuenta: la dosis y el
    volumen de caldo solo quedan si se dicen esos números, y los textos si se dicen sus palabras
    cerca del producto (no en la frase de otro producto)."""
    frases = _frases_del_producto(transcripcion, extraida.producto, otros_productos)
    cerca = " ".join(frases)
    dosis = unidad = None
    if extraida.dosis is not None:
        dicha = _dosis_dicha(extraida.dosis, frases)
        if dicha is None:
            logger.warning("Descarto la dosis %s de «%s»: no se dijo", extraida.dosis, extraida.producto)
        else:
            dosis, unidad = dicha[0], dicha[1] or _unidad_normalizada(extraida.unidad)
    volumen = extraida.volumen_caldo or None  # el modelo a veces escribe 0 en vez de omitirlo
    if volumen is not None and not _numero_dicho(volumen, _frases(transcripcion), _CONTEXTO_CALDO):
        logger.warning("Descarto el volumen de caldo %s: no se dijo", volumen)
        volumen = None
    textos = {}
    for campo in ("objetivo", "momento", "coadyuvante"):
        valor = (getattr(extraida, campo) or "").strip()
        if campo == "objetivo":
            con_anterior = " ".join(_frases_del_producto(transcripcion, extraida.producto, otros_productos, True))
            dicho = _nombre_esta_en_el_texto(valor, con_anterior)
        else:
            dicho = _palabras_dichas(valor, cerca)
        if campo == "momento" and _NO_ES_MOMENTO.search(_sin_tildes_y_minusculas(valor)):
            dicho = False
        if valor and not dicho:
            logger.warning("Descarto %s «%s» de «%s»: no se dijo", campo, valor, extraida.producto)
        textos[campo] = valor if valor and dicho else None
    if textos["objetivo"]:
        textos["objetivo"] = catalogo_mod.nombre_de_adversidad(textos["objetivo"], vocabulario)
    ya_aplicado = extraida.ya_aplicado and bool(_YA_APLICADO.search(_sin_tildes_y_minusculas(" ".join(frases))))
    identificado = productos_mod.identificar(extraida.producto, vocabulario)
    return Aplicacion(
        producto=identificado.producto,
        principio_activo=identificado.principio_activo,
        dosis=dosis,
        unidad=unidad,
        volumen_caldo=volumen,
        estado=EstadoAplicacion.REALIZADA if ya_aplicado else EstadoAplicacion.RECOMENDADA,
        **textos,
    )


def _audio_desde_paso_4(
    paso4: _Paso4,
    transcripcion: str,
    conocidos: list[str] | None = None,
    vocabulario: list[catalogo_mod.EntradaCatalogo] | None = None,
) -> RecorridaAudio:
    """Lo del paso 4 como un RecorridaAudio para sumarlo con `combinar`: cada producto va al
    híbrido que se nombra con él o, si no, a todo el lote."""
    audio = RecorridaAudio()
    destino = _repartidor(audio, conocidos or [])
    nombres = [a.producto for a in paso4.aplicaciones]
    dadas: list[Aplicacion] = []
    for extraida in paso4.aplicaciones:
        if not extraida.producto.strip() or not _producto_dicho(extraida.producto, transcripcion):
            logger.warning("Descarto el producto «%s»: el modelo lo mencionó pero no está en el audio", extraida.producto)
            continue
        otros = [n for n in nombres if normalizar_texto(n) != normalizar_texto(extraida.producto)]
        aplicacion = _aplicacion_de(extraida, transcripcion, otros, vocabulario or [])
        de_hibrido = _nombre_detectado(extraida.hibrido, conocidos or []) if extraida.hibrido else None
        if de_hibrido and not _producto_es_de_hibrido(transcripcion, extraida.producto, de_hibrido):
            de_hibrido = None
        fusionar_aplicaciones(destino(de_hibrido).aplicaciones, [aplicacion])
        dadas.append(aplicacion)
    for de_hibrido, aplicacion in _aplicaciones_que_el_modelo_omitio(transcripcion, dadas, conocidos or [], vocabulario or []):
        logger.warning("Agrego «%s»: se nombra para aplicar y el modelo no lo listó", aplicacion.producto)
        fusionar_aplicaciones(destino(de_hibrido).aplicaciones, [aplicacion])
    return audio


def _limites_de_oraciones(texto: str) -> list[tuple[int, int]]:
    """(inicio, fin) de cada oración de `texto` (cortando en puntos que no son decimales)."""
    limites, inicio = [], 0
    for corte in _ORACIONES.finditer(texto):
        limites.append((inicio, corte.start()))
        inicio = corte.end()
    limites.append((inicio, len(texto)))
    return limites


def _aplicaciones_que_el_modelo_omitio(
    transcripcion: str,
    dadas: list[Aplicacion],
    conocidos: list[str],
    vocabulario: list[catalogo_mod.EntradaCatalogo],
) -> list[tuple[str | None, Aplicacion]]:
    """Un modelo chico lista un solo producto aunque se nombren varios ("glifosato 2 litros más
    2,4 D medio litro"). Cada principio activo que se nombra para aplicar (con una palabra como
    "aplicar" antes, o con una dosis después) y el modelo no listó se agrega con lo que se dijo
    en su tramo: desde el producto anterior hasta el siguiente, dentro de la misma oración.

    Devuelve (híbrido, aplicación): el híbrido, si en la oración se nombra uno solo."""
    cubiertos = {normalizar_texto(t) for a in dadas for t in (a.producto, a.principio_activo) if t}
    coadyuvantes = " ".join(normalizar_texto(a.coadyuvante) for a in dadas if a.coadyuvante)
    menciones = productos_mod.productos_nombrados(transcripcion, vocabulario)
    oraciones = _limites_de_oraciones(transcripcion)
    agregadas = []
    for k, mencion in enumerate(menciones):
        identificado = mencion.identificacion
        claves = {normalizar_texto(t) for t in (identificado.producto, identificado.principio_activo) if t}
        if claves & cubiertos or (coadyuvantes and normalizar_texto(mencion.texto) in coadyuvantes):
            continue
        inicio_oracion, fin_oracion = next((a, b) for a, b in oraciones if a <= mencion.inicio <= b)
        desde = max([inicio_oracion, *(m.fin for m in menciones[:k] if inicio_oracion <= m.fin <= mencion.inicio)])
        hasta = min([fin_oracion, *(m.inicio for m in menciones[k + 1:] if m.inicio <= fin_oracion)])
        antes = transcripcion[desde:mencion.inicio]
        despues = _VOLUMEN_DE_CALDO.sub(" ", transcripcion[mencion.fin:hasta])
        cantidades = [c for c in _cantidades_con_unidad(despues) if c[1]]
        if not (_CONTEXTO_APLICACION.search(_sin_tildes_y_minusculas(antes)) or cantidades):
            continue
        if _NO_APLICAR.search(_sin_tildes_y_minusculas(antes.split(",")[-1])):
            continue  # "no hace falta aplicar atrazina"
        tramo = transcripcion[desde:hasta]
        momento = _MOMENTO_DICHO.search(tramo)
        oracion = normalizar_texto(transcripcion[inicio_oracion:fin_oracion])
        hibridos = [h for h in conocidos if normalizar_texto(h) and normalizar_texto(h) in oracion]
        dosis, unidad = cantidades[0] if cantidades else (None, None)
        ya_aplicado = bool(_YA_APLICADO.search(_sin_tildes_y_minusculas(tramo)))
        agregadas.append((hibridos[0] if len(hibridos) == 1 else None, Aplicacion(
            producto=identificado.producto,
            principio_activo=identificado.principio_activo,
            dosis=dosis,
            unidad=unidad,
            momento=momento.group(0) if momento else None,
            estado=EstadoAplicacion.REALIZADA if ya_aplicado else EstadoAplicacion.RECOMENDADA,
        )))
        cubiertos |= claves
    return agregadas


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
    clientes_existentes: list[Cliente] | None = None,
) -> RecorridaAudio:
    """Extrae lo que dice UN audio: datos del lote, datos generales y lista de híbridos.

    Cada audio se extrae por separado; el bot los suma después (`RecorridaAudio.combinar`).
    Si falla algún paso tras reintentar, propaga `ExtraccionError` (no se descarta nada en silencio).
    """
    vocabulario = vocabulario or []
    clientes_existentes = clientes_existentes or []
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

    if menciona_aplicaciones(transcripcion, vocabulario):
        nombres = [h.hibrido_variedad for h in audio.hibridos if h.hibrido_variedad]
        paso4 = await _pedir(config, 4, _prompt_paso_4(transcripcion, nombres))
        audio.combinar(_audio_desde_paso_4(paso4, transcripcion, nombres, vocabulario))

    if menciona_cliente(transcripcion, clientes_existentes):
        paso_cliente = await _pedir(config, 5, _prompt_paso_cliente(transcripcion, clientes_existentes))
        audio.cliente, audio.cliente_id = paso_cliente.cliente, paso_cliente.cliente_id
        _cliente_sin_inventar(audio, transcripcion, clientes_existentes)

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
