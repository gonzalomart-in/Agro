"""Lógica del panel web (panel.py): pasar los registros a filas de tabla, detectar qué se
cambió y validar lo que se carga. No usa Streamlit ni la base, así se puede probar sola."""
from __future__ import annotations

import json
import math

from . import catalogo as catalogo_mod
from . import productos as productos_mod
from .catalogo import EntradaCatalogo
from .exportar import _fecha_hora_argentina_sin_tz
from .ficha import _corto_aplicacion, _corto_enfermedad, _corto_maleza, _corto_plaga, _resumen_de_lista
from .modelos import Aplicacion, Enfermedad, EstadoAplicacion, Maleza, Plaga, UmbralDanoEconomico

UMBRAL_TEXTO = {
    UmbralDanoEconomico.NO_EVALUADO.value: "No evaluado",
    UmbralDanoEconomico.NO_SUPERADO.value: "No superado",
    UmbralDanoEconomico.CERCANO.value: "Cercano",
    UmbralDanoEconomico.SUPERADO.value: "Superado",
}

# campo -> título de la columna que se ve en el panel
COLUMNAS_RECORRIDA = {
    "provincia": "Provincia",
    "localidad": "Localidad",
    "lote": "Lote",
    "cultivo": "Cultivo",
    "ensayo": "Ensayo",
    "tratamiento": "Tratamiento",
    "estadio_fenologico": "Estadio",
    "hibrido_variedad": "Híbrido/variedad",
    "stand_valor": "Stand (pl/m)",
    "estado_cultivo": "Estado",
    "umbral_dano_economico": "Umbral",
    "acciones": "Acciones",
    "comentarios": "Comentarios",
}
COLUMNAS_LOTE = {
    "nombre": "Nombre",
    "localidad": "Localidad",
    "cultivo_habitual": "Cultivo habitual",
    "ensayo_habitual": "Ensayo habitual",
}
COLUMNAS_CLIENTE = {"nombre": "Nombre"}
SIN_CLIENTE = "— (sin cliente)"
LISTAS = {"malezas": "Malezas", "plagas": "Plagas", "enfermedades": "Enfermedades"}
CAMPOS_ITEMS = {
    "malezas": {"nombre": "Nombre", "tamano": "Tamaño", "porcentaje_cobertura": "% cobertura", "observacion": "Observación"},
    "plagas": {"nombre": "Nombre", "cantidad_por_metro_lineal": "Por metro", "porcentaje_dano": "% daño", "observacion": "Observación"},
    "enfermedades": {"nombre": "Nombre", "porcentaje_incidencia": "% incidencia", "severidad": "Severidad", "observacion": "Observación"},
}
_MODELO_ITEM = {"malezas": Maleza, "plagas": Plaga, "enfermedades": Enfermedad}
_CORTO = {"malezas": _corto_maleza, "plagas": _corto_plaga, "enfermedades": _corto_enfermedad}

# productos a aplicar o ya aplicados
CAMPOS_APLICACION = {
    "estado": "Estado",
    "producto": "Producto",
    "principio_activo": "Principio activo",
    "dosis": "Dosis",
    "unidad": "Unidad",
    "objetivo": "Para",
    "momento": "Momento",
    "coadyuvante": "Coadyuvante",
    "volumen_caldo": "Caldo (l/ha)",
}
ESTADO_TEXTO = {EstadoAplicacion.RECOMENDADA.value: "A aplicar", EstadoAplicacion.REALIZADA.value: "Ya aplicado"}
UNIDADES_DOSIS = ["l/ha", "cc/ha", "g/ha", "kg/ha"]

_NUMERICOS = {
    "stand_valor", "porcentaje_cobertura", "cantidad_por_metro_lineal", "porcentaje_dano", "porcentaje_incidencia",
    "dosis", "volumen_caldo",
}
_TITULOS = {
    **COLUMNAS_RECORRIDA, **COLUMNAS_LOTE, **COLUMNAS_CLIENTE, **CAMPOS_APLICACION,
    **{c: t for campos in CAMPOS_ITEMS.values() for c, t in campos.items()},
}


def _vacio(valor) -> bool:
    if valor is None:
        return True
    if isinstance(valor, float):
        return math.isnan(valor)
    if isinstance(valor, str):
        return not valor.strip()
    return str(valor) in ("nan", "NaT", "<NA>")


