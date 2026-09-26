import pytest

from bot.catalogo import (
    EntradaCatalogo,
    formatear_listado,
    formatear_para_prompt,
    parsear_entradas,
    tipo_valido,
    unificar_nombres,
)
from bot.modelos import Enfermedad, Hibrido, Maleza, Plaga, RecorridaAudio


def test_tipo_valido_acepta_plurales_y_tildes():
    assert tipo_valido("híbridos") == "hibrido"
    assert tipo_valido("Hibrido") == "hibrido"
    assert tipo_valido("malezas") == "maleza"
    assert tipo_valido("enfermedad") == "enfermedad"
    assert tipo_valido("cosas") is None
    assert tipo_valido(None) is None


def test_parsear_entradas_una_por_linea_con_sinonimos():
    entradas = parsear_entradas("maleza", "rama negra = conyza, buva\nyuyo colorado\n\n  ")
    assert entradas == [
        EntradaCatalogo("maleza", "rama negra", ["conyza", "buva"]),
        EntradaCatalogo("maleza", "yuyo colorado", []),
    ]


def test_parsear_entradas_nota_no_separa_por_igual():
    entradas = parsear_entradas("nota", "el testigo = híbrido 9939")
    assert entradas == [EntradaCatalogo("nota", "el testigo = híbrido 9939")]


def test_parsear_entradas_ignora_lineas_sin_nombre():
    assert parsear_entradas("hibrido", "= algo\n   \n") == []


def test_formatear_para_prompt_vacio():
    assert formatear_para_prompt([]) == ""


def test_formatear_para_prompt_incluye_sinonimos_y_notas():
    texto = formatear_para_prompt([
        EntradaCatalogo("hibrido", "9939"),
        EntradaCatalogo("maleza", "rama negra", ["conyza"]),
        EntradaCatalogo("nota", "el testigo es el 9939"),
    ])
    assert "Híbridos/variedades: 9939" in texto
    assert "rama negra (también: conyza)" in texto
    assert "Nota: el testigo es el 9939" in texto


def test_formatear_listado_vacio_explica_como_cargar():
    assert "/agregar" in formatear_listado([])


def test_unificar_nombres_usa_sinonimos_y_tolera_plurales():
    entradas = [
        EntradaCatalogo("maleza", "rama negra", ["conyza"]),
        EntradaCatalogo("plaga", "oruga cortadora"),
        EntradaCatalogo("enfermedad", "roya"),
    ]
    audio = RecorridaAudio(hibridos=[
        Hibrido(
            malezas=[Maleza(nombre="Conyza")],
            plagas=[Plaga(nombre="orugas cortadoras", cantidad_por_metro_lineal=0.5)],
            enfermedades=[Enfermedad(nombre="Roya")],
        )
    ])
    unificar_nombres(audio, entradas)
    h = audio.hibridos[0]
    assert h.malezas[0].nombre == "rama negra"
    assert h.plagas[0].nombre == "oruga cortadora"
    assert h.plagas[0].cantidad_por_metro_lineal == 0.5
    assert h.enfermedades[0].nombre == "roya"


def test_unificar_nombres_deja_lo_desconocido_como_esta():
    audio = RecorridaAudio(hibridos=[Hibrido(malezas=[Maleza(nombre="capín")])])
    unificar_nombres(audio, [EntradaCatalogo("maleza", "rama negra")])
    assert audio.hibridos[0].malezas[0].nombre == "capín"


def test_hibridos_solo_se_unifican_por_coincidencia_exacta():
    entradas = [EntradaCatalogo("hibrido", "9939"), EntradaCatalogo("hibrido", "DM 2738", ["dm2738x"])]
    audio = RecorridaAudio(hibridos=[
        Hibrido(hibrido_variedad="híbrido 9939"),
        Hibrido(hibrido_variedad="9938"),
        Hibrido(hibrido_variedad="dm 2738"),
        Hibrido(hibrido_variedad="DM2738X"),
    ])
    unificar_nombres(audio, entradas)
    assert [h.hibrido_variedad for h in audio.hibridos] == ["9939", "9938", "DM 2738", "DM 2738"]


def test_unificar_ensayo():
    audio = RecorridaAudio(ensayo="Comparativo de rendimiento")
    unificar_nombres(audio, [EntradaCatalogo("ensayo", "comparativo de rendimiento")])
    assert audio.ensayo == "comparativo de rendimiento"


def test_unificar_sin_catalogo_no_toca_nada():
    audio = RecorridaAudio(hibridos=[Hibrido(hibrido_variedad="x")])
    unificar_nombres(audio, [])
    assert audio.hibridos[0].hibrido_variedad == "x"


LOCALIDADES = [EntradaCatalogo("localidad", n) for n in ("Rancagua", "San Pedro", "Roberts")]


