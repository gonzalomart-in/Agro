"""Sumar varios audios en un solo borrador, datos generales y correcciones manuales."""
import pytest

from bot.correcciones import ErrorCorreccion, aplicar_correccion, eliminar_hibrido
from bot.ficha import formatear_borrador, resumen_borrador
from bot.modelos import (
    CabeceraLote,
    Enfermedad,
    Hibrido,
    Maleza,
    Plaga,
    RecorridaAudio,
    UmbralDanoEconomico,
    UnidadStand,
)


def _h(nombre, stand=None, **kw):
    return Hibrido(hibrido_variedad=nombre, stand_valor=stand, stand_unidad=UnidadStand.PL_M_LINEAL if stand else None, **kw)


# ---------- combinar ----------

def test_combinar_agrega_hibridos_nuevos_en_orden():
    borrador = RecorridaAudio(lote="Las Lilas", hibridos=[_h("9939", 3)])
    agregados, actualizados = borrador.combinar(RecorridaAudio(hibridos=[_h("9937", 3.2), _h("9833", 2.9)]))
    assert agregados == ["9937", "9833"]
    assert actualizados == []
    assert [h.hibrido_variedad for h in borrador.hibridos] == ["9939", "9937", "9833"]


def test_combinar_mismo_hibrido_lo_actualiza_sin_duplicar():
    borrador = RecorridaAudio(hibridos=[_h("ST 9939", 3, estado_cultivo="bueno")])
    agregados, actualizados = borrador.combinar(RecorridaAudio(hibridos=[_h("st9939", 3.4)]))
    assert agregados == []
    assert actualizados == ["st9939"]
    assert len(borrador.hibridos) == 1
    assert borrador.hibridos[0].stand_valor == 3.4
    assert borrador.hibridos[0].estado_cultivo == "bueno"


def test_combinar_hibrido_sin_nombre_siempre_se_agrega():
    borrador = RecorridaAudio(hibridos=[Hibrido(stand_valor=3)])
    agregados, _ = borrador.combinar(RecorridaAudio(hibridos=[Hibrido(stand_valor=2)]))
    assert agregados == ["sin nombre"]
    assert len(borrador.hibridos) == 2


def test_combinar_cabecera_solo_rellena_huecos():
    borrador = RecorridaAudio(lote="Las Lilas", localidad="Roberts")
    borrador.combinar(RecorridaAudio(localidad="Otra", cultivo="maíz", provincia="Buenos Aires"))
    assert borrador.localidad == "Roberts"
    assert borrador.cultivo == "maíz"
    assert borrador.provincia == "Buenos Aires"


def test_combinar_junta_transcripciones():
    borrador = RecorridaAudio(transcripcion_original="audio 1")
    borrador.combinar(RecorridaAudio(transcripcion_original="audio 2"))
    assert borrador.transcripcion_original == "audio 1\naudio 2"
    vacio = RecorridaAudio()
    vacio.combinar(RecorridaAudio(transcripcion_original="solo"))
    assert vacio.transcripcion_original == "solo"


def test_combinar_agrega_malezas_al_mismo_hibrido_sin_perder_las_anteriores():
    borrador = RecorridaAudio(hibridos=[_h("9939", 3, malezas=[Maleza(nombre="rama negra", porcentaje_cobertura=5)])])
    borrador.combinar(RecorridaAudio(hibridos=[_h("9939", plagas=[Plaga(nombre="oruga", cantidad_por_metro_lineal=0.5)],
                                                   malezas=[Maleza(nombre="Rama Negra", porcentaje_cobertura=8),
                                                            Maleza(nombre="capín")])]))
    h = borrador.hibridos[0]
    assert [(m.nombre, m.porcentaje_cobertura) for m in h.malezas] == [("Rama Negra", 8), ("capín", None)]
    assert h.plagas[0].nombre == "oruga"


