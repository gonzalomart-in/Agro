"""Abre el panel web (panel.py) con una base de datos falsa en memoria: no toca Neon."""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from bot.db import BaseDeDatos

PANEL = str(Path(__file__).resolve().parent.parent / "panel.py")
ADMIN, TECNICO = 111, 222
TOKENS = {"token-admin": (ADMIN, True), "token-tecnico": (TECNICO, False)}


def _recorrida(rid: int, usuario: int, **datos) -> dict:
    base = {
        "id": rid, "telegram_user_id": usuario, "telegram_user_name": None, "lote_id": None, "visita_id": None,
        "fecha_hora": datetime.now(timezone.utc) - timedelta(days=1), "provincia": "Buenos Aires",
        "localidad": "Rancagua", "lote": "Martín", "cultivo": "soja", "ensayo": None, "tratamiento": None,
        "estadio_fenologico": "R3", "hibrido_variedad": "ST46EA25", "stand_valor": None, "stand_unidad": None,
        "estado_cultivo": "bueno", "malezas": "[]", "plagas": "[]", "enfermedades": "[]",
        "sin_malezas": False, "sin_plagas": False, "sin_enfermedades": False,
        "umbral_dano_economico": "no_evaluado", "acciones": None, "comentarios": None,
        "latitud": None, "longitud": None, "transcripcion_original": "Localidad Rancawa...",
    }
    return {**base, **datos}


class BaseFalsa:
    def __init__(self):
        self.llamadas: list[tuple] = []
        self.recorridas = [
            _recorrida(1, ADMIN, enfermedades='[{"nombre": "mancha marrón", "porcentaje_incidencia": null}]'),
            _recorrida(2, TECNICO, localidad="San Pedro", lote="La Loma", hibrido_variedad="DM46i20", stand_valor=3.2),
        ]
        self.lotes = [
            {"id": 5, "telegram_user_id": ADMIN, "nombre": "Martín", "localidad": "Rancagua", "cultivo_habitual": "soja", "ensayo_habitual": None},
            {"id": 6, "telegram_user_id": TECNICO, "nombre": "La Loma", "localidad": "San Pedro", "cultivo_habitual": None, "ensayo_habitual": None},
        ]
        self.catalogo = [{"id": 1, "tipo": "hibrido", "nombre": "ST9939VIP3", "sinonimos": []}]

    async def usuario_de_acceso_panel(self, token_hash):
        for token, (uid, admin) in TOKENS.items():
            if hashlib.sha256(token.encode()).hexdigest() == token_hash:
                return {"telegram_user_id": uid, "es_admin": admin, "vence_en": datetime.now(timezone.utc) + timedelta(hours=12)}
        return None

    async def borrar_acceso_panel(self, token_hash):
        self.llamadas.append(("borrar_acceso_panel",))

    async def nombres_de_usuarios(self):
        return {ADMIN: "Valentina", TECNICO: "Gonzalo"}

    async def listar_recorridas_panel(self, de_usuario, desde=None):
        self.llamadas.append(("listar_recorridas_panel", de_usuario))
        return [r for r in self.recorridas if de_usuario is None or r["telegram_user_id"] == de_usuario]

    async def listar_lotes_panel(self, de_usuario):
        self.llamadas.append(("listar_lotes_panel", de_usuario))
        return [lote for lote in self.lotes if de_usuario is None or lote["telegram_user_id"] == de_usuario]

    async def listar_catalogo(self, tipo=None):
        return [dict(f) for f in self.catalogo if tipo is None or f["tipo"] == tipo]

    async def listar_localidades_usadas(self):
        return ["Rancagua", "San Pedro"]

    async def agregar_catalogo(self, tipo, nombre, sinonimos, agregado_por):
        self.llamadas.append(("agregar_catalogo", tipo, nombre, sinonimos, agregado_por))
        self.catalogo.append({"id": len(self.catalogo) + 1, "tipo": tipo, "nombre": nombre, "sinonimos": sinonimos})
        return True

    async def agregar_sinonimos(self, tipo, nombre, sinonimos):
        self.llamadas.append(("agregar_sinonimos", tipo, nombre, sinonimos))
        return True


