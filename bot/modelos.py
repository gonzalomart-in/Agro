"""Modelos de datos (Pydantic) para las recorridas a campo.

Un audio puede traer los datos del lote (`CabeceraLote`), uno o varios
híbridos (`Hibrido`) y datos generales que valen para todos los híbridos
(`Relevamiento` a nivel del audio). Cada híbrido se guarda como una fila
(`RecorridaCampo`) que junta los datos del lote y los del híbrido.
"""
from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, Field


class UnidadStand(str, Enum):
    PL_M_LINEAL = "pl/m lineal"
    PL_M2 = "pl/m2"
    PL_HA = "pl/ha"


class UmbralDanoEconomico(str, Enum):
    SUPERADO = "superado"
    CERCANO = "cercano"
    NO_SUPERADO = "no_superado"
    NO_EVALUADO = "no_evaluado"


class Maleza(BaseModel):
    nombre: str
    tamano: str | None = None
    porcentaje_cobertura: float | None = None
    observacion: str | None = None


class Plaga(BaseModel):
    nombre: str
    cantidad_por_metro_lineal: float | None = None
    porcentaje_dano: float | None = None
    observacion: str | None = None


class Enfermedad(BaseModel):
    nombre: str
    porcentaje_incidencia: float | None = None
    severidad: str | None = None
    observacion: str | None = None


class EstadoAplicacion(str, Enum):
    RECOMENDADA = "recomendada"
    REALIZADA = "realizada"


class Aplicacion(BaseModel):
    """Un producto fitosanitario que el técnico recomienda aplicar (o dice que ya se aplicó)."""

    producto: str
    principio_activo: str | None = None
    dosis: float | None = None
    unidad: str | None = None
    objetivo: str | None = None
    momento: str | None = None
    coadyuvante: str | None = None
    volumen_caldo: float | None = None
    estado: EstadoAplicacion = EstadoAplicacion.RECOMENDADA


class Lote(BaseModel):
    """Entrada del catálogo de lotes de un usuario."""

    id: int | None = None
    nombre: str
    localidad: str | None = None
    cultivo_habitual: str | None = None
    ensayo_habitual: str | None = None
    cliente_id: int | None = None
    cliente: str | None = None


class Cliente(BaseModel):
    """Entrada del catálogo de clientes de un usuario (dueño de los lotes)."""

    id: int | None = None
    nombre: str


def normalizar_texto(texto: str | None) -> str:
    """Minúsculas, sin tildes ni espacios/símbolos: 'Las  Lilas' == 'las lilas' == 'laslilas'."""
    if not texto:
        return ""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", sin_tildes.lower())


_CULTIVOS_CON_HIBRIDOS = {"maiz", "girasol", "sorgo"}
_CULTIVOS_CON_VARIEDADES = {
    "soja", "trigo", "cebada", "avena", "centeno", "mani", "poroto", "arroz", "algodon", "lino", "alfalfa",
}


def etiqueta_material(cultivo: str | None) -> tuple[str, str]:
    """Cómo se le dice al material genético según el cultivo: (singular, plural).

    Maíz, girasol y sorgo se siembran con híbridos; soja, trigo, cebada y otros con
    variedades. Si el cultivo no se conoce, se usa la palabra doble.
    """
    clave = normalizar_texto(cultivo)
    if clave in _CULTIVOS_CON_HIBRIDOS:
        return "híbrido", "híbridos"
    if clave in _CULTIVOS_CON_VARIEDADES:
        return "variedad", "variedades"
    return "híbrido/variedad", "híbridos/variedades"


def _clave_lote(nombre: str | None) -> str:
    """'Lote Las Lilas', 'las lilas' y 'LAS LILAS' comparan igual."""
    clave = normalizar_texto(nombre)
    if clave.startswith("lote") and len(clave) > len("lote"):
        return clave[len("lote"):]
    return clave


