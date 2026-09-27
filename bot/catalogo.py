"""Vocabulario precargado a mano (híbridos, malezas, plagas, etc.).

Sirve para dos cosas: se le pasa al modelo para que interprete mejor lo que
se dice en los audios, y se usa después para unificar los nombres (que
"conyza" y "rama negra" queden guardados igual).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher, get_close_matches

from .modelos import RecorridaAudio, etiqueta_material, normalizar_texto

TIPOS = ("hibrido", "maleza", "plaga", "enfermedad", "producto", "ensayo", "localidad", "termino", "nota")

TITULOS = {
    "hibrido": "Híbridos/variedades",
    "maleza": "Malezas",
    "plaga": "Plagas",
    "enfermedad": "Enfermedades",
    "producto": "Productos fitosanitarios",
    "ensayo": "Ensayos",
    "localidad": "Localidades",
    "termino": "Términos técnicos (palabra correcta = cómo la escribe mal)",
    "nota": "Notas",
}

_ALIAS_TIPO = {
    "hibrido": "hibrido",
    "hibridos": "hibrido",
    "variedad": "hibrido",
    "variedades": "hibrido",
    "maleza": "maleza",
    "malezas": "maleza",
    "plaga": "plaga",
    "plagas": "plaga",
    "enfermedad": "enfermedad",
    "enfermedades": "enfermedad",
    "producto": "producto",
    "productos": "producto",
    "fitosanitario": "producto",
    "fitosanitarios": "producto",
    "agroquimico": "producto",
    "agroquimicos": "producto",
    "ensayo": "ensayo",
    "ensayos": "ensayo",
    "localidad": "localidad",
    "localidades": "localidad",
    "nota": "nota",
    "notas": "nota",
    "termino": "termino",
    "terminos": "termino",
    "palabra": "termino",
    "palabras": "termino",
    "cultivar": "hibrido",
    "cultivares": "hibrido",
}

# Con más entradas que esto el prompt se vuelve demasiado largo para un modelo chico.
MAX_ENTRADAS_EN_PROMPT = 300


@dataclass
class EntradaCatalogo:
    tipo: str
    nombre: str
    sinonimos: list[str] = field(default_factory=list)


def tipo_valido(texto: str | None) -> str | None:
    """'Híbridos' -> 'hibrido'; devuelve None si no es un tipo conocido."""
    return _ALIAS_TIPO.get(normalizar_texto(texto))


def parsear_entradas(tipo: str, texto: str) -> list[EntradaCatalogo]:
    """Una entrada por línea; los sinónimos van después de un '=' separados por coma.

    'rama negra = conyza, buva' -> EntradaCatalogo('maleza', 'rama negra', ['conyza', 'buva'])
    """
    entradas = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        if tipo == "nota":
            entradas.append(EntradaCatalogo(tipo, linea))
            continue
        nombre, _, resto = linea.partition("=")
        nombre = nombre.strip()
        if not nombre:
            continue
        sinonimos = [s.strip() for s in resto.split(",") if s.strip()]
        entradas.append(EntradaCatalogo(tipo, nombre, sinonimos))
    return entradas


def formatear_para_prompt(entradas: list[EntradaCatalogo]) -> str:
    """Texto del vocabulario para el prompt del modelo ('' si no hay nada cargado)."""
    if not entradas:
        return ""
    lineas = ["Vocabulario precargado por el equipo (usalo para interpretar el audio y corregir nombres mal transcriptos):"]
    for tipo in TIPOS:
        de_este_tipo = [e for e in entradas if e.tipo == tipo][:MAX_ENTRADAS_EN_PROMPT]
        if not de_este_tipo:
            continue
        if tipo == "nota":
            lineas.extend(f"- Nota: {e.nombre}" for e in de_este_tipo)
            continue
        partes = []
        for e in de_este_tipo:
            partes.append(f"{e.nombre} (también: {', '.join(e.sinonimos)})" if e.sinonimos else e.nombre)
        lineas.append(f"- {TITULOS[tipo]}: " + "; ".join(partes))
    return "\n".join(lineas)


def formatear_listado(entradas: list[EntradaCatalogo]) -> str:
    """Texto para mostrarle al usuario qué hay cargado."""
    if not entradas:
        return "Todavía no hay nada cargado. Usá /agregar para precargar híbridos, malezas, plagas, etc."
    lineas = []
    for tipo in TIPOS:
        de_este_tipo = [e for e in entradas if e.tipo == tipo]
        if not de_este_tipo:
            continue
        lineas.append(f"{TITULOS[tipo]}:")
        for e in de_este_tipo:
            extra = f" = {', '.join(e.sinonimos)}" if e.sinonimos else ""
            lineas.append(f"  • {e.nombre}{extra}")
        lineas.append("")
    return "\n".join(lineas).rstrip()


def _indice(entradas: list[EntradaCatalogo], tipo: str) -> dict[str, str]:
    """clave normalizada (del nombre o de un sinónimo) -> nombre oficial."""
    indice: dict[str, str] = {}
    for e in entradas:
        if e.tipo != tipo:
            continue
        for texto in [e.nombre, *e.sinonimos]:
            clave = normalizar_texto(texto)
            if clave:
                indice.setdefault(clave, e.nombre)
    return indice


def clave_hibrido(nombre: str) -> str:
    """'híbrido 9939' y '9939' comparan igual."""
    clave = normalizar_texto(nombre)
    for prefijo in ("hibrido", "variedad"):
        if clave.startswith(prefijo) and len(clave) > len(prefijo):
            return clave[len(prefijo):]
    return clave


def _oficial(nombre: str, indice: dict[str, str], clave: str, aproximar: bool) -> str:
    if clave in indice:
        return indice[clave]
    if aproximar:
        parecido = get_close_matches(clave, list(indice), n=1, cutoff=0.85)
        if parecido:
            return indice[parecido[0]]
    return nombre


def _lugar_oficial(nombre: str, indice: dict[str, str]) -> str:
    """Como `_oficial`, pero tolera más diferencia: Whisper escribe mal los nombres de pueblos
    que no conoce ("Rancawa" por "Rancagua"). Para no confundir dos pueblos distintos, el
    parecido tiene que empezar con la misma letra."""
    clave = normalizar_texto(nombre)
    if clave in indice:
        return indice[clave]
    candidatos = [c for c in indice if c[:1] == clave[:1]]
    parecido = get_close_matches(clave, candidatos, n=1, cutoff=0.75)
    return indice[parecido[0]] if parecido else nombre


def unificar_nombres(audio: RecorridaAudio, entradas: list[EntradaCatalogo]) -> None:
    """Reemplaza, en el propio `audio`, los nombres por el nombre oficial del catálogo.

    Los híbridos solo se unifican por coincidencia exacta (9939 y 9938 son
    híbridos distintos); malezas, plagas, enfermedades y ensayo toleran
    pequeñas diferencias (plural, tilde, una letra de más), y la localidad
    un poco más (ver `_lugar_oficial`).
    """
    if not entradas:
        return

    hibridos = {}
    for e in entradas:
        if e.tipo == "hibrido":
            for texto in [e.nombre, *e.sinonimos]:
                hibridos.setdefault(clave_hibrido(texto), e.nombre)
    malezas = _indice(entradas, "maleza")
    plagas = _indice(entradas, "plaga")
    enfermedades = _indice(entradas, "enfermedad")
    ensayos = _indice(entradas, "ensayo")
    localidades = _indice(entradas, "localidad")

    if audio.ensayo:
        audio.ensayo = _oficial(audio.ensayo, ensayos, normalizar_texto(audio.ensayo), True)
    if audio.localidad:
        audio.localidad = _lugar_oficial(audio.localidad, localidades)

    def unificar_relevamiento(rel) -> None:
        for m in rel.malezas:
            m.nombre = _oficial(m.nombre, malezas, normalizar_texto(m.nombre), True)
        for p in rel.plagas:
            p.nombre = _oficial(p.nombre, plagas, normalizar_texto(p.nombre), True)
        for enf in rel.enfermedades:
            enf.nombre = _oficial(enf.nombre, enfermedades, normalizar_texto(enf.nombre), True)

    unificar_relevamiento(audio)
    for h in audio.hibridos:
        if h.hibrido_variedad:
            clave = clave_hibrido(h.hibrido_variedad)
            h.hibrido_variedad = hibridos.get(clave, h.hibrido_variedad)
        unificar_relevamiento(h)


def tipo_de_adversidad(nombre: str, entradas: list[EntradaCatalogo]) -> str | None:
    """"maleza", "plaga" o "enfermedad" según el vocabulario ("cogollero" -> "plaga"). None si no
    está, o si el mismo nombre aparece en más de un tipo."""
    clave = normalizar_texto(nombre)
    tipos = {tipo for tipo in ("maleza", "plaga", "enfermedad") if clave in _indice(entradas, tipo)}
    if not tipos:
        tipos = {
            tipo for tipo in ("maleza", "plaga", "enfermedad")
            if get_close_matches(clave, list(_indice(entradas, tipo)), n=1, cutoff=0.85)
        }
    return tipos.pop() if len(tipos) == 1 else None


def nombre_de_adversidad(nombre: str, entradas: list[EntradaCatalogo]) -> str:
    """El nombre oficial de una maleza, plaga o enfermedad ("conyza" -> "rama negra"), para lo que
    se quiere controlar con un producto. Si no está en el vocabulario, queda como está."""
    clave = normalizar_texto(nombre)
    for tipo in ("maleza", "plaga", "enfermedad"):
        indice = _indice(entradas, tipo)
        if clave in indice:
            return indice[clave]
    for tipo in ("maleza", "plaga", "enfermedad"):
        oficial = _oficial(nombre, _indice(entradas, tipo), clave, True)
        if oficial != nombre:
            return oficial
    return nombre


def _funcion_clave(tipo: str):
    return clave_hibrido if tipo == "hibrido" else normalizar_texto


def _se_parecen(a: str, b: str) -> bool:
    """Una clave contiene a la otra ("9939" en "st9939vip3") o son casi iguales."""
    if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.85


def buscar_coincidencias(
    tipo: str, nombre: str, entradas: list[EntradaCatalogo]
) -> tuple[EntradaCatalogo | None, list[EntradaCatalogo]]:
    """Busca en el vocabulario lo que ya existe igual (o parecido) a `nombre`.

    Devuelve (exacta, parecidas): `exacta` es la entrada cuyo nombre o algún sinónimo
    es lo mismo escrito distinto (tildes, mayúsculas, espacios); `parecidas` son las
    que se le parecen sin serlo, y solo se llena si no hay una exacta.
    """
    clave_de = _funcion_clave(tipo)
    clave = clave_de(nombre)
    parecidas = []
    for entrada in entradas:
        if entrada.tipo != tipo:
            continue
        claves = [c for c in (clave_de(t) for t in [entrada.nombre, *entrada.sinonimos]) if c]
        if clave in claves:
            return entrada, []
        if tipo not in ("nota", "termino") and clave and any(_se_parecen(clave, c) for c in claves):
            parecidas.append(entrada)
    return None, parecidas


def sinonimos_utiles(oficial: str, sinonimos: list[str]) -> list[str]:
    """Sinónimos sin repetidos y sin el propio nombre oficial."""
    vistos = {normalizar_texto(oficial)}
    resultado = []
    for s in sinonimos:
        clave = normalizar_texto(s)
        if clave and clave not in vistos:
            vistos.add(clave)
            resultado.append(s.strip())
    return resultado


# Whisper solo lee los últimos ~224 tokens de la pista: se le da un presupuesto en caracteres.
PRESUPUESTO_PISTA_WHISPER = 650
PRESUPUESTO_PRODUCTOS_WHISPER = 100


def _lista_que_entre(prefijo: str, nombres: list[str], presupuesto: int) -> str:
    """'prefijo: a, b, c.' con tantos nombres como entren en `presupuesto` caracteres."""
    texto = prefijo
    for i, nombre in enumerate(nombres):
        candidato = f"{texto}{', ' if i else ' '}{nombre}"
        if len(candidato) + 1 > presupuesto:
            break
        texto = candidato
    return texto + "." if texto != prefijo else ""


def texto_para_whisper(entradas: list[EntradaCatalogo], cultivo: str | None = None) -> str:
    """Pista para Whisper: los híbridos/variedades cargados, las localidades, los productos y las
    palabras técnicas.

    Solo listas de nombres, sin frases de ejemplo: Whisper a veces "repite" la pista como si se
    hubiera dicho, y una frase como "el 9939 tiene 3,5 plantas al metro" terminaba cargada como
    dato. Lo que se cuele igual lo saca `quitar_eco_de_pista`.

    Si no entra todo, se conserva lo más importante. Whisper se queda con el final del texto,
    así que lo más importante va al final.
    """
    def nombres(tipo: str) -> list[str]:
        return [e.nombre for e in entradas if e.tipo == tipo]

    etiqueta = etiqueta_material(cultivo)[1].capitalize()

    # de más a menos importante: cada parte usa lo que queda del presupuesto (los productos, como
    # mucho PRESUPUESTO_PRODUCTOS_WHISPER, para no dejar sin lugar a los términos)
    partes_por_importancia: list[str] = []
    restante = PRESUPUESTO_PISTA_WHISPER
    for prefijo, lista, tope in (
        (f"{etiqueta}:", nombres("hibrido"), None),
        ("Localidades:", nombres("localidad"), None),
        ("Productos:", nombres("producto"), PRESUPUESTO_PRODUCTOS_WHISPER),
        ("Términos:", nombres("termino"), None),
        ("Malezas, plagas y enfermedades:", nombres("maleza") + nombres("plaga") + nombres("enfermedad"), None),
    ):
        if not lista:
            continue
        parte = _lista_que_entre(prefijo, lista, min(restante, tope or restante))
        if parte and len(parte) <= restante:
            partes_por_importancia.append(parte)
            restante -= len(parte) + 1
    return " ".join(reversed(partes_por_importancia))


# Una oración de la transcripción que está entera dentro de la pista es un eco de la pista, no algo
# dicho. Las oraciones muy cortas ("San Pedro.") se dejan: pueden coincidir de casualidad.
_LARGO_MINIMO_DE_ECO = 20
# corta después de . ; ! ? o salto de línea, sin partir decimales ("3.5"); el separador se conserva
_FIN_DE_ORACION = re.compile(r"((?<=[.;!?\n])(?!\d)\s*)")


def quitar_eco_de_pista(texto: str, pista: str) -> str:
    """Saca de la transcripción lo que Whisper copió de la pista en vez de escucharlo."""
    pista_normalizada = normalizar_texto(pista)
    if not pista_normalizada:
        return texto
    partes = _FIN_DE_ORACION.split(texto)
    conservado = []
    for i in range(0, len(partes), 2):
        oracion, separador = partes[i], partes[i + 1] if i + 1 < len(partes) else ""
        clave = normalizar_texto(oracion)
        if len(clave) >= _LARGO_MINIMO_DE_ECO and clave in pista_normalizada:
            continue
        conservado.append(oracion + separador)
    return "".join(conservado).strip()


_SEPARADOR_PARTIDO = r"[\s,.\-]{0,2}"
_DECIMAL_PARTIDO = re.compile(
    r"(?<![\d,.])(\d{1,3})\s*[,.]\s+(\d{1,2})"
    r"(?=\s*(?:plantas?\b|pl\b|%|por\s+ciento|por\s+metro|al\s+metro|cm\b|cent[ií]metros|metros?\b|"
    r"litros?\b|l\b|l/ha\b|cc\b|gramos?\b|kilos?\b|kg\b))",
    re.IGNORECASE,
)
_DECIMAL_HABLADO = re.compile(r"(?<![\d,.])(\d{1,3})\s+coma\s+(\d{1,2})(?!\d)", re.IGNORECASE)


_NUMEROS_HABLADOS = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
}
_ETAPA_FENOLOGICA = re.compile(
    r"(?<![A-Za-z0-9])([VR])[\s\-]*(\d{1,2}|" + "|".join(_NUMEROS_HABLADOS) + r")(?!\d|[.,]\d|[A-Za-z])",
    re.IGNORECASE,
)


def _etapa_bien_escrita(m: re.Match) -> str:
    numero = m.group(2).lower()
    return f"{m.group(1).upper()}{_NUMEROS_HABLADOS.get(numero, numero)}"


def normalizar_transcripcion(texto: str, entradas: list[EntradaCatalogo]) -> str:
    """Arregla lo que Whisper suele escribir mal.

    - "99, 39" -> "9939", pero solo si 9939 es un híbrido/variedad precargado (si no,
      no hay forma segura de distinguirlo de dos números distintos).
    - Palabras técnicas mal transcriptas ("válida" -> "variedad"), según los "términos".
    - Etapas fenológicas: "V 4" y "v cuatro" -> "V4".
    - "3, 2 plantas al metro" y "3 coma 2" -> "3,2".
    """
    candidatos: dict[str, str] = {}
    for e in entradas:
        if e.tipo != "hibrido":
            continue
        for nombre in [e.nombre, *e.sinonimos]:
            clave = normalizar_texto(nombre)
            if len(clave) >= 3:
                candidatos.setdefault(clave, e.nombre)

    # los más largos primero, para que 98RR2 no se coma a 98RR
    for clave in sorted(candidatos, key=len, reverse=True):
        patron = re.compile(
            r"(?<![0-9A-Za-z])"
            + _SEPARADOR_PARTIDO.join(re.escape(c) for c in clave)
            + r"(?![0-9A-Za-z])",
            re.IGNORECASE,
        )
        texto = patron.sub(lambda _m, nombre=candidatos[clave]: nombre, texto)

    for entrada in entradas:
        if entrada.tipo != "termino":
            continue
        for mal_escrita in entrada.sinonimos:
            if normalizar_texto(mal_escrita) == normalizar_texto(entrada.nombre):
                continue
            patron = re.compile(r"(?<!\w)" + re.escape(mal_escrita) + r"(?!\w)", re.IGNORECASE)
            texto = patron.sub(lambda _m, correcta=entrada.nombre: correcta, texto)

    texto = _ETAPA_FENOLOGICA.sub(_etapa_bien_escrita, texto)
    texto = _DECIMAL_HABLADO.sub(r"\1,\2", texto)
    return _DECIMAL_PARTIDO.sub(r"\1,\2", texto)