def valor_limpio(campo: str, valor):
    """Lo que quedó escrito en una celda, listo para guardar: '' -> None, '3,5' -> 3.5,
    'Superado' -> 'superado'. Lanza ValueError con un mensaje para mostrar si no es válido."""
    if campo == "umbral_dano_economico":
        if _vacio(valor):
            return UmbralDanoEconomico.NO_EVALUADO.value
        texto = str(valor).strip()
        for clave, etiqueta in UMBRAL_TEXTO.items():
            if texto.lower() in (clave, etiqueta.lower()):
                return clave
        raise ValueError(f"Umbral no válido: «{valor}». Opciones: {', '.join(UMBRAL_TEXTO.values())}.")
    if campo == "estado":
        if _vacio(valor):
            return EstadoAplicacion.RECOMENDADA.value
        texto = str(valor).strip()
        for clave, etiqueta in ESTADO_TEXTO.items():
            if texto.lower() in (clave, etiqueta.lower()):
                return clave
        raise ValueError(f"Estado no válido: «{valor}». Opciones: {', '.join(ESTADO_TEXTO.values())}.")
    if campo == "unidad" and not _vacio(valor) and str(valor).strip() not in UNIDADES_DOSIS:
        raise ValueError(f"Unidad no válida: «{valor}». Opciones: {', '.join(UNIDADES_DOSIS)}.")
    if _vacio(valor):
        return None
    if campo in _NUMERICOS:
        titulo = _TITULOS.get(campo, campo)
        try:
            numero = float(str(valor).replace(",", "."))
        except ValueError:
            raise ValueError(f"«{valor}» no es un número ({titulo}).") from None
        if math.isnan(numero) or numero < 0:
            raise ValueError(f"«{valor}» no es un número válido ({titulo}).")
        if campo.startswith("porcentaje") and numero > 100:
            raise ValueError(f"{titulo}: {valor} es más que 100%.")
        return numero
    return str(valor).strip()


def items_de(valor) -> list[dict]:
    """La lista de malezas/plagas/enfermedades tal como viene de la base (JSON)."""
    items = json.loads(valor) if isinstance(valor, str) else valor
    return [dict(i) for i in items or []]


def resumen_de(lista: str, valor, sin_presencia: bool) -> str:
    items = [_MODELO_ITEM[lista](**i) for i in items_de(valor)]
    return _resumen_de_lista(items, _CORTO[lista], bool(sin_presencia))


def resumen_aplicaciones(valor) -> str:
    """'glifosato 2 l/ha, atrazina 1 l/ha (ya aplicado)' ('' si no hay ninguna)."""
    return ", ".join(_corto_aplicacion(Aplicacion(**a)) for a in items_de(valor))


def filas_de_aplicaciones(valor) -> list[dict]:
    """Las aplicaciones de una recorrida como renglones de la tabla del panel."""
    filas = []
    for a in items_de(valor):
        fila = {campo: a.get(campo) for campo in CAMPOS_APLICACION}
        fila["estado"] = ESTADO_TEXTO.get(a.get("estado") or "", ESTADO_TEXTO[EstadoAplicacion.RECOMENDADA.value])
        filas.append(fila)
    return filas


def aplicaciones_para_guardar(filas: list[dict]) -> list[dict]:
    """Los renglones de la tabla de productos, validados. Si falta el principio activo, se busca
    el producto en el registro de SENASA."""
    aplicaciones = []
    for fila in filas:
        datos = {campo: valor_limpio(campo, fila.get(campo)) for campo in CAMPOS_APLICACION}
        if datos["producto"] is None:
            if any(v is not None for c, v in datos.items() if c != "estado"):
                raise ValueError("En productos hay un renglón sin nombre de producto.")
            continue
        if datos["principio_activo"] is None:
            datos["principio_activo"] = productos_mod.identificar(datos["producto"]).principio_activo
        aplicaciones.append(Aplicacion(**datos).model_dump(mode="json"))
    return aplicaciones


def filas_de_recorridas(registros: list[dict], nombres: dict[int, str]) -> list[dict]:
    """Una fila por recorrida con lo que muestra la tabla del panel."""
    filas = []
    for r in registros:
        fila = {
            "id": r["id"],
            "fecha": _fecha_hora_argentina_sin_tz(r["fecha_hora"]),
            "tecnico": nombres.get(r["telegram_user_id"]) or r.get("telegram_user_name") or str(r["telegram_user_id"]),
        }
        for campo in COLUMNAS_RECORRIDA:
            fila[campo] = r.get(campo)
        fila["umbral_dano_economico"] = UMBRAL_TEXTO.get(r.get("umbral_dano_economico") or "", "No evaluado")
        for lista in LISTAS:
            fila[lista] = resumen_de(lista, r.get(lista), r.get(f"sin_{lista}"))
        fila["aplicaciones"] = resumen_aplicaciones(r.get("aplicaciones"))
        filas.append(fila)
    return filas


