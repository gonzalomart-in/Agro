from bot.ficha import formatear_borrador, formatear_cabecera, formatear_hibrido, partir_texto
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


def _hibrido_completo(**overrides) -> Hibrido:
    datos = dict(
        hibrido_variedad="9939",
        stand_valor=3.5,
        stand_unidad=UnidadStand.PL_M_LINEAL,
        estado_cultivo="bueno",
        sin_malezas=True,
        sin_plagas=True,
        sin_enfermedades=True,
    )
    datos.update(overrides)
    return Hibrido(**datos)


def _audio(**overrides) -> RecorridaAudio:
    datos = dict(
        provincia="Buenos Aires",
        localidad="Roberts",
        lote="Las Lilas",
        cultivo="maíz",
        ensayo="comparativo de rendimiento",
        hibridos=[_hibrido_completo()],
    )
    datos.update(overrides)
    return RecorridaAudio(**datos)


def test_hibrido_completo_no_marca_faltantes():
    texto = formatear_hibrido(_hibrido_completo(), 1)
    assert "Faltan" not in texto
    assert "Falta confirmar" not in texto
    assert "9939" in texto
    assert "3.5" in texto


def test_hibrido_incompleto_marca_faltantes():
    texto = formatear_hibrido(Hibrido(), 1)
    assert "Faltan: Híbrido/variedad, Stand de plantas, Estado del cultivo" in texto


def test_hibrido_muestra_malezas_plagas_enfermedades():
    hibrido = Hibrido(
        malezas=[Maleza(nombre="yuyo colorado", tamano="grande", porcentaje_cobertura=30)],
        plagas=[Plaga(nombre="isoca", cantidad_por_metro_lineal=4.5, porcentaje_dano=12)],
        enfermedades=[Enfermedad(nombre="roya", porcentaje_incidencia=20, severidad="media")],
    )
    texto = formatear_hibrido(hibrido, 1)
    assert "yuyo colorado" in texto
    assert "tamaño: grande" in texto
    assert "cobertura: 30" in texto
    assert "isoca" in texto
    assert "4.5 individuos por metro lineal" in texto
    assert "roya" in texto
    assert "severidad: media" in texto


def test_hibrido_distingue_no_informado_de_sin_presencia():
    texto = formatear_hibrido(Hibrido(sin_enfermedades=True), 1)
    assert "Malezas: ❓ no informado" in texto
    assert "Plagas: ❓ no informado" in texto
    assert "Enfermedades: ✅ sin presencia (confirmado)" in texto
    assert "Falta confirmar si hay o no hay: malezas, plagas" in texto


def test_relevamientos_sin_informar_con_items_no_cuenta():
    hibrido = Hibrido(plagas=[Plaga(nombre="isoca")])
    assert hibrido.relevamientos_sin_informar() == ["malezas", "enfermedades"]


def test_umbral_default_no_evaluado():
    assert Hibrido().umbral_dano_economico == UmbralDanoEconomico.NO_EVALUADO
    assert "no_evaluado" in formatear_hibrido(Hibrido(), 1)


def test_cabecera_muestra_datos_del_lote():
    texto = formatear_cabecera(CabeceraLote(provincia="Buenos Aires", localidad="Roberts", lote="Las Lilas",
                                            cultivo="maíz", ensayo="e1", estadio_fenologico="V2"))
    assert "Provincia: Buenos Aires" in texto
    assert "Localidad: Roberts" in texto
    assert "V2" in texto
    assert "Faltan datos del lote" not in texto


def test_cabecera_incompleta_marca_faltantes():
    texto = formatear_cabecera(CabeceraLote(cultivo="maíz"))
    assert "Faltan datos del lote: Localidad, Lote, Ensayo" in texto


def test_borrador_con_varios_hibridos_los_muestra_todos():
    audio = _audio(hibridos=[
        _hibrido_completo(hibrido_variedad="9939", stand_valor=3),
        _hibrido_completo(hibrido_variedad="9937", stand_valor=2.9),
    ])
    texto = formatear_borrador(audio)
    assert "Híbrido 1: 9939" in texto
    assert "Híbrido 2: 9937" in texto
    assert "2.9" in texto
    assert "abrir un lote nuevo" in texto


def test_borrador_sin_hibridos_lo_avisa():
    texto = formatear_borrador(_audio(hibridos=[]))
    assert "Todavía no hay ningún híbrido" in texto


def test_borrador_con_lote_abierto_usa_sus_datos():
    abierta = CabeceraLote(provincia="Buenos Aires", localidad="Roberts", lote="Las Lilas", cultivo="maíz", ensayo="e1")
    audio = RecorridaAudio(hibridos=[_hibrido_completo()])
    texto = formatear_borrador(audio, abierta)
    assert "Lote abierto" in texto
    assert "Las Lilas" in texto
    assert "Faltan datos del lote" not in texto


def test_borrador_avisa_si_el_audio_nombra_otro_lote():
    abierta = CabeceraLote(lote="Las Lilas")
    audio = RecorridaAudio(lote="El Ombú", hibridos=[_hibrido_completo()])
    texto = formatear_borrador(audio, abierta)
    assert "/cerrar" in texto
    assert "El Ombú" in texto


def test_partir_texto_respeta_el_limite_y_no_pierde_contenido():
    bloques = [f"bloque {i} " + "x" * 100 for i in range(50)]
    partes = partir_texto("\n\n".join(bloques), limite=1000)
    assert len(partes) > 1
    assert all(len(p) <= 1000 for p in partes)
    assert "\n\n".join(partes) == "\n\n".join(bloques)


def test_partir_texto_corto_queda_en_un_mensaje():
    assert partir_texto("hola") == ["hola"]