class CabeceraLote(BaseModel):
    """Datos del lote que se está recorriendo (comunes a todos sus híbridos)."""

    provincia: str | None = None
    localidad: str | None = None
    lote: str | None = None
    lote_id: int | None = Field(
        default=None,
        description="ID del lote existente si coincide con el catálogo del usuario",
    )
    cliente: str | None = Field(
        default=None,
        description="Nombre del cliente/productor dueño del lote, si el técnico lo dice",
    )
    cliente_id: int | None = Field(
        default=None,
        description="ID del cliente existente si coincide con el catálogo del usuario",
    )
    cultivo: str | None = None
    ensayo: str | None = None
    estadio_fenologico: str | None = None
    latitud: float | None = None
    longitud: float | None = None

    CAMPOS_CLAVE_CABECERA: ClassVar[tuple[str, ...]] = ("localidad", "lote", "cultivo", "ensayo")

    def campos_cabecera_faltantes(self) -> list[str]:
        return [c for c in self.CAMPOS_CLAVE_CABECERA if getattr(self, c) in (None, "")]

    def como_cabecera(self) -> "CabeceraLote":
        """Copia solo los datos del lote (descarta lo que sea de un híbrido)."""
        return CabeceraLote(**{c: getattr(self, c) for c in CabeceraLote.model_fields})

    def completar_con(self, abierta: "CabeceraLote | None") -> "CabeceraLote":
        """Datos del lote a usar al guardar: los del lote ya abierto mandan, y los
        de este audio solo rellenan lo que el lote abierto no tiene."""
        if abierta is None:
            return self.como_cabecera()
        efectiva = abierta.como_cabecera()
        for campo in CabeceraLote.model_fields:
            if getattr(efectiva, campo) is None:
                setattr(efectiva, campo, getattr(self, campo))
        return efectiva

    def es_otro_lote_que(self, otro: "CabeceraLote | None") -> bool:
        """True si este lote tiene nombre y es distinto del de `otro`."""
        if otro is None or not self.lote or not otro.lote:
            return False
        return _clave_lote(self.lote) != _clave_lote(otro.lote)


_LISTAS_Y_FLAGS = (
    ("malezas", "sin_malezas"),
    ("plagas", "sin_plagas"),
    ("enfermedades", "sin_enfermedades"),
)


def _fusionar_items(actuales: list, nuevos: list) -> None:
    """Agrega `nuevos` a `actuales`; si ya había uno con el mismo nombre, lo reemplaza."""
    for item in nuevos:
        clave = normalizar_texto(item.nombre)
        for i, actual in enumerate(actuales):
            if normalizar_texto(actual.nombre) == clave:
                actuales[i] = item
                break
        else:
            actuales.append(item)


def _clave_aplicacion(aplicacion: Aplicacion) -> tuple[str, str]:
    return normalizar_texto(aplicacion.producto), aplicacion.estado.value


def fusionar_aplicaciones(actuales: list[Aplicacion], nuevas: list[Aplicacion]) -> None:
    """Agrega `nuevas` a `actuales`; el mismo producto (recomendado o ya aplicado) dicho de
    nuevo reemplaza al anterior (por ejemplo, para corregir la dosis)."""
    for nueva in nuevas:
        clave = _clave_aplicacion(nueva)
        for i, actual in enumerate(actuales):
            if _clave_aplicacion(actual) == clave:
                actuales[i] = nueva
                break
        else:
            actuales.append(nueva)


class Relevamiento(BaseModel):
    """Malezas, plagas y enfermedades, con la constancia explícita de cuando NO hay."""

    malezas: list[Maleza] = Field(default_factory=list)
    plagas: list[Plaga] = Field(default_factory=list)
    enfermedades: list[Enfermedad] = Field(default_factory=list)

    sin_malezas: bool = Field(
        default=False,
        description="true solo si el técnico dijo explícitamente que NO hay malezas",
    )
    sin_plagas: bool = Field(
        default=False,
        description="true solo si el técnico dijo explícitamente que NO hay plagas",
    )
    sin_enfermedades: bool = Field(
        default=False,
        description="true solo si el técnico dijo explícitamente que NO hay síntomas de enfermedades",
    )

    def relevamientos_sin_informar(self) -> list[str]:
        """Malezas/plagas/enfermedades que ni tienen items ni fueron confirmadas como ausentes."""
        return [
            lista for lista, sin in _LISTAS_Y_FLAGS if not getattr(self, lista) and not getattr(self, sin)
        ]

    def fusionar_relevamientos(self, otro: "Relevamiento") -> None:
        """Suma lo relevado en `otro`: sus items reemplazan/agregan; un "no hay" explícito
        de `otro` vacía la lista; y si hay items, ya no vale el "no hay"."""
        for lista, sin in _LISTAS_Y_FLAGS:
            nuevos = getattr(otro, lista)
            if nuevos:
                _fusionar_items(getattr(self, lista), nuevos)
                setattr(self, sin, False)
            elif getattr(otro, sin):
                setattr(self, lista, [])
                setattr(self, sin, True)