@pytest.fixture
def base():
    falsa = BaseFalsa()

    async def conectar_a(database_url, admin_user_ids=None):
        return falsa

    st.cache_resource.clear()
    st.cache_data.clear()
    with patch.object(BaseDeDatos, "conectar_a", new=conectar_a), patch.dict(os.environ, {"DATABASE_URL": "postgresql://falsa"}):
        yield falsa
    st.cache_resource.clear()
    st.cache_data.clear()


def _abrir(token: str | None = None) -> AppTest:
    at = AppTest.from_file(PANEL, default_timeout=30)
    if token:
        at.query_params["acceso"] = token
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _ir_a(at: AppTest, seccion: str) -> AppTest:
    at.sidebar.radio[0].set_value(seccion).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _textos(at: AppTest) -> str:
    return " ".join(str(e.value) for e in [*at.markdown, *at.caption, *at.error, *at.success, *at.warning, *at.info])


def test_sin_link_pide_entrar_con_el_bot(base):
    at = _abrir()
    assert "/panel" in _textos(at)
    assert ("listar_recorridas_panel", None) not in base.llamadas


def test_link_invalido_no_muestra_datos(base):
    at = _abrir("inventado")
    assert "venció" in _textos(at)
    assert not [c for c in base.llamadas if c[0] == "listar_recorridas_panel"]


def test_admin_ve_las_recorridas_de_todos(base):
    at = _abrir("token-admin")
    assert ("listar_recorridas_panel", None) in base.llamadas
    assert at.metric[0].value == "2"
    assert "Valentina" in _textos(at)


def test_tecnico_ve_solo_lo_suyo(base):
    at = _abrir("token-tecnico")
    assert ("listar_recorridas_panel", TECNICO) in base.llamadas
    assert ("listar_recorridas_panel", None) not in base.llamadas
    assert at.metric[0].value == "1"
    _ir_a(at, "🗂️ Lotes")
    assert ("listar_lotes_panel", TECNICO) in base.llamadas


def test_cargar_vocabulario_y_confirmar_un_parecido(base):
    at = _ir_a(_abrir("token-tecnico"), "📚 Vocabulario")
    at.segmented_control(key="tipo").set_value("localidad").run()
    at.text_area[0].input("Rancagua = Rancawa").run()
    at.button[0].click().run()  # el botón del formulario
    assert ("agregar_catalogo", "localidad", "Rancagua", ["Rancawa"], TECNICO) in base.llamadas
    assert "Cargué" in _textos(at)

    at.segmented_control(key="tipo").set_value("hibrido").run()
    at.text_area[0].input("9939").run()
    at.button[0].click().run()
    assert "se parece" in _textos(at)
    at.button(key="confirmar_dudosa").click().run()  # la primera opción: es lo mismo
    assert ("agregar_sinonimos", "hibrido", "ST9939VIP3", ["9939"]) in base.llamadas


def test_si_la_base_no_responde_muestra_un_aviso_en_castellano():
    async def conectar_a(database_url, admin_user_ids=None):
        raise ConnectionRefusedError("sin base")

    st.cache_resource.clear()
    with patch.object(BaseDeDatos, "conectar_a", new=conectar_a), patch.dict(os.environ, {"DATABASE_URL": "postgresql://falsa"}):
        at = _abrir("token-admin")
    st.cache_resource.clear()
    assert "problema con la base de datos" in _textos(at)


def test_cerrar_sesion(base):
    at = _abrir("token-admin")
    next(b for b in at.sidebar.button if b.label == "Cerrar sesión").click().run()
    assert ("borrar_acceso_panel",) in base.llamadas
    assert "/panel" in _textos(at)