def test_combinar_un_no_hay_explicito_vacia_la_lista_y_los_items_lo_cancelan():
    borrador = RecorridaAudio(hibridos=[_h("9939", plagas=[Plaga(nombre="oruga")])])
    borrador.combinar(RecorridaAudio(hibridos=[_h("9939", sin_plagas=True)]))
    assert borrador.hibridos[0].plagas == []
    assert borrador.hibridos[0].sin_plagas is True

    borrador.combinar(RecorridaAudio(hibridos=[_h("9939", plagas=[Plaga(nombre="chinche")])]))
    assert borrador.hibridos[0].sin_plagas is False
    assert borrador.hibridos[0].plagas[0].nombre == "chinche"


def test_combinar_umbral_solo_se_pisa_si_el_nuevo_lo_evaluo():
    borrador = RecorridaAudio(hibridos=[_h("9939", umbral_dano_economico=UmbralDanoEconomico.SUPERADO)])
    borrador.combinar(RecorridaAudio(hibridos=[_h("9939", 3)]))
    assert borrador.hibridos[0].umbral_dano_economico == UmbralDanoEconomico.SUPERADO


def test_tiene_datos():
    assert RecorridaAudio().tiene_datos() is False
    assert RecorridaAudio(lote="x").tiene_datos() is True
    assert RecorridaAudio(hibridos=[Hibrido()]).tiene_datos() is True
    assert RecorridaAudio(sin_plagas=True).tiene_datos() is True


# ---------- datos generales ----------

def test_generales_los_heredan_solo_los_hibridos_que_no_informaron():
    borrador = RecorridaAudio(
        sin_enfermedades=True,
        malezas=[Maleza(nombre="rama negra", porcentaje_cobertura=5)],
        hibridos=[
            _h("9939", 3, enfermedades=[Enfermedad(nombre="roya")]),
            _h("9937", 3.2),
        ],
    )
    a, b = borrador.hibridos_efectivos()
    assert [e.nombre for e in a.enfermedades] == ["roya"]
    assert a.sin_enfermedades is False
    assert b.sin_enfermedades is True
    assert a.malezas[0].nombre == b.malezas[0].nombre == "rama negra"
    # el borrador original no se modifica
    assert borrador.hibridos[1].sin_enfermedades is False
    assert borrador.hibridos[1].malezas == []


def test_generales_llegan_a_las_filas_que_se_guardan():
    borrador = RecorridaAudio(sin_plagas=True, hibridos=[_h("9939", 3), _h("9937", 2.9)])
    fichas = borrador.fichas(CabeceraLote(lote="Las Lilas"))
    assert all(f.sin_plagas for f in fichas)


def test_generales_de_un_audio_se_suman_al_borrador_y_valen_para_hibridos_de_antes_y_despues():
    borrador = RecorridaAudio(hibridos=[_h("9939", 3)])
    borrador.combinar(RecorridaAudio(sin_enfermedades=True))
    borrador.combinar(RecorridaAudio(hibridos=[_h("9937", 2.9)]))
    assert all(h.sin_enfermedades for h in borrador.hibridos_efectivos())


# ---------- lote con o sin la palabra "lote" ----------

def test_lote_con_y_sin_la_palabra_lote_es_el_mismo():
    assert RecorridaAudio(lote="Lote Las Lilas").es_otro_lote_que(CabeceraLote(lote="Las Lilas")) is False
    assert RecorridaAudio(lote="lote 3").es_otro_lote_que(CabeceraLote(lote="3")) is False
    assert RecorridaAudio(lote="Lote 3").es_otro_lote_que(CabeceraLote(lote="Lote 4")) is True


# ---------- resumen y borrador completo ----------

def test_resumen_muestra_novedades_lote_y_faltantes():
    borrador = RecorridaAudio(
        provincia="Buenos Aires", localidad="Roberts", lote="Las Lilas", cultivo="maíz",
        hibridos=[_h("9939", 3, estado_cultivo="bueno"), _h("9937", 3.2)],
    )
    texto = resumen_borrador(borrador, None, ["9939", "9937"], [])
    assert "+2 híbrido(s): 9939, 9937" in texto
    assert "Lote: Las Lilas" in texto
    assert "Roberts, Buenos Aires" in texto
    assert "se abre al guardar" in texto
    assert "Borrador (2 híbrido(s)):" in texto
    assert "Falta confirmar si hay o no hay malezas en 2 híbrido(s)" in texto
    assert "Faltan datos del lote: Ensayo" in texto
    assert "/corregir todos sin" in texto