@pytest.mark.parametrize("escuchado", ["Rancawa", "rancagua", "Rancagüa", "Rancaua"])
def test_localidad_mal_transcripta_se_lleva_a_la_conocida(escuchado):
    audio = RecorridaAudio(localidad=escuchado)
    unificar_nombres(audio, LOCALIDADES)
    assert audio.localidad == "Rancagua"


@pytest.mark.parametrize("escuchado", ["Pergamino", "Salto", "San Pablo", "Rojas"])
def test_localidad_desconocida_o_distinta_no_se_toca(escuchado):
    audio = RecorridaAudio(localidad=escuchado)
    unificar_nombres(audio, LOCALIDADES)
    assert audio.localidad == escuchado


def test_localidad_por_sinonimo_cargado():
    audio = RecorridaAudio(localidad="Rancawa")
    unificar_nombres(audio, [EntradaCatalogo("localidad", "Rancagua", ["Rancawa"])])
    assert audio.localidad == "Rancagua"


def test_localidad_es_un_tipo_valido():
    assert tipo_valido("localidad") == "localidad"
    assert tipo_valido("Localidades") == "localidad"


from bot.catalogo import normalizar_transcripcion, texto_para_whisper  # noqa: E402

HIBRIDOS = [EntradaCatalogo("hibrido", n) for n in ("9939", "9937", "98RR")]


def test_normalizar_transcripcion_junta_hibridos_partidos_por_whisper():
    texto = "el 99, 39 tiene tres plantas al metro, el 99 37 tiene algo y el 98 RR tiene otro"
    assert normalizar_transcripcion(texto, HIBRIDOS) == (
        "el 9939 tiene tres plantas al metro, el 9937 tiene algo y el 98RR tiene otro"
    )


def test_normalizar_transcripcion_arregla_decimales_partidos():
    texto = "tiene 3, 2 plantas al metro, otro 2, 9 plantas por metro y 1,5 plantas al metro"
    assert normalizar_transcripcion(texto, []) == (
        "tiene 3,2 plantas al metro, otro 2,9 plantas por metro y 1,5 plantas al metro"
    )


def test_normalizar_transcripcion_decimal_hablado():
    assert normalizar_transcripcion("son 3 coma 5 plantas", []) == "son 3,5 plantas"


def test_normalizar_transcripcion_no_toca_numeros_que_no_son_decimales_ni_hibridos():
    texto = "el 97, 37 tiene 12, 40 hectáreas"
    assert normalizar_transcripcion(texto, HIBRIDOS) == texto


def test_normalizar_transcripcion_no_junta_dentro_de_numeros_mas_largos():
    assert normalizar_transcripcion("son 19939 plantas", HIBRIDOS) == "son 19939 plantas"


def test_normalizar_transcripcion_transcripcion_real_de_whisper():
    texto = (
        "Localidad Roberts, Buenos Aires, lote las lilas, cultivo de maíz. En el 99, 39 tiene tres "
        "plantas al metro, el 97, 37 tiene 3, 2 plantas al metro, el 98, 33 tiene 2, 9 plantas al metro."
    )
    resultado = normalizar_transcripcion(texto, HIBRIDOS)
    assert "el 9939 tiene tres plantas" in resultado
    assert "3,2 plantas" in resultado
    assert "2,9 plantas" in resultado


def test_texto_para_whisper_incluye_hibridos_al_final_y_sin_frases_de_ejemplo():
    pista = texto_para_whisper(HIBRIDOS + [EntradaCatalogo("maleza", "rama negra")], "maíz")
    assert "rama negra" in pista
    assert pista.endswith("Híbridos: 9939, 9937, 98RR.")
    # una frase de ejemplo con un stand terminaba "dicha" en la transcripción
    assert "plantas" not in pista


def test_quitar_eco_de_pista_saca_lo_que_whisper_copio():
    from bot.catalogo import quitar_eco_de_pista
    pista = "Localidades: Rancagua, San Pedro. Híbridos: BRV8181, DK7272, N7765, ST9939."
    texto = (
        "Localidad San Pedro, lote Marelli. Híbridos: BRV8181, DK7272, N7765. "
        "El ST9939 tiene 3.5 plantas al metro. San Pedro."
    )
    assert quitar_eco_de_pista(texto, pista) == "Localidad San Pedro, lote Marelli. El ST9939 tiene 3.5 plantas al metro. San Pedro."
    assert quitar_eco_de_pista(texto, "") == texto