class Hibrido(Relevamiento):
    """Datos relevados de UN híbrido/variedad dentro de un lote."""

    hibrido_variedad: str | None = Field(
        default=None, description="Nombre o código del híbrido o variedad, ej. 9939"
    )
    tratamiento: str | None = Field(
        default=None,
        description="Tratamiento o cultivar específico dentro del ensayo, ya que un mismo ensayo puede tener varios",
    )
    stand_valor: float | None = None
    stand_unidad: UnidadStand | None = None

    estado_cultivo: str | None = None

    umbral_dano_economico: UmbralDanoEconomico = UmbralDanoEconomico.NO_EVALUADO

    acciones: str | None = None
    comentarios: str | None = None
    aplicaciones: list[Aplicacion] = Field(default_factory=list)

    CAMPOS_CLAVE_HIBRIDO: ClassVar[tuple[str, ...]] = (
        "hibrido_variedad",
        "stand_valor",
        "estado_cultivo",
    )
    _CAMPOS_SIMPLES: ClassVar[tuple[str, ...]] = (
        "tratamiento",
        "stand_valor",
        "stand_unidad",
        "estado_cultivo",
        "acciones",
        "comentarios",
    )

    def campos_hibrido_faltantes(self) -> list[str]:
        return [c for c in self.CAMPOS_CLAVE_HIBRIDO if getattr(self, c) in (None, "")]

    def actualizar_con(self, otro: "Hibrido") -> None:
        """Suma los datos de `otro` (el mismo híbrido dicho en otro audio): lo nuevo pisa lo viejo."""
        for campo in self._CAMPOS_SIMPLES:
            valor = getattr(otro, campo)
            if valor is not None:
                setattr(self, campo, valor)
        if otro.umbral_dano_economico != UmbralDanoEconomico.NO_EVALUADO:
            self.umbral_dano_economico = otro.umbral_dano_economico
        self.fusionar_relevamientos(otro)
        fusionar_aplicaciones(self.aplicaciones, otro.aplicaciones)

    def con_generales(self, generales: "Relevamiento") -> "Hibrido":
        """Copia del híbrido que hereda de `generales` lo que él no informó por su cuenta.

        Las aplicaciones se suman: lo que se recomienda para todo el lote vale también para
        cada híbrido, salvo que para ese híbrido se haya dicho el mismo producto."""
        copia = self.model_copy(deep=True)
        for lista, sin in _LISTAS_Y_FLAGS:
            if not getattr(copia, lista) and not getattr(copia, sin):
                setattr(copia, lista, [i.model_copy() for i in getattr(generales, lista)])
                setattr(copia, sin, getattr(generales, sin))
        for campo in ("acciones", "comentarios"):
            if getattr(copia, campo) is None:
                setattr(copia, campo, getattr(generales, campo, None))
        propias = {_clave_aplicacion(a) for a in copia.aplicaciones}
        del_lote = [a.model_copy() for a in getattr(generales, "aplicaciones", []) if _clave_aplicacion(a) not in propias]
        copia.aplicaciones = del_lote + copia.aplicaciones
        umbral_general = getattr(generales, "umbral_dano_economico", UmbralDanoEconomico.NO_EVALUADO)
        if copia.umbral_dano_economico == UmbralDanoEconomico.NO_EVALUADO:
            copia.umbral_dano_economico = umbral_general
        return copia


