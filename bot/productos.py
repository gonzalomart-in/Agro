"""Productos fitosanitarios: el registro oficial de SENASA, para reconocer los productos que se
nombran en los audios y completar su principio activo.

La lista está en bot/datos/productos_senasa.csv (marca, empresa, principios activos y banda
toxicológica de cada producto inscripto) y se actualiza con scripts/actualizar_productos_senasa.py.

El técnico puede nombrar una marca ("Roundup Full II"), una familia de marcas ("Roundup") o
directamente el principio activo ("glifosato"), y Whisper los escribe como suenan ("cletodín").
Se busca en ese orden: vocabulario cargado por el equipo, principio activo, marca exacta, familia
de marcas y, por último, por parecido al oído. Ante la duda no se cambia nada: mejor el nombre
como lo dijo que una marca equivocada.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
from functools import lru_cache
from pathlib import Path

from .catalogo import EntradaCatalogo
from .modelos import normalizar_texto

ARCHIVO_SENASA = Path(__file__).resolve().parent / "datos" / "productos_senasa.csv"

# "GLIFOSATO 48% p/v, 2,4 D 30%" -> ("GLIFOSATO", "2,4 D"); también ",003%" (sin el 0 inicial)
_ACTIVO_CON_CONCENTRACION = re.compile(r"\s*(.+?)\s+(\d*[.,]?\d+)\s*%[^,]*?(?:,(?=\s*\D)|$)")

# lo que se agrega al nombre del principio activo y no se dice en el campo ("haloxifop-P metil" = "haloxifop")
_SUFIJOS_DE_SAL = re.compile(
    r"(?:[\s-]+(?:p|m|metil|etil|meptil|butil|dicloruro|de amonio|de potasio|de sodio|sal \w+|"
    r"sal|potasica|sodica|amina|dimetilamina|isopropilamina|acido))+$",
    re.IGNORECASE,
)

# cómo se dicen algunos principios activos, además de su nombre
_OTROS_NOMBRES = {
    "2,4 D": ["2,4-D", "dos cuatro D", "2 4 D"],
    "2,4-DB": ["dos cuatro DB"],
    "LAMBDA-CIALOTRINA": ["lambda", "lambdacialotrina", "lambda cialotrina"],
    "BENZOATO DE EMAMECTINA": ["emamectina"],
    "S-METOLACLORO": ["metolaclor", "ese metolacloro"],
    "ESTERES METILICOS DE ACIDOS GRASOS DE ACEITE DE SOJA": ["aceite metilado de soja", "aceite metilado"],
    "ESTERES METILICOS DE ACIDOS GRASOS DE ACEITE VEGETAL": ["aceite vegetal metilado"],
    "ALFACIPERMETRINA/ALFAMETRINA": ["alfacipermetrina", "alfametrina"],
}

# parecido mínimo (0 a 1) para aceptar un nombre que no es exacto
_PARECIDO_ACTIVO = 0.86
_PARECIDO_MARCA = 0.9

# palabras genéricas que no son un producto (hay marcas que empiezan así: "HERBICIDA GLIFOSATO...")
_GENERICOS = {
    normalizar_texto(p) for p in (
        "herbicida", "herbicidas", "insecticida", "insecticidas", "fungicida", "fungicidas", "acaricida",
        "graminicida", "coadyuvante", "coadyuvantes", "aceite", "producto", "productos", "curasemilla",
        "fertilizante", "hormonal", "residual", "desecante", "mojante", "surfactante", "tensioactivo",
        "antideriva", "regulador", "terapico", "insecticida biologico",
    )
}


@dataclass(frozen=True)
class ProductoRegistrado:
    registro: str
    marca: str
    empresa: str
    activos: tuple[str, ...]
    banda_toxicologica: str


@dataclass(frozen=True)
class Identificacion:
    """Qué producto es: el nombre para guardar y su principio activo (None si no se sabe)."""

    producto: str
    principio_activo: str | None = None


def clave_fonetica(texto: str) -> str:
    """Cómo suena, para comparar lo que escribe Whisper con el nombre real: 'cletodín' y
    'cletodim', 'Rancawa' y 'Rancagua', 'zulfentrasone' y 'sulfentrazone' dan lo mismo."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]", "", t)
    for patron, reemplazo in (
        (r"ph", "f"), (r"qu", "k"), (r"gu(?=[ei])", "g"), (r"gu(?=a)", "w"), (r"hu(?=[aeio])", "w"),
        (r"ch", "#"), (r"c(?=[ei])", "s"), (r"c", "k"), (r"z", "s"), (r"x", "ks"), (r"v", "b"),
        (r"w", "u"), (r"ll", "y"), (r"h", ""), (r"g(?=[ei])", "j"), (r"y$", "i"), (r"#", "ch"),
    ):
        t = re.sub(patron, reemplazo, t)
    t = re.sub(r"(.)\1+", r"\1", t)
    return re.sub(r"m$", "n", t)


