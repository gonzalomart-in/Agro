"""Correcciones manuales sobre el borrador (comandos /corregir y /eliminar).

Se hacen "a mano", sin pasar por el modelo de lenguaje, para que un dato mal
entendido se arregle de forma exacta.
"""
from __future__ import annotations

from .modelos import RecorridaAudio, UmbralDanoEconomico, UnidadStand, normalizar_texto

AYUDA_CORREGIR = (
    "Cómo corregir el borrador (los números de híbrido salen en /borrador):\n\n"
    "Datos del lote:\n"
    "/corregir lote Las Lilas\n"
    "Campos: provincia, localidad, lote, cultivo, ensayo, estadio\n\n"
    "Datos de un híbrido (n = su número):\n"
    "/corregir 2 stand 3,1\n"
    "/corregir 2 hibrido 9938\n"
    "Campos: hibrido, stand, estado, tratamiento, acciones, comentarios, umbral\n"
    "(umbral: superado, cercano, no_superado, no_evaluado)\n\n"
    "Dejar constancia de que NO hay algo:\n"
    "/corregir 2 sin plagas\n"
    "/corregir todos sin enfermedades\n"
    "Opciones: malezas, plagas, enfermedades\n\n"
    "Borrar un dato: /corregir 2 estado -\n"
    "Vaciar una lista: /corregir 2 limpiar plagas\n"
    "Sacar un híbrido del borrador: /eliminar 2\n\n"
    "Para agregar o cambiar más cosas también podés mandar otro audio o texto: "
    "si nombra un híbrido que ya está, lo actualiza."
)

_CAMPOS_LOTE = {
    "provincia": "provincia",
    "localidad": "localidad",
    "lote": "lote",
    "cultivo": "cultivo",
    "ensayo": "ensayo",
    "estadio": "estadio_fenologico",
}

_CAMPOS_HIBRIDO_TEXTO = {
    "hibrido": "hibrido_variedad",
    "variedad": "hibrido_variedad",
    "estado": "estado_cultivo",
    "tratamiento": "tratamiento",
    "acciones": "acciones",
    "comentarios": "comentarios",
}

_LISTAS = {
    "maleza": "malezas",
    "malezas": "malezas",
    "plaga": "plagas",
    "plagas": "plagas",
    "enfermedad": "enfermedades",
    "enfermedades": "enfermedades",
}

_BORRAR = {"-", "borrar", "nada", "ninguno"}


class ErrorCorreccion(Exception):
    """El comando no se entendió; el mensaje explica qué corregir."""


def _valor_o_none(texto: str) -> str | None:
    texto = texto.strip()
    return None if texto.lower() in _BORRAR else texto


def _numero_de_hibrido(texto: str, borrador: RecorridaAudio) -> int:
    try:
        n = int(texto)
    except ValueError:
        raise ErrorCorreccion(f"«{texto}» no es un número de híbrido.") from None
    if not 1 <= n <= len(borrador.hibridos):
        raise ErrorCorreccion(
            f"No existe el híbrido {n}. El borrador tiene {len(borrador.hibridos)} (mirá /borrador)."
        )
    return n


def _lista(nombre: str) -> str:
    lista = _LISTAS.get(normalizar_texto(nombre))
    if lista is None:
        raise ErrorCorreccion("Indicá malezas, plagas o enfermedades.")
    return lista