class RecorridaAudio(CabeceraLote, Relevamiento):
    """Lo que se extrae de un audio: datos del lote (si los dijo), sus híbridos y lo que
    vale para todos los híbridos (malezas/plagas/enfermedades a nivel del lote)."""

    umbral_dano_economico: UmbralDanoEconomico = UmbralDanoEconomico.NO_EVALUADO
    acciones: str | None = None
    comentarios: str | None = None
    aplicaciones: list[Aplicacion] = Field(
        default_factory=list,
        description="Productos a aplicar (o ya aplicados) en todo el lote",
    )

    hibridos: list[Hibrido] = Field(
        default_factory=list,
        description="Un item por cada híbrido/variedad del que habla el audio",
    )
    transcripcion_original: str | None = None

    def tiene_datos_generales(self) -> bool:
        """True si hay algo dicho para todo el lote (malezas, productos, comentarios...)."""
        return bool(
            any(getattr(self, lista) or getattr(self, sin) for lista, sin in _LISTAS_Y_FLAGS)
            or self.acciones
            or self.comentarios
            or self.aplicaciones
            or self.umbral_dano_economico != UmbralDanoEconomico.NO_EVALUADO
        )

    def tiene_datos(self) -> bool:
        """False si el audio no aportó nada (ni lote, ni híbridos, ni datos generales)."""
        return bool(
            self.hibridos
            or any(getattr(self, c) is not None for c in CabeceraLote.model_fields)
            or self.tiene_datos_generales()
        )

    def hibridos_efectivos(self) -> list[Hibrido]:
        """Los híbridos con los datos generales del lote ya aplicados."""
        return [h.con_generales(self) for h in self.hibridos]

    def solo_datos_del_lote(self) -> bool:
        """True si no se nombró ningún híbrido pero hay datos para todo el lote (por ejemplo, un
        audio que solo recomienda un producto después de haber guardado los híbridos)."""
        return not self.hibridos and self.tiene_datos_generales()

    def combinar(self, nuevo: "RecorridaAudio") -> tuple[list[str], list[str]]:
        """Suma lo extraído de otro audio a este borrador.

        Los datos del lote solo rellenan huecos; un híbrido ya presente (mismo
        nombre) se actualiza y los demás se agregan. Devuelve los nombres de los
        híbridos (agregados, actualizados).
        """
        for campo in CabeceraLote.model_fields:
            if getattr(self, campo) is None:
                setattr(self, campo, getattr(nuevo, campo))
        self.fusionar_relevamientos(nuevo)
        for campo in ("acciones", "comentarios"):
            if getattr(nuevo, campo) is not None:
                setattr(self, campo, getattr(nuevo, campo))
        fusionar_aplicaciones(self.aplicaciones, nuevo.aplicaciones)
        if nuevo.umbral_dano_economico != UmbralDanoEconomico.NO_EVALUADO:
            self.umbral_dano_economico = nuevo.umbral_dano_economico
        if nuevo.transcripcion_original:
            self.transcripcion_original = (
                f"{self.transcripcion_original}\n{nuevo.transcripcion_original}"
                if self.transcripcion_original
                else nuevo.transcripcion_original
            )

        agregados: list[str] = []
        actualizados: list[str] = []
        for hibrido in nuevo.hibridos:
            clave = normalizar_texto(hibrido.hibrido_variedad)
            existente = next(
                (h for h in self.hibridos if clave and normalizar_texto(h.hibrido_variedad) == clave),
                None,
            )
            nombre = hibrido.hibrido_variedad or "sin nombre"
            if existente is None:
                self.hibridos.append(hibrido)
                agregados.append(nombre)
            else:
                existente.actualizar_con(hibrido)
                actualizados.append(nombre)
        return agregados, actualizados

    def fichas(self, cabecera: CabeceraLote) -> list["RecorridaCampo"]:
        """Una fila lista para guardar por cada híbrido, con los datos del lote `cabecera`.
        Si solo hay datos para todo el lote, una única fila sin híbrido, para no perderlos."""
        hibridos = [Hibrido().con_generales(self)] if self.solo_datos_del_lote() else self.hibridos_efectivos()
        return [
            RecorridaCampo(
                **cabecera.model_dump(),
                **h.model_dump(),
                transcripcion_original=self.transcripcion_original,
            )
            for h in hibridos
        ]


class RecorridaCampo(CabeceraLote, Hibrido):
    """Una fila de la tabla `recorridas`: datos del lote + datos de un híbrido."""

    transcripcion_original: str | None = None