def test_resumen_con_confirmacion_general_no_pide_confirmar_de_nuevo():
    borrador = RecorridaAudio(
        lote="Las Lilas", sin_malezas=True, sin_plagas=True, sin_enfermedades=True,
        hibridos=[_h("9939", 3, estado_cultivo="bueno")],
    )
    texto = resumen_borrador(borrador, None, [], ["9939"])
    assert "Falta confirmar" not in texto
    assert "actualicé: 9939" in texto


def test_resumen_avisa_si_el_borrador_es_de_otro_lote_que_el_abierto():
    borrador = RecorridaAudio(lote="El Ombú", hibridos=[_h("1", 3)])
    texto = resumen_borrador(borrador, CabeceraLote(lote="Las Lilas"), ["1"], [])
    assert "/cerrar" in texto


def test_borrador_completo_numera_los_hibridos_y_usa_los_generales():
    borrador = RecorridaAudio(cultivo="maíz", sin_enfermedades=True, hibridos=[_h("9939", 3), _h("9937", 2.9)])
    texto = formatear_borrador(borrador)
    assert "Híbrido 1: 9939" in texto and "Híbrido 2: 9937" in texto
    assert texto.count("Enfermedades: ✅ sin presencia (confirmado)") == 2


# ---------- correcciones manuales ----------

def _borrador():
    return RecorridaAudio(lote="Las Lilas", hibridos=[_h("9939", 3), _h("9937", 3.2)])


def test_corregir_dato_del_lote():
    b = _borrador()
    b.lote_id = 5
    assert "lote = Los Ceibos" in aplicar_correccion(b, "lote Los Ceibos")
    assert b.lote == "Los Ceibos"
    assert b.lote_id is None
    aplicar_correccion(b, "estadio V3")
    assert b.estadio_fenologico == "V3"


def test_corregir_stand_acepta_coma_y_pone_unidad():
    b = RecorridaAudio(hibridos=[Hibrido(hibrido_variedad="9939")])
    aplicar_correccion(b, "1 stand 3,1")
    assert b.hibridos[0].stand_valor == 3.1
    assert b.hibridos[0].stand_unidad == UnidadStand.PL_M_LINEAL


def test_corregir_stand_invalido_da_error_claro():
    with pytest.raises(ErrorCorreccion, match="no es un número"):
        aplicar_correccion(_borrador(), "1 stand mucho")


def test_corregir_nombre_y_estado_del_hibrido_y_borrar_con_guion():
    b = _borrador()
    aplicar_correccion(b, "2 hibrido ST 9938")
    aplicar_correccion(b, "2 estado regular con manchas")
    assert b.hibridos[1].hibrido_variedad == "ST 9938"
    assert b.hibridos[1].estado_cultivo == "regular con manchas"
    aplicar_correccion(b, "2 estado -")
    assert b.hibridos[1].estado_cultivo is None


def test_corregir_sin_presencia_de_un_hibrido_y_de_todos():
    b = _borrador()
    b.hibridos[0].plagas = [Plaga(nombre="oruga")]
    aplicar_correccion(b, "1 sin plagas")
    assert b.hibridos[0].plagas == [] and b.hibridos[0].sin_plagas is True
    assert b.hibridos[1].sin_plagas is False

    aplicar_correccion(b, "todos sin enfermedades")
    assert b.sin_enfermedades is True
    assert all(h.sin_enfermedades for h in b.hibridos_efectivos())


def test_corregir_limpiar_deja_la_lista_como_no_informada():
    b = _borrador()
    b.hibridos[0].sin_malezas = True
    aplicar_correccion(b, "1 limpiar malezas")
    assert b.hibridos[0].sin_malezas is False
    assert "malezas" in b.hibridos[0].relevamientos_sin_informar()


