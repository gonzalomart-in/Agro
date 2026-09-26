"""Modelos de datos (Pydantic) para la ficha de recorrida a campo."""
from __future__ import annotations

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


class Lote(BaseModel):
    """Entrada del catálogo de lotes de un usuario."""

    id: int | None = None
    nombre: str
    localidad: str | None = None
    cultivo_habitual: str | None = None
    ensayo_habitual: str | None = None


class RecorridaCampo(BaseModel):
    """Ficha completa de una recorrida a campo, extraída de una transcripción."""

    localidad: str | None = None
    lote: str | None = None
    lote_id: int | None = Field(
        default=None,
        description="ID del lote existente si coincide con el catálogo del usuario",
    )
    cultivo: str | None = None
    hibrido_variedad: str | None = None
    ensayo: str | None = None
    tratamiento: str | None = Field(
        default=None,
        description="Tratamiento o cultivar específico dentro del ensayo, ya que un mismo ensayo puede tener varios",
    )
    estadio_fenologico: str | None = None

    stand_valor: float | None = None
    stand_unidad: UnidadStand | None = None

    estado_cultivo: str | None = None

    malezas: list[Maleza] = Field(default_factory=list)
    plagas: list[Plaga] = Field(default_factory=list)
    enfermedades: list[Enfermedad] = Field(default_factory=list)

    umbral_dano_economico: UmbralDanoEconomico = UmbralDanoEconomico.NO_EVALUADO

    acciones: str | None = None
    comentarios: str | None = None

    latitud: float | None = None
    longitud: float | None = None

    transcripcion_original: str | None = None

    CAMPOS_CLAVE: ClassVar[tuple[str, ...]] = (
        "localidad",
        "lote",
        "cultivo",
        "ensayo",
        "stand_valor",
        "estado_cultivo",
    )

    def campos_clave_faltantes(self) -> list[str]:
        faltantes = []
        for campo in self.CAMPOS_CLAVE:
            if getattr(self, campo) in (None, ""):
                faltantes.append(campo)
        return faltantes