def test_quitar_eco_del_ejemplo_viejo_del_audio_real():
    from bot.catalogo import quitar_eco_de_pista
    pista = "Híbridos: BRV8181, DK7272. Recorrida: el BRV8181 tiene 3,5 plantas al metro, el DK7272 tiene 2,9 plantas al metro."
    texto = "Localidad San Pedro, Lotte Marelli. El 99, el 39 tiene 3,5 plantas al metro. El BRV8181 tiene 3,5 plantas al metro."
    assert quitar_eco_de_pista(texto, pista) == "Localidad San Pedro, Lotte Marelli. El 99, el 39 tiene 3,5 plantas al metro."


def test_texto_para_whisper_incluye_localidades():
    pista = texto_para_whisper(HIBRIDOS + LOCALIDADES, "soja")
    assert "Localidades: Rancagua, San Pedro, Roberts." in pista


def test_texto_para_whisper_sin_vocabulario_es_vacio():
    assert texto_para_whisper([]) == ""


from bot.catalogo import buscar_coincidencias, sinonimos_utiles  # noqa: E402


def test_coincidencia_exacta_ignora_tildes_mayusculas_y_espacios():
    entradas = [EntradaCatalogo("maleza", "Rama Negra")]
    exacta, parecidas = buscar_coincidencias("maleza", "rama  negra", entradas)
    assert exacta is entradas[0] and parecidas == []


def test_coincidencia_exacta_tambien_por_sinonimo():
    entradas = [EntradaCatalogo("hibrido", "ST9939VIP3", ["9939"])]
    exacta, _ = buscar_coincidencias("hibrido", "9939", entradas)
    assert exacta is entradas[0]


def test_9939_se_parece_a_ST9939VIP3_y_pregunta():
    entradas = [EntradaCatalogo("hibrido", "ST9939VIP3")]
    exacta, parecidas = buscar_coincidencias("hibrido", "9939", entradas)
    assert exacta is None
    assert parecidas == entradas


def test_hibrido_con_prefijo_hibrido_se_compara_sin_la_palabra():
    exacta, _ = buscar_coincidencias("hibrido", "híbrido 9939", [EntradaCatalogo("hibrido", "9939")])
    assert exacta is not None


def test_hibridos_distintos_de_un_digito_no_se_consideran_parecidos():
    entradas = [EntradaCatalogo("hibrido", "9939"), EntradaCatalogo("hibrido", "DM 2738")]
    assert buscar_coincidencias("hibrido", "9938", entradas) == (None, [])
    assert buscar_coincidencias("hibrido", "DM 2739", entradas) == (None, [])


def test_el_mismo_nombre_en_otro_tipo_no_cuenta():
    entradas = [EntradaCatalogo("maleza", "roya")]
    assert buscar_coincidencias("enfermedad", "roya", entradas) == (None, [])


def test_nota_solo_se_compara_por_texto_exacto():
    entradas = [EntradaCatalogo("nota", "el testigo es el 9939")]
    assert buscar_coincidencias("nota", "El testigo es el 9939", entradas)[0] is entradas[0]
    assert buscar_coincidencias("nota", "el testigo es el 9937", entradas) == (None, [])


def test_plural_y_singular_se_consideran_parecidos():
    entradas = [EntradaCatalogo("plaga", "oruga cortadora")]
    _, parecidas = buscar_coincidencias("plaga", "orugas cortadoras", entradas)
    assert parecidas == entradas


def test_sinonimos_utiles_saca_repetidos_y_el_nombre_oficial():
    assert sinonimos_utiles("ST9939VIP3", ["9939", "st9939vip3", "9939", " ST 9939 ", ""]) == ["9939", "ST 9939"]


# ---------- vocabulario técnico, variedades y correcciones de palabras ----------

from bot.modelos import etiqueta_material  # noqa: E402
from bot.vocabulario_base import VOCABULARIO_BASE  # noqa: E402


@pytest.mark.parametrize("cultivo, esperado", [
    ("maíz", ("híbrido", "híbridos")),
    ("Maiz", ("híbrido", "híbridos")),
    ("girasol", ("híbrido", "híbridos")),
    ("sorgo", ("híbrido", "híbridos")),
    ("soja", ("variedad", "variedades")),
    ("Trigo", ("variedad", "variedades")),
    ("cebada", ("variedad", "variedades")),
    (None, ("híbrido/variedad", "híbridos/variedades")),
    ("quinoa", ("híbrido/variedad", "híbridos/variedades")),
])
def test_etiqueta_material_segun_cultivo(cultivo, esperado):
    assert etiqueta_material(cultivo) == esperado


def test_pista_de_whisper_para_soja_habla_de_variedades():
    variedades = [EntradaCatalogo("hibrido", n) for n in ("DM46i20", "ST 46EA23", "ST 38EA23")]
    pista = texto_para_whisper(variedades, "soja")
    assert "Variedades: DM46i20, ST 46EA23, ST 38EA23." in pista


