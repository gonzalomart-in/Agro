"""Productos a aplicar (o ya aplicados) con su dosis: el registro de SENASA, el paso 4 de la
extracción y cómo se muestran, se suman, se corrigen y se exportan."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from bot import edicion, productos
from bot.catalogo import EntradaCatalogo, normalizar_transcripcion, texto_para_whisper
from bot.correcciones import aplicar_correccion
from bot.extraccion import (
    _audio_desde_paso_4,
    _cantidades_con_unidad,
    _Paso4,
    extraer_recorrida,
    menciona_aplicaciones,
)
from bot.exportar import aplicaciones_a_dataframe, generar_excel, registros_a_dataframe
from bot.ficha import formatear_borrador, formatear_hibrido, resumen_borrador
from bot.modelos import Aplicacion, CabeceraLote, EstadoAplicacion, Hibrido, RecorridaAudio
from bot.vocabulario_base import VOCABULARIO_BASE
from tests.test_extraccion import OllamaFalso, _config_prueba


# ---------- registro de SENASA ----------

def test_el_registro_de_senasa_esta_en_el_proyecto():
    registrados = productos.productos_registrados()
    assert len(registrados) > 5000
    assert any(p.activos == ("glifosato",) for p in registrados)


@pytest.mark.parametrize("dicho, producto, activo", [
    ("glifosato", "glifosato", "glifosato"),
    ("cletodín", "cletodim", "cletodim"),          # mal escrito: se guarda bien escrito
    ("atracina", "atrazina", "atrazina"),
    ("Roundup", "Roundup", "glifosato"),           # familia de marcas con el mismo activo
    ("Coragen", "CORAGEN", "clorantraniliprole"),  # marca: queda el nombre registrado
    ("2,4-D", "2,4-D", "2,4 D"),
    ("metolaclor", "metolaclor", "S-metolacloro"),
    ("cletodim macro 24", "cletodim macro 24", "cletodim"),  # una palabra es el principio activo
])
def test_identificar_producto(dicho, producto, activo):
    identificado = productos.identificar(dicho)
    assert (identificado.producto, identificado.principio_activo) == (producto, activo)


@pytest.mark.parametrize("dicho", ["herbicida", "insecticida", "algo raro", ""])
def test_no_se_inventa_el_principio_activo(dicho):
    assert productos.identificar(dicho).principio_activo is None


def test_el_vocabulario_del_equipo_tiene_prioridad():
    vocabulario = [EntradaCatalogo("producto", "Roundup Full II", ["randap"])]
    identificado = productos.identificar("randap", vocabulario)
    assert identificado.producto == "Roundup Full II"
    assert identificado.principio_activo == "glifosato"


def test_activos_de_la_columna_de_senasa():
    assert productos.activos_de("GLIFOSATO 48% p/v, 2,4 D 30%") == ("glifosato", "2,4 D")
    assert productos.activos_de("FIPRONIL ,003%") == ("fipronil",)


def test_clave_fonetica():
    assert productos.clave_fonetica("cletodín") == productos.clave_fonetica("cletodim")
    assert productos.clave_fonetica("Rancawa") == productos.clave_fonetica("Rancagua")
    assert productos.clave_fonetica("atracina") == productos.clave_fonetica("atrazina")


@pytest.mark.parametrize("texto, esperado", [
    ("recomiendo cletodín medio litro", True),
    ("aplicar atracina", True),
    ("el 9939 tiene 3 plantas al metro", False),
    ("no hay malezas ni plagas", False),
])
def test_nombra_algun_producto(texto, esperado):
    assert productos.nombra_algun_producto(texto) is esperado


# ---------- cantidades dichas ----------

@pytest.mark.parametrize("frase, esperado", [
    ("glifosato a 2 litros por hectárea", [(2, "l/ha")]),
    ("medio litro de cletodim", [(0.5, "l/ha")]),
    ("un litro y medio", [(1.5, "l/ha")]),
    ("dos litros y medio", [(2.5, "l/ha")]),
    ("500 cc", [(500, "cc/ha")]),
    ("1,5 l/ha", [(1.5, "l/ha")]),
    ("doscientos cincuenta gramos", [(250, "g/ha")]),
    ("aplicar un herbicida", []),  # "un" suelto no es una cantidad
])
def test_cantidades_con_unidad(frase, esperado):
    assert _cantidades_con_unidad(frase) == esperado


def test_decimal_partido_antes_de_litros():
    assert normalizar_transcripcion("glifosato 2, 5 litros", []) == "glifosato 2,5 litros"


# ---------- paso 4 ----------

def _paso4(*aplicaciones) -> _Paso4:
    return _Paso4(aplicaciones=list(aplicaciones))


def test_producto_con_dosis_para_todo_el_lote():
    texto = "Hay que aplicar glifosato a 2 litros por hectárea para la rama negra, con 80 litros de caldo"
    audio = _audio_desde_paso_4(
        _paso4({"producto": "glifosato", "dosis": 2, "unidad": "litros", "objetivo": "rama negra", "volumen_caldo": 80}),
        texto, [], VOCABULARIO_BASE,
    )
    [aplicacion] = audio.aplicaciones
    assert aplicacion.producto == "glifosato" and aplicacion.principio_activo == "glifosato"
    assert (aplicacion.dosis, aplicacion.unidad) == (2, "l/ha")
    assert aplicacion.objetivo == "rama negra"
    assert aplicacion.volumen_caldo == 80
    assert aplicacion.estado == EstadoAplicacion.RECOMENDADA


def test_dosis_que_no_se_dijo_se_descarta():
    audio = _audio_desde_paso_4(_paso4({"producto": "atrazina", "dosis": 3, "unidad": "l/ha"}), "conviene aplicar atrazina", [])
    [aplicacion] = audio.aplicaciones
    assert aplicacion.dosis is None and aplicacion.unidad is None


def test_si_el_modelo_convierte_la_unidad_vale_lo_que_dijo_el_tecnico():
    audio = _audio_desde_paso_4(_paso4({"producto": "2,4 D", "dosis": 0.5, "unidad": "l/ha"}), "2,4 D a 500 cc", [])
    assert (audio.aplicaciones[0].dosis, audio.aplicaciones[0].unidad) == (500, "cc/ha")


def test_producto_que_no_se_nombra_se_descarta():
    audio = _audio_desde_paso_4(_paso4({"producto": "paraquat", "dosis": 2}), "hay que aplicar algo para la rama negra", [])
    assert audio.aplicaciones == []


def test_ya_aplicado_solo_si_lo_dice():
    texto = "Hace diez días se aplicó atrazina, 1 litro. Ahora hay que aplicar cletodim medio litro"
    audio = _audio_desde_paso_4(
        _paso4(
            {"producto": "atrazina", "dosis": 1, "ya_aplicado": True},
            {"producto": "cletodim", "dosis": 0.5, "ya_aplicado": True},  # el modelo se equivoca
        ),
        texto, [],
    )
    estados = {a.producto: a.estado for a in audio.aplicaciones}
    assert estados == {"atrazina": EstadoAplicacion.REALIZADA, "cletodim": EstadoAplicacion.RECOMENDADA}


def test_producto_de_un_hibrido():
    texto = "En el 9939 hay que aplicar clorantraniliprole 50 cc. El 9937 está bien"
    audio = _audio_desde_paso_4(
        _paso4({"producto": "clorantraniliprole", "dosis": 50, "unidad": "cc/ha", "hibrido": "9939"}), texto, ["9939", "9937"]
    )
    assert audio.aplicaciones == []
    [hibrido] = audio.hibridos
    assert hibrido.hibrido_variedad == "9939"
    assert hibrido.aplicaciones[0].dosis == 50


def test_textos_que_no_se_dijeron_se_descartan():
    audio = _audio_desde_paso_4(
        _paso4({"producto": "glifosato", "momento": "en presiembra", "coadyuvante": "aceite metilado", "objetivo": "sorgo de Alepo"}),
        "aplicar glifosato", [],
    )
    aplicacion = audio.aplicaciones[0]
    assert aplicacion.momento is None and aplicacion.coadyuvante is None and aplicacion.objetivo is None


def test_momento_y_coadyuvante_tienen_que_estar_cerca_del_producto():
    texto = "Hace diez días se aplicó atrazina. En el 9939 hay cogollero, conviene aplicar Coragen 50 cc"
    audio = _audio_desde_paso_4(
        _paso4({"producto": "Coragen", "dosis": 50, "momento": "Hace diez días", "objetivo": "cogollero", "hibrido": "9939"}),
        texto, ["9939"],
    )
    [hibrido] = audio.hibridos  # el híbrido está en la misma oración, antes de la coma
    coragen = next(a for a in hibrido.aplicaciones if a.producto == "CORAGEN")
    assert coragen.momento is None  # era de la atrazina
    assert coragen.objetivo == "cogollero"  # dicho en la frase anterior


def test_la_dosis_no_es_un_momento():
    texto = "aplicar glifosato 2 litros, en 80 litros de caldo"
    audio = _audio_desde_paso_4(_paso4({"producto": "glifosato", "momento": "80 litros de caldo"}), texto, [])
    assert audio.aplicaciones[0].momento is None


def test_productos_que_el_modelo_se_salteo_se_agregan_con_su_dosis():
    texto = (
        "Hace diez días se aplicó atrazina, 1 litro por hectárea. Recomiendo aplicar glifosato a 2 litros por "
        "hectárea más 2,4 D a medio litro, con aceite metilado, en 80 litros de caldo."
    )
    audio = _audio_desde_paso_4(
        _paso4({"producto": "glifosato", "dosis": 2, "unidad": "l/ha", "coadyuvante": "aceite metilado"}), texto, []
    )
    resumen = [(a.producto, a.dosis, a.unidad, a.estado.value, a.momento) for a in audio.aplicaciones]
    assert resumen == [
        ("glifosato", 2, "l/ha", "recomendada", None),
        ("atrazina", 1, "l/ha", "realizada", "Hace diez días"),
        ("2,4 D", 0.5, "l/ha", "recomendada", None),
        # el aceite metilado ya está como coadyuvante del glifosato: no se repite
    ]


@pytest.mark.parametrize("texto", [
    "la rama negra es resistente a glifosato",
    "no hace falta aplicar atrazina",
    "el lote tuvo atrazina el año pasado",
])
def test_nombrar_un_producto_sin_aplicarlo_no_agrega_nada(texto):
    assert _audio_desde_paso_4(_paso4(), texto, []).aplicaciones == []


def test_producto_agregado_va_al_hibrido_de_su_oracion():
    texto = "El 9937 está bien. En el 9939 conviene aplicar lambdacialotrina 100 cc"
    audio = _audio_desde_paso_4(_paso4(), texto, ["9937", "9939"])
    assert audio.aplicaciones == []
    [hibrido] = audio.hibridos
    assert hibrido.hibrido_variedad == "9939"
    assert (hibrido.aplicaciones[0].principio_activo, hibrido.aplicaciones[0].dosis) == ("lambda-cialotrina", 100)


def test_productos_nombrados_en_el_texto():
    menciones = productos.productos_nombrados("aplicar cletodín medio litro y 2,4 D, no insecticida")
    assert [(m.texto, m.identificacion.principio_activo) for m in menciones] == [("cletodín", "cletodim"), ("2,4 D", "2,4 D")]


def test_menciona_aplicaciones():
    assert menciona_aplicaciones("hay que aplicar", [])
    assert menciona_aplicaciones("atrazina 2 litros", [])
    assert not menciona_aplicaciones("el 9939 tiene 3 plantas al metro, estado bueno", [])


@pytest.mark.asyncio
async def test_extraccion_con_productos_hace_el_paso_4():
    falso = OllamaFalso(
        paso1={"hibridos": [{"hibrido_variedad": "9939", "stand_valor": 3}]},
        paso3={"acciones": "aplicar glifosato"},
        paso4={"aplicaciones": [{"producto": "glifosato", "dosis": 2, "unidad": "l/ha"}]},
    )
    texto = "El 9939 tiene 3 plantas al metro. Recomiendo aplicar glifosato 2 litros por hectárea"
    with patch("bot.extraccion._llamar_ollama", new=falso):
        audio = await extraer_recorrida(_config_prueba(), texto)
    assert falso.pasos() == [1, 3, 4]
    assert "Híbridos detectados: 9939" in falso.pedidos[-1][1]
    assert audio.aplicaciones[0].dosis == 2
    # lo del lote entero llega al híbrido
    assert audio.hibridos_efectivos()[0].aplicaciones[0].producto == "glifosato"


@pytest.mark.asyncio
async def test_audio_de_stand_no_hace_el_paso_4():
    falso = OllamaFalso(paso1={"hibridos": [{"hibrido_variedad": "9939", "stand_valor": 3}]})
    with patch("bot.extraccion._llamar_ollama", new=falso):
        await extraer_recorrida(_config_prueba(), "el 9939 tiene 3 plantas al metro")
    assert falso.pasos() == [1]


# ---------- sumar audios ----------

def _a(producto, dosis=None, **kw) -> Aplicacion:
    return Aplicacion(producto=producto, dosis=dosis, unidad="l/ha" if dosis else None, **kw)


def test_el_mismo_producto_dicho_de_nuevo_actualiza_la_dosis():
    borrador = RecorridaAudio(aplicaciones=[_a("glifosato", 2), _a("atrazina", 1)])
    borrador.combinar(RecorridaAudio(aplicaciones=[_a("Glifosato", 3)]))
    assert [(a.producto, a.dosis) for a in borrador.aplicaciones] == [("Glifosato", 3), ("atrazina", 1)]


def test_recomendado_y_ya_aplicado_son_distintos():
    borrador = RecorridaAudio(aplicaciones=[_a("glifosato", 2, estado=EstadoAplicacion.REALIZADA)])
    borrador.combinar(RecorridaAudio(aplicaciones=[_a("glifosato", 3)]))
    assert len(borrador.aplicaciones) == 2


def test_los_productos_del_lote_se_suman_a_los_del_hibrido():
    borrador = RecorridaAudio(
        aplicaciones=[_a("glifosato", 2), _a("atrazina", 1)],
        hibridos=[Hibrido(hibrido_variedad="9939", aplicaciones=[_a("atrazina", 1.5), _a("clorantraniliprole", 0.05)])],
    )
    [efectivo] = borrador.hibridos_efectivos()
    assert [(a.producto, a.dosis) for a in efectivo.aplicaciones] == [
        ("glifosato", 2), ("atrazina", 1.5), ("clorantraniliprole", 0.05),
    ]


def test_audio_que_solo_recomienda_un_producto_se_guarda_para_todo_el_lote():
    borrador = RecorridaAudio(aplicaciones=[_a("glifosato", 2)])
    assert borrador.tiene_datos() and borrador.solo_datos_del_lote()
    [fila] = borrador.fichas(CabeceraLote(lote="Martín", cultivo="soja"))
    assert fila.hibrido_variedad is None
    assert fila.lote == "Martín"
    assert fila.aplicaciones[0].producto == "glifosato"


def test_sin_datos_del_lote_no_se_guarda_una_fila_vacia():
    assert RecorridaAudio(lote="Martín").fichas(CabeceraLote(lote="Martín")) == []


# ---------- cómo se ve en Telegram ----------

def test_ficha_muestra_los_productos():
    hibrido = Hibrido(hibrido_variedad="9939", aplicaciones=[
        Aplicacion(producto="Roundup", principio_activo="glifosato", dosis=2, unidad="l/ha", objetivo="rama negra",
                   momento="en presiembra", coadyuvante="aceite metilado", volumen_caldo=80),
        Aplicacion(producto="atrazina", principio_activo="atrazina", estado=EstadoAplicacion.REALIZADA),
    ])
    texto = formatear_hibrido(hibrido, 1)
    assert "A aplicar: Roundup (glifosato) 2 l/ha · para rama negra · en presiembra · + aceite metilado · caldo 80 l/ha" in texto
    assert "Ya aplicado: atrazina dosis ❓" in texto


def test_resumen_y_borrador_de_un_audio_solo_con_productos():
    borrador = RecorridaAudio(lote="Martín", aplicaciones=[_a("glifosato", 2)])
    assert "🧴 glifosato 2 l/ha" in resumen_borrador(borrador, None, [], [])
    assert "A aplicar: glifosato 2 l/ha" in formatear_borrador(borrador)


def test_corregir_limpiar_productos():
    borrador = RecorridaAudio(
        aplicaciones=[_a("glifosato", 2)], hibridos=[Hibrido(hibrido_variedad="9939", aplicaciones=[_a("atrazina", 1)])]
    )
    mensaje = aplicar_correccion(borrador, "1 limpiar productos")
    assert borrador.hibridos[0].aplicaciones == [] and borrador.aplicaciones
    assert "todo el lote" in mensaje
    aplicar_correccion(borrador, "todos limpiar productos")
    assert borrador.aplicaciones == []


# ---------- vocabulario ----------

def test_la_pista_de_whisper_incluye_productos_sin_pasarse_del_presupuesto():
    pista = texto_para_whisper(VOCABULARIO_BASE, "soja")
    assert "Productos: glifosato, 2,4-D, atrazina" in pista
    assert len(pista) <= 650


# ---------- Excel y panel ----------

_APLICACIONES_JSON = json.dumps([
    {"producto": "Roundup", "principio_activo": "glifosato", "dosis": 2, "unidad": "l/ha", "objetivo": "rama negra",
     "momento": None, "coadyuvante": None, "volumen_caldo": None, "estado": "recomendada"},
    {"producto": "atrazina", "principio_activo": "atrazina", "dosis": None, "unidad": None, "objetivo": None,
     "momento": "hace 10 días", "coadyuvante": None, "volumen_caldo": None, "estado": "realizada"},
])
_REGISTRO = {
    "id": 3, "fecha_hora": datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc), "localidad": "Rancagua", "lote": "Martín",
    "cultivo": "soja", "hibrido_variedad": "DM46i20", "malezas": "[]", "plagas": "[]", "enfermedades": "[]",
    "aplicaciones": _APLICACIONES_JSON,
}


def test_excel_columna_y_hoja_de_aplicaciones():
    df = registros_a_dataframe([_REGISTRO])
    assert df.iloc[0]["aplicaciones"] == (
        "A aplicar: Roundup (glifosato) 2 l/ha, para rama negra; Ya aplicado: atrazina, hace 10 días"
    )
    hoja = aplicaciones_a_dataframe([_REGISTRO])
    assert list(hoja["Producto"]) == ["Roundup", "atrazina"]
    assert list(hoja["Estado"]) == ["A aplicar", "Ya aplicado"]
    assert hoja.iloc[0]["Lote"] == "Martín" and hoja.iloc[0]["Dosis"] == 2
    assert len(generar_excel([_REGISTRO]).getvalue()) > 0


def test_registros_viejos_sin_aplicaciones_no_fallan():
    viejo = {k: v for k, v in _REGISTRO.items() if k != "aplicaciones"}
    assert registros_a_dataframe([viejo]).iloc[0]["aplicaciones"] == ""
    assert aplicaciones_a_dataframe([viejo]).empty
    assert edicion.resumen_aplicaciones(None) == ""


def test_panel_resumen_y_filas_de_aplicaciones():
    assert edicion.resumen_aplicaciones(_APLICACIONES_JSON) == "Roundup 2 l/ha, atrazina (ya aplicado)"
    filas = edicion.filas_de_aplicaciones(_APLICACIONES_JSON)
    assert [f["estado"] for f in filas] == ["A aplicar", "Ya aplicado"]


def test_panel_guardar_aplicaciones_completa_el_principio_activo():
    filas = [
        {"estado": "A aplicar", "producto": "Coragen", "principio_activo": None, "dosis": "50", "unidad": "cc/ha"},
        {"estado": None, "producto": None, "principio_activo": None, "dosis": None},  # renglón vacío
    ]
    [guardada] = edicion.aplicaciones_para_guardar(filas)
    assert guardada["principio_activo"] == "clorantraniliprole"
    assert guardada["dosis"] == 50 and guardada["estado"] == "recomendada"


@pytest.mark.parametrize("fila, error", [
    ({"estado": "A aplicar", "producto": None, "dosis": 2}, "sin nombre"),
    ({"estado": "Quizás", "producto": "glifosato"}, "Estado no válido"),
    ({"estado": "A aplicar", "producto": "glifosato", "unidad": "baldes"}, "Unidad no válida"),
    ({"estado": "A aplicar", "producto": "glifosato", "dosis": "mucho"}, "no es un número"),
])
def test_panel_valida_las_aplicaciones(fila, error):
    with pytest.raises(ValueError, match=error):
        edicion.aplicaciones_para_guardar([fila])