def aplicar_correccion(borrador: RecorridaAudio, argumentos: str) -> str:
    """Aplica al borrador lo que pide `argumentos` y devuelve el mensaje de confirmación.

    Lanza `ErrorCorreccion` (con un mensaje entendible) si no se pudo aplicar.
    """
    partes = argumentos.strip().split(None, 1)
    if not partes:
        raise ErrorCorreccion(AYUDA_CORREGIR)
    primero, resto = partes[0], (partes[1] if len(partes) > 1 else "")

    campo_lote = _CAMPOS_LOTE.get(normalizar_texto(primero))
    if campo_lote is not None:
        if not resto.strip():
            raise ErrorCorreccion(f"Falta el valor. Ejemplo: /corregir {primero.lower()} Las Lilas")
        setattr(borrador, campo_lote, _valor_o_none(resto))
        if campo_lote == "lote":
            borrador.lote_id = None
        return f"✅ {primero.lower()} = {getattr(borrador, campo_lote) or '—'}"

    if normalizar_texto(primero) == "todos":
        objetivo, etiqueta = None, "todos los híbridos"
    else:
        objetivo = _numero_de_hibrido(primero, borrador)
        etiqueta = f"híbrido {objetivo}"

    partes = resto.strip().split(None, 1)
    if not partes:
        raise ErrorCorreccion(AYUDA_CORREGIR)
    campo, valor = normalizar_texto(partes[0]), (partes[1] if len(partes) > 1 else "")

    if campo == "sin":
        lista = _lista(valor)
        if objetivo is None:
            setattr(borrador, lista, [])
            setattr(borrador, f"sin_{lista}", True)
        else:
            hibrido = borrador.hibridos[objetivo - 1]
            setattr(hibrido, lista, [])
            setattr(hibrido, f"sin_{lista}", True)
        return f"✅ {etiqueta}: sin {lista} (confirmado)"

    if campo == "limpiar":
        lista = _lista(valor)
        destino = borrador if objetivo is None else borrador.hibridos[objetivo - 1]
        setattr(destino, lista, [])
        setattr(destino, f"sin_{lista}", False)
        return f"✅ {etiqueta}: {lista} vaciadas (quedan como no informado)"

    if objetivo is None:
        raise ErrorCorreccion(
            "Con «todos» solo se puede usar «sin» o «limpiar». Para el resto, indicá el número de híbrido."
        )
    hibrido = borrador.hibridos[objetivo - 1]

    if campo == "stand":
        if not valor.strip():
            raise ErrorCorreccion("Falta el valor. Ejemplo: /corregir 2 stand 3,1")
        texto = valor.strip().replace(",", ".")
        if texto.lower() in _BORRAR:
            hibrido.stand_valor = None
            return f"✅ {etiqueta}: stand borrado"
        try:
            hibrido.stand_valor = float(texto)
        except ValueError:
            raise ErrorCorreccion(f"«{valor}» no es un número. Ejemplo: /corregir 2 stand 3,1") from None
        if hibrido.stand_unidad is None:
            hibrido.stand_unidad = UnidadStand.PL_M_LINEAL
        return f"✅ {etiqueta}: stand = {hibrido.stand_valor} {hibrido.stand_unidad.value}"

    if campo == "umbral":
        try:
            hibrido.umbral_dano_economico = UmbralDanoEconomico(valor.strip().lower())
        except ValueError:
            opciones = ", ".join(u.value for u in UmbralDanoEconomico)
            raise ErrorCorreccion(f"Umbral inválido. Opciones: {opciones}") from None
        return f"✅ {etiqueta}: umbral = {hibrido.umbral_dano_economico.value}"

    atributo = _CAMPOS_HIBRIDO_TEXTO.get(campo)
    if atributo is None:
        raise ErrorCorreccion(f"No conozco el campo «{partes[0]}».\n\n{AYUDA_CORREGIR}")
    if not valor.strip():
        raise ErrorCorreccion(f"Falta el valor. Ejemplo: /corregir {primero} {partes[0]} algo")
    setattr(hibrido, atributo, _valor_o_none(valor))
    return f"✅ {etiqueta}: {partes[0].lower()} = {getattr(hibrido, atributo) or '—'}"


def eliminar_hibrido(borrador: RecorridaAudio, argumentos: str) -> str:
    """Saca un híbrido del borrador por su número y devuelve el mensaje de confirmación."""
    if not argumentos.strip():
        raise ErrorCorreccion("Indicá el número del híbrido. Ejemplo: /eliminar 2 (los números salen en /borrador)")
    n = _numero_de_hibrido(argumentos.strip().split()[0], borrador)
    quitado = borrador.hibridos.pop(n - 1)
    return f"🗑️ Saqué el híbrido {n} ({quitado.hibrido_variedad or 'sin nombre'}) del borrador."