def test_pista_de_whisper_respeta_el_presupuesto_y_conserva_lo_importante():
    from bot.catalogo import PRESUPUESTO_PISTA_WHISPER
    muchos = [EntradaCatalogo("hibrido", f"ST{9000 + i}") for i in range(100)]
    terminos = [EntradaCatalogo("termino", f"palabra{i}") for i in range(100)]
    pista = texto_para_whisper(muchos + terminos, "maíz")
    assert len(pista) <= PRESUPUESTO_PISTA_WHISPER
    assert "Híbridos: ST9000" in pista  # los híbridos, lo más importante, entran primero
    assert pista.rstrip().endswith(".")
    assert "Términos:" in pista or len(pista) > 500


def test_pista_de_whisper_incluye_terminos_tecnicos_del_vocabulario_base():
    pista = texto_para_whisper(VOCABULARIO_BASE, "soja")
    assert "variedad" in pista


def test_corrige_variedad_mal_transcripta():
    texto = "En la válida DM 46i20 hay 3 plantas por metro, y las válidas restantes 2"
    resultado = normalizar_transcripcion(texto, VOCABULARIO_BASE)
    assert "En la variedad DM 46i20" in resultado
    assert "las variedades restantes" in resultado


def test_no_corrige_palabras_que_solo_contienen_la_mal_escrita():
    assert normalizar_transcripcion("una validación", VOCABULARIO_BASE) == "una validación"
    assert normalizar_transcripcion("las plantas estan bien", VOCABULARIO_BASE) == "las plantas estan bien"


def test_correcciones_del_usuario_se_aplican_y_mandan_sobre_el_base():
    propias = [EntradaCatalogo("termino", "cobertura", ["cobertora", "covertura"])]
    assert normalizar_transcripcion("una covertura del 5%", propias + VOCABULARIO_BASE) == "una cobertura del 5%"


def test_una_diferencia_solo_de_tilde_no_se_corrige_ni_rompe_nada():
    assert normalizar_transcripcion("el hibrido 9939", VOCABULARIO_BASE) == "el hibrido 9939"


@pytest.mark.parametrize("dicho, esperado", [
    ("estadio v 4", "estadio V4"),
    ("estadio v4,", "estadio V4,"),
    ("estadio R cuatro", "estadio R4"),
    ("en V-2 hoy", "en V2 hoy"),
    ("estadio r 5.", "estadio R5."),
])
def test_normaliza_etapas_fenologicas(dicho, esperado):
    assert normalizar_transcripcion(dicho, []) == esperado


@pytest.mark.parametrize("texto", ["por dos plantas", "hay 3 v 2,5 plantas", "el ve dos", "revisé 4 lotes", "vale 3"])
def test_las_etapas_no_tocan_otras_palabras(texto):
    assert normalizar_transcripcion(texto, []) == texto


def test_el_vocabulario_base_no_tiene_correcciones_que_rompan_palabras_comunes():
    palabras_comunes = {"estan", "esta", "están", "valido", "alambre", "hay", "sin"}
    for e in VOCABULARIO_BASE:
        if e.tipo == "termino":
            for mal in e.sinonimos:
                assert mal.lower() not in palabras_comunes, mal


def test_el_vocabulario_base_tiene_lo_esencial_y_sin_duplicados():
    por_tipo = {}
    for e in VOCABULARIO_BASE:
        por_tipo.setdefault(e.tipo, []).append(e.nombre.lower())
    assert {"termino", "maleza", "plaga", "enfermedad"} <= set(por_tipo)
    assert "rama negra" in por_tipo["maleza"] and "roya" in por_tipo["enfermedad"]
    for tipo, nombres in por_tipo.items():
        assert len(nombres) == len(set(nombres)), tipo


def test_el_vocabulario_base_unifica_sinonimos_de_malezas():
    audio = RecorridaAudio(hibridos=[Hibrido(malezas=[Maleza(nombre="Conyza"), Maleza(nombre="amaranthus")])])
    unificar_nombres(audio, VOCABULARIO_BASE)
    assert [m.nombre for m in audio.hibridos[0].malezas] == ["rama negra", "yuyo colorado"]


def test_termino_solo_se_compara_por_texto_exacto_al_cargar():
    entradas = [EntradaCatalogo("termino", "variedad", ["válida"])]
    assert buscar_coincidencias("termino", "variedades", entradas) == (None, [])
    assert buscar_coincidencias("termino", "Válida", entradas)[0] is entradas[0]


def test_tipo_termino_y_cultivar_son_alias_validos():
    assert tipo_valido("términos") == "termino"
    assert tipo_valido("palabra") == "termino"
    assert tipo_valido("cultivar") == "hibrido"
    assert tipo_valido("variedad") == "hibrido"