def cambios_en_tabla(
    originales: list[dict], editadas: list[dict], campos, obligatorios: tuple[str, ...] = ()
) -> dict[int, dict]:
    """{id: {campo: valor nuevo}} con lo que cambió en la tabla editada."""
    por_id = {int(f["id"]): f for f in originales}
    cambios: dict[int, dict] = {}
    for fila in editadas:
        original = por_id.get(int(fila["id"]))
        if original is None:
            continue
        distintos = {}
        for campo in campos:
            nuevo = valor_limpio(campo, fila.get(campo))
            if nuevo != valor_limpio(campo, original.get(campo)):
                if nuevo is None and campo in obligatorios:
                    raise ValueError(f"{_TITULOS.get(campo, campo)} no puede quedar vacío.")
                distintos[campo] = nuevo
        if distintos:
            cambios[int(original["id"])] = distintos
    return cambios


def items_para_guardar(lista: str, filas: list[dict]) -> list[dict]:
    """Los renglones que se cargaron en la tabla de malezas/plagas/enfermedades, validados."""
    items = []
    for fila in filas:
        datos = {campo: valor_limpio(campo, fila.get(campo)) for campo in CAMPOS_ITEMS[lista]}
        if datos["nombre"] is None:
            if any(v is not None for v in datos.values()):
                raise ValueError(f"En {LISTAS[lista].lower()} hay un renglón sin nombre.")
            continue
        items.append(_MODELO_ITEM[lista](**datos).model_dump())
    return items


def relevamiento_para_guardar(filas_por_lista: dict[str, list[dict]], sin_presencia: dict[str, bool]) -> dict:
    """Cambios para guardar las tres listas. Si una lista tiene algo, no puede estar
    marcada como "sin presencia"."""
    cambios = {}
    for lista in LISTAS:
        items = items_para_guardar(lista, filas_por_lista.get(lista, []))
        cambios[lista] = items
        cambios[f"sin_{lista}"] = bool(sin_presencia.get(lista)) and not items
    return cambios


# ---- vocabulario ----

def texto_a_sinonimos(texto) -> list[str]:
    if _vacio(texto):
        return []
    return [s.strip() for s in str(texto).split(",") if s.strip()]


def cambios_en_vocabulario(
    originales: list[dict], editadas: list[dict]
) -> tuple[dict[int, tuple[str, list[str]]], list[int]]:
    """Lo que cambió en la tabla de vocabulario: ({id: (nombre, sinónimos)}, [ids a quitar])."""
    por_id = {int(f["id"]): f for f in originales}
    modificar: dict[int, tuple[str, list[str]]] = {}
    quitar: list[int] = []
    for fila in editadas:
        original = por_id.get(int(fila["id"]))
        if original is None:
            continue
        if fila.get("quitar"):
            quitar.append(int(fila["id"]))
            continue
        nombre = "" if _vacio(fila.get("nombre")) else str(fila["nombre"]).strip()
        if not nombre:
            raise ValueError("Una entrada quedó sin nombre. Para borrarla, marcá «Quitar».")
        sinonimos = catalogo_mod.sinonimos_utiles(nombre, texto_a_sinonimos(fila.get("sinonimos")))
        if nombre != original["nombre"] or sinonimos != texto_a_sinonimos(original.get("sinonimos")):
            modificar[int(fila["id"])] = (nombre, sinonimos)
    return modificar, quitar


def clasificar_altas(
    tipo: str, texto: str, existentes: list[EntradaCatalogo]
) -> tuple[list[EntradaCatalogo], list[tuple[EntradaCatalogo, EntradaCatalogo]], list[tuple[EntradaCatalogo, list[EntradaCatalogo]]]]:
    """Lo que se quiere cargar (mismo formato que /agregar), separado en: nuevas, las que ya
    estaban (con la entrada que ya existe) y las dudosas (se parecen a algo ya cargado)."""
    conocidas = list(existentes)
    nuevas, ya_estaban, dudosas = [], [], []
    for entrada in catalogo_mod.parsear_entradas(tipo, texto):
        exacta, parecidas = catalogo_mod.buscar_coincidencias(entrada.tipo, entrada.nombre, conocidas)
        if exacta is not None:
            ya_estaban.append((entrada, exacta))
        elif parecidas:
            dudosas.append((entrada, parecidas[:3]))
        else:
            nuevas.append(entrada)
            conocidas.append(entrada)
    return nuevas, ya_estaban, dudosas


def sinonimos_al_unir(principal: EntradaCatalogo, otras: list[EntradaCatalogo]) -> list[str]:
    """Al unir entradas duplicadas, la principal se queda con los nombres y sinónimos de las otras."""
    todos = list(principal.sinonimos)
    for otra in otras:
        todos += [otra.nombre, *otra.sinonimos]
    return catalogo_mod.sinonimos_utiles(principal.nombre, todos)
