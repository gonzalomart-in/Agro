"""Tests de la lógica del panel web (bot/edicion.py) y de los links de acceso (acceso.py)."""
import hashlib
from datetime import datetime, timezone

import pandas as pd
import pytest

from bot import acceso, edicion
from bot.catalogo import EntradaCatalogo

REGISTRO = {
    "id": 7,
    "telegram_user_id": 111,
    "telegram_user_name": "Vale",
    "fecha_hora": datetime(2026, 9, 26, 15, 30, tzinfo=timezone.utc),
    "provincia": "Buenos Aires",
    "localidad": "Rancagua",
    "lote": "Martín",
    "cultivo": "soja",
    "hibrido_variedad": "ST46EA25",
    "stand_valor": 3.5,
    "estado_cultivo": "bueno",
    "umbral_dano_economico": "no_evaluado",
    "malezas": "[]",
    "plagas": [],
    "enfermedades": '[{"nombre": "mancha marrón", "porcentaje_incidencia": 15, "severidad": null, "observacion": null}]',
    "sin_malezas": False,
    "sin_plagas": True,
    "sin_enfermedades": False,
}


# ---------- tabla de recorridas ----------

def test_filas_de_recorridas_resume_listas_y_usa_nombre_y_hora_argentina():
    fila = edicion.filas_de_recorridas([REGISTRO], {111: "Valentina"})[0]
    assert fila["tecnico"] == "Valentina"
    assert fila["fecha"] == datetime(2026, 9, 26, 12, 30)
    assert fila["umbral_dano_economico"] == "No evaluado"
    assert fila["enfermedades"] == "mancha marrón 15%"
    assert fila["plagas"] == "✅ no hay"
    assert fila["malezas"] == "❓"


def test_cambios_en_tabla_detecta_solo_lo_modificado():
    original = edicion.filas_de_recorridas([REGISTRO], {})
    editada = [dict(original[0], localidad="Rancagua ", stand_valor="3,2", umbral_dano_economico="Superado")]
    assert edicion.cambios_en_tabla(original, editada, edicion.COLUMNAS_RECORRIDA) == {
        7: {"stand_valor": 3.2, "umbral_dano_economico": "superado"}
    }


def test_cambios_en_tabla_vaciar_una_celda_la_borra():
    original = edicion.filas_de_recorridas([REGISTRO], {})
    editada = [dict(original[0], estado_cultivo="", stand_valor=float("nan"))]
    assert edicion.cambios_en_tabla(original, editada, edicion.COLUMNAS_RECORRIDA) == {
        7: {"stand_valor": None, "estado_cultivo": None}
    }


def test_cambios_en_tabla_sin_cambios_con_los_tipos_que_devuelve_pandas():
    original = pd.DataFrame(edicion.filas_de_recorridas([REGISTRO, dict(REGISTRO, id=8, stand_valor=None)], {}))
    assert edicion.cambios_en_tabla(original.to_dict("records"), original.copy().to_dict("records"), edicion.COLUMNAS_RECORRIDA) == {}


@pytest.mark.parametrize("valor", ["tres", "-1"])
def test_stand_invalido_da_un_error_claro(valor):
    original = edicion.filas_de_recorridas([REGISTRO], {})
    with pytest.raises(ValueError, match="Stand"):
        edicion.cambios_en_tabla(original, [dict(original[0], stand_valor=valor)], edicion.COLUMNAS_RECORRIDA)


def test_campo_obligatorio_no_puede_quedar_vacio():
    original = [{"id": 1, "nombre": "Martín", "localidad": "Rancagua"}]
    with pytest.raises(ValueError, match="Nombre"):
        edicion.cambios_en_tabla(original, [dict(original[0], nombre=" ")], edicion.COLUMNAS_LOTE, obligatorios=("nombre",))


# ---------- malezas, plagas y enfermedades ----------

def test_relevamiento_valida_numeros_y_saca_renglones_vacios():
    cambios = edicion.relevamiento_para_guardar(
        {
            "malezas": [{"nombre": "rama negra", "porcentaje_cobertura": "5"}, {"nombre": None}],
            "enfermedades": [{"nombre": "mancha marrón", "porcentaje_incidencia": float("nan"), "severidad": pd.NA}],
        },
        {"plagas": True, "enfermedades": True},
    )
    assert cambios["malezas"] == [{"nombre": "rama negra", "tamano": None, "porcentaje_cobertura": 5.0, "observacion": None}]
    assert cambios["enfermedades"][0]["porcentaje_incidencia"] is None
    # con algo cargado no puede quedar "sin presencia"
    assert (cambios["sin_malezas"], cambios["sin_plagas"], cambios["sin_enfermedades"]) == (False, True, False)