def _nombre_lindo(activo: str) -> str:
    """'2,4 D' -> '2,4 D', 'S-METOLACLORO' -> 'S-metolacloro', 'GLIFOSATO' -> 'glifosato'."""
    texto = re.sub(r"\s+", " ", activo.strip().lower())
    return re.sub(r"\b([a-z])\b", lambda m: m.group(1).upper(), texto)


def activos_de(texto: str) -> tuple[str, ...]:
    """Los principios activos de la columna "activos" de SENASA, sin la concentración."""
    return tuple(_nombre_lindo(nombre) for nombre, _ in _ACTIVO_CON_CONCENTRACION.findall(texto or ""))


def _formas_del_activo(activo: str) -> set[str]:
    """Las formas de decir un principio activo, normalizadas."""
    formas = {activo, _SUFIJOS_DE_SAL.sub("", activo)}
    formas.update(activo.split("/"))
    formas.update(_OTROS_NOMBRES.get(activo.upper(), []))
    return {normalizar_texto(f) for f in formas if len(normalizar_texto(f)) >= 3}


@dataclass(frozen=True)
class _Registro:
    productos: tuple[ProductoRegistrado, ...]
    por_marca: dict[str, ProductoRegistrado]
    por_activo: dict[str, str]
    activo_por_sonido: dict[str, str]
    marca_por_sonido: dict[str, str]


@lru_cache(maxsize=1)
def _registro() -> _Registro:
    productos = []
    if ARCHIVO_SENASA.exists():
        with ARCHIVO_SENASA.open(encoding="utf-8", newline="") as archivo:
            for fila in csv.DictReader(archivo):
                productos.append(ProductoRegistrado(
                    fila["registro"], fila["marca"], fila["empresa"], activos_de(fila["activos"]),
                    fila["banda_toxicologica"],
                ))
    por_marca: dict[str, ProductoRegistrado] = {}
    por_activo: dict[str, str] = {}
    for producto in productos:
        por_marca.setdefault(normalizar_texto(producto.marca), producto)
        for activo in producto.activos:
            for forma in _formas_del_activo(activo):
                por_activo.setdefault(forma, activo)
    activo_por_sonido = {clave_fonetica(forma): activo for forma, activo in por_activo.items()}
    marca_por_sonido = {clave_fonetica(clave): clave for clave in por_marca}
    return _Registro(tuple(productos), por_marca, por_activo, activo_por_sonido, marca_por_sonido)


def productos_registrados() -> tuple[ProductoRegistrado, ...]:
    return _registro().productos


def _activo_de_marca(producto: ProductoRegistrado) -> str | None:
    return ", ".join(producto.activos) or None


def _activo_parecido(texto: str) -> str | None:
    """El principio activo que suena como `texto` (None si ninguno se parece lo suficiente)."""
    registro = _registro()
    clave = normalizar_texto(texto)
    if clave in registro.por_activo:
        return registro.por_activo[clave]
    sonido = clave_fonetica(texto)
    if len(sonido) < 5:
        return None
    parecidos = get_close_matches(sonido, list(registro.activo_por_sonido), n=1, cutoff=_PARECIDO_ACTIVO)
    return registro.activo_por_sonido[parecidos[0]] if parecidos else None


def _familia_de_marcas(clave: str) -> str | None:
    """'Roundup' -> glifosato, si todas las marcas que empiezan así tienen los mismos activos."""
    if len(clave) < 5:
        return None
    activos = {p.activos for c, p in _registro().por_marca.items() if c.startswith(clave) and p.activos}
    return ", ".join(activos.pop()) if len(activos) == 1 else None


def _marca_parecida(texto: str) -> ProductoRegistrado | None:
    """La marca que suena como `texto`, solo si no hay otra casi igual de parecida con otros activos."""
    registro = _registro()
    sonido = clave_fonetica(texto)
    if len(sonido) < 5:
        return None
    parecidos = get_close_matches(sonido, list(registro.marca_por_sonido), n=3, cutoff=_PARECIDO_MARCA)
    if not parecidos:
        return None
    mejor = registro.por_marca[registro.marca_por_sonido[parecidos[0]]]
    for otro in parecidos[1:]:
        candidato = registro.por_marca[registro.marca_por_sonido[otro]]
        cerca = SequenceMatcher(None, sonido, otro).ratio() >= SequenceMatcher(None, sonido, parecidos[0]).ratio() - 0.02
        if cerca and candidato.activos != mejor.activos:
            return None
    return mejor


def _del_vocabulario(nombre: str, vocabulario: list[EntradaCatalogo]) -> str | None:
    clave = normalizar_texto(nombre)
    for entrada in vocabulario:
        if entrada.tipo == "producto" and clave in {normalizar_texto(t) for t in [entrada.nombre, *entrada.sinonimos]}:
            return entrada.nombre
    return None