def test_corregir_umbral_valida_las_opciones():
    b = _borrador()
    aplicar_correccion(b, "1 umbral superado")
    assert b.hibridos[0].umbral_dano_economico == UmbralDanoEconomico.SUPERADO
    with pytest.raises(ErrorCorreccion, match="Opciones"):
        aplicar_correccion(b, "1 umbral raro")


@pytest.mark.parametrize("comando, mensaje", [
    ("", "Cómo corregir"),
    ("9 stand 3", "No existe el híbrido 9"),
    ("abc stand 3", "no es un número de híbrido"),
    ("1 color rojo", "No conozco el campo"),
    ("1 stand", "Falta el valor"),
    ("lote", "Falta el valor"),
    ("todos stand 3", "solo se puede usar"),
    ("1 sin cosas", "malezas, plagas o enfermedades"),
])
def test_corregir_errores_dan_mensajes_entendibles(comando, mensaje):
    with pytest.raises(ErrorCorreccion, match=mensaje):
        aplicar_correccion(_borrador(), comando)


def test_eliminar_hibrido():
    b = _borrador()
    assert "Saqué el híbrido 1 (9939)" in eliminar_hibrido(b, "1")
    assert [h.hibrido_variedad for h in b.hibridos] == ["9937"]
    with pytest.raises(ErrorCorreccion):
        eliminar_hibrido(b, "5")
    with pytest.raises(ErrorCorreccion):
        eliminar_hibrido(b, "")


# ---------- resumen con una línea por híbrido ----------

def test_resumen_muestra_una_linea_por_hibrido_con_todo_lo_cargado():
    borrador = RecorridaAudio(
        lote="Las Lilas", cultivo="maíz",
        sin_enfermedades=True,
        hibridos=[
            _h("ST9939", 3, estado_cultivo="bueno",
               malezas=[Maleza(nombre="rama negra", porcentaje_cobertura=5, tamano="elongada")],
               plagas=[Plaga(nombre="oruga cortadora", cantidad_por_metro_lineal=0.5)]),
            _h("ST9937", 3.2, sin_plagas=True),
            Hibrido(hibrido_variedad="DK7272"),
        ],
    )
    texto = resumen_borrador(borrador, None, ["ST9939", "ST9937"], ["DK7272"])
    assert "Borrador (3 híbrido(s)):" in texto
    assert ("1. 🆕 ST9939 — stand 3 pl/m · estado bueno · 🌾 rama negra 5% (elongada) · "
            "🐛 oruga cortadora 0.5/m · 🦠 ✅ no hay") in texto
    assert "2. 🆕 ST9937 — stand 3.2 pl/m · estado ❓ · 🌾 ❓ · 🐛 ✅ no hay · 🦠 ✅ no hay" in texto
    assert "3. ✏️ DK7272 — stand ❓ · estado ❓ · 🌾 ❓ · 🐛 ❓ · 🦠 ✅ no hay" in texto
    assert "🌐 Para todo el lote: 🦠 ✅ no hay" in texto


def test_resumen_sin_novedades_ni_marcas_en_hibridos_de_audios_anteriores():
    borrador = RecorridaAudio(lote="Las Lilas", hibridos=[_h("9939", 3), _h("9937", 3.2)])
    texto = resumen_borrador(borrador, None, ["9937"], [])
    assert "1. 9939 —" in texto
    assert "2. 🆕 9937 —" in texto


def test_resumen_muestra_umbral_acciones_y_comentarios():
    borrador = RecorridaAudio(
        acciones="aplicar insecticida en todo el lote", comentarios="buen estado general",
        umbral_dano_economico=UmbralDanoEconomico.CERCANO,
        hibridos=[_h("9939", 3)],
    )
    texto = resumen_borrador(borrador, None, ["9939"], [])
    assert "umbral cercano" in texto
    assert "acciones: aplicar insecticida en todo el lote" in texto
    assert "💬 buen estado general" in texto


def test_resumen_recorta_textos_largos():
    borrador = RecorridaAudio(hibridos=[_h("9939", 3, comentarios="x" * 200)])
    linea = [l for l in resumen_borrador(borrador, None, [], []).splitlines() if l.startswith("1.")][0]
    assert "…" in linea and len(linea) < 250