def test_relevamiento_rechaza_porcentaje_mayor_a_100_y_renglon_sin_nombre():
    with pytest.raises(ValueError, match="100"):
        edicion.relevamiento_para_guardar({"enfermedades": [{"nombre": "roya", "porcentaje_incidencia": 150}]}, {})
    with pytest.raises(ValueError, match="sin nombre"):
        edicion.relevamiento_para_guardar({"plagas": [{"nombre": "", "porcentaje_dano": 10}]}, {})


# ---------- vocabulario ----------

def test_cambios_en_vocabulario():
    originales = [
        {"id": 1, "nombre": "ST9939", "sinonimos": "9939"},
        {"id": 2, "nombre": "Rancagua", "sinonimos": ""},
        {"id": 3, "nombre": "viejo", "sinonimos": ""},
    ]
    editadas = [
        dict(originales[0]),
        dict(originales[1], sinonimos="Rancawa, rancagua, Rancawa"),
        dict(originales[2], quitar=True),
    ]
    modificar, quitar = edicion.cambios_en_vocabulario(originales, editadas)
    assert modificar == {2: ("Rancagua", ["Rancawa"])}
    assert quitar == [3]


def test_vocabulario_sin_nombre_pide_marcar_quitar():
    with pytest.raises(ValueError, match="Quitar"):
        edicion.cambios_en_vocabulario([{"id": 1, "nombre": "x", "sinonimos": ""}], [{"id": 1, "nombre": None}])


def test_clasificar_altas_separa_nuevas_repetidas_y_dudosas():
    existentes = [EntradaCatalogo("hibrido", "ST9939VIP3"), EntradaCatalogo("hibrido", "DM46i20")]
    nuevas, ya_estaban, dudosas = edicion.clasificar_altas("hibrido", "dm 46i20 = 46i20\n9939\nNS7921\nns 7921", existentes)
    assert [e.nombre for e in nuevas] == ["NS7921"]
    assert [(e.nombre, x.nombre) for e, x in ya_estaban] == [("dm 46i20", "DM46i20"), ("ns 7921", "NS7921")]
    assert [(e.nombre, [p.nombre for p in ps]) for e, ps in dudosas] == [("9939", ["ST9939VIP3"])]


def test_sinonimos_al_unir():
    principal = EntradaCatalogo("hibrido", "ST9939VIP3", ["9939"])
    otras = [EntradaCatalogo("hibrido", "ST9939", ["9939", "st 9939"]), EntradaCatalogo("hibrido", "ST9939 VIP3")]
    assert edicion.sinonimos_al_unir(principal, otras) == ["9939", "ST9939"]


# ---------- links de acceso al panel ----------

class BaseDeAccesosFalsa:
    def __init__(self):
        self.accesos: dict[str, tuple[int, datetime]] = {}
        self.permitidos = {111: True, 222: False}

    async def crear_acceso_panel(self, telegram_user_id, token_hash, vence_en):
        self.accesos[token_hash] = (telegram_user_id, vence_en)

    async def usuario_de_acceso_panel(self, token_hash):
        if token_hash not in self.accesos:
            return None
        uid, vence_en = self.accesos[token_hash]
        if uid not in self.permitidos or vence_en <= datetime.now(timezone.utc):
            return None
        return {"telegram_user_id": uid, "es_admin": self.permitidos[uid], "vence_en": vence_en}

    async def borrar_acceso_panel(self, token_hash):
        self.accesos.pop(token_hash, None)


@pytest.mark.asyncio
async def test_link_del_panel_identifica_al_usuario_y_se_puede_cerrar():
    db = BaseDeAccesosFalsa()
    link = await acceso.crear_link_panel(db, 222, "https://panel.ejemplo.com/")
    assert link.startswith("https://panel.ejemplo.com/?acceso=")
    token = link.split("=", 1)[1]
    assert len(token) >= 40
    # en la base queda solo la huella, no el token
    assert list(db.accesos) == [hashlib.sha256(token.encode()).hexdigest()]

    usuario = await acceso.usuario_del_panel(db, token)
    assert (usuario.telegram_user_id, usuario.es_admin) == (222, False)

    await acceso.cerrar_acceso_panel(db, token)
    assert await acceso.usuario_del_panel(db, token) is None


@pytest.mark.asyncio
async def test_link_de_usuario_revocado_o_inventado_no_sirve():
    db = BaseDeAccesosFalsa()
    link = await acceso.crear_link_panel(db, 222, "https://panel.ejemplo.com")
    del db.permitidos[222]
    assert await acceso.usuario_del_panel(db, link.split("=", 1)[1]) is None
    assert await acceso.usuario_del_panel(db, "inventado") is None
    assert await acceso.usuario_del_panel(db, None) is None


@pytest.mark.asyncio
async def test_dos_links_seguidos_son_distintos():
    db = BaseDeAccesosFalsa()
    assert await acceso.crear_link_panel(db, 111, "https://x") != await acceso.crear_link_panel(db, 111, "https://x")