def identificar(nombre: str, vocabulario: list[EntradaCatalogo] | None = None) -> Identificacion:
    """Qué producto es `nombre` (como lo escribió el modelo) y cuál es su principio activo."""
    nombre = re.sub(r"\s+", " ", nombre or "").strip()
    oficial = _del_vocabulario(nombre, vocabulario or [])
    if oficial is not None:
        nombre = oficial
    registro = _registro()
    clave = normalizar_texto(nombre)
    if not clave or clave in _GENERICOS:
        return Identificacion(nombre)

    if clave in registro.por_activo:
        return Identificacion(nombre, registro.por_activo[clave])
    if clave in registro.por_marca:
        producto = registro.por_marca[clave]
        # si el equipo lo cargó en su vocabulario, queda escrito como lo cargaron
        return Identificacion(nombre if oficial else producto.marca, _activo_de_marca(producto))
    de_la_familia = _familia_de_marcas(clave)
    if de_la_familia:
        return Identificacion(nombre, de_la_familia)
    if oficial is None:
        # el nombre entero suena como un principio activo ("cletodín"): se guarda bien escrito
        activo = _activo_parecido(nombre)
        if activo:
            return Identificacion(activo, activo)
        marca = _marca_parecida(nombre)
        if marca is not None:
            return Identificacion(marca.marca, _activo_de_marca(marca))
    # una palabra (o dos seguidas) del nombre es un principio activo ("cletodim macro 24")
    palabras = nombre.split()
    for tamano in (2, 1):
        for i in range(len(palabras) - tamano + 1):
            grupo = " ".join(palabras[i:i + tamano])
            activo = None if normalizar_texto(grupo) in _GENERICOS else _activo_parecido(grupo)
            if activo:
                return Identificacion(nombre, activo)
    return Identificacion(nombre)


@dataclass(frozen=True)
class Mencion:
    """Dónde se nombra un producto en el texto (posiciones de inicio y fin) y qué producto es."""

    inicio: int
    fin: int
    texto: str
    identificacion: Identificacion


def productos_nombrados(texto: str, vocabulario: list[EntradaCatalogo] | None = None) -> list[Mencion]:
    """Los principios activos registrados (y los productos del vocabulario del equipo) que se
    nombran en `texto`, en orden. No busca marcas: hay miles y algunas son palabras comunes.
    Un principio activo mal escrito cuenta solo si suena igual ("cletodín", "atracina")."""
    registro = _registro()
    del_vocabulario = {
        normalizar_texto(t): entrada.nombre
        for entrada in vocabulario or [] if entrada.tipo == "producto"
        for t in [entrada.nombre, *entrada.sinonimos]
        if len(normalizar_texto(t)) >= 4
    }
    palabras = list(re.finditer(r"[\w,\-/]+", texto or ""))
    menciones: list[Mencion] = []
    i = 0
    while i < len(palabras):
        encontrada = None
        for n in (4, 3, 2, 1):
            if i + n > len(palabras):
                continue
            crudo = texto[palabras[i].start():palabras[i + n - 1].end()]
            dicho = crudo.strip(",-/")
            inicio = palabras[i].start() + len(crudo) - len(crudo.lstrip(",-/"))
            clave = normalizar_texto(dicho)
            # "24d" (2,4 D) es corto, pero con números no se confunde con una palabra
            if len(clave) < (3 if re.search(r"\d", clave) else 4) or clave in _GENERICOS:
                continue
            sonido_de_activo = n == 1 and len(clave) >= 6 and clave_fonetica(dicho) in registro.activo_por_sonido
            if clave in del_vocabulario or clave in registro.por_activo or sonido_de_activo:
                encontrada = (n, inicio, dicho)
                break
        if encontrada is None:
            i += 1
            continue
        n, inicio, dicho = encontrada
        menciones.append(Mencion(inicio, inicio + len(dicho), dicho, identificar(dicho, vocabulario)))
        i += n
    return menciones


def _grupos_de_palabras(texto: str, hasta: int = 4) -> set[str]:
    """Cada palabra del texto y cada grupo de hasta `hasta` palabras seguidas, normalizados."""
    palabras = re.findall(r"[\w,.\-/]+", texto or "")
    return {
        normalizar_texto(" ".join(palabras[i:i + n]))
        for n in range(1, hasta + 1)
        for i in range(len(palabras) - n + 1)
    }


def nombra_algun_producto(texto: str, vocabulario: list[EntradaCatalogo] | None = None) -> bool:
    """True si en `texto` se nombra algún principio activo registrado (aunque esté mal escrito)
    o algún producto del vocabulario del equipo."""
    grupos = _grupos_de_palabras(texto)
    for entrada in vocabulario or []:
        if entrada.tipo == "producto" and grupos & {normalizar_texto(t) for t in [entrada.nombre, *entrada.sinonimos]}:
            return True
    registro = _registro()
    if any(g in registro.por_activo for g in grupos if len(g) >= 4):
        return True
    sonidos = {clave_fonetica(p) for p in re.findall(r"\w+", texto or "") if len(p) >= 6}
    return bool(sonidos & set(registro.activo_por_sonido))
