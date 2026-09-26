from bot.modelos import CabeceraLote, Hibrido, RecorridaAudio, RecorridaCampo, normalizar_texto


def test_normalizar_texto_ignora_tildes_mayusculas_y_espacios():
    assert normalizar_texto("Las  Lilas") == normalizar_texto("las lilas") == "laslilas"
    assert normalizar_texto("El Ombú") == "elombu"
    assert normalizar_texto(None) == ""


def test_como_cabecera_descarta_datos_de_hibrido():
    ficha = RecorridaCampo(lote="Las Lilas", estadio_fenologico="V2", hibrido_variedad="9939", stand_valor=3.5)
    cabecera = ficha.como_cabecera()
    assert type(cabecera) is CabeceraLote
    assert cabecera.lote == "Las Lilas"
    assert cabecera.estadio_fenologico == "V2"
    assert not hasattr(cabecera, "hibrido_variedad")


def test_completar_con_lote_abierto_manda_el_lote_abierto_y_rellena_huecos():
    abierta = CabeceraLote(provincia="Buenos Aires", localidad="Roberts", lote="Las Lilas", lote_id=7)
    audio = RecorridaAudio(localidad="Robert", estadio_fenologico="V2")
    efectiva = audio.completar_con(abierta)
    assert efectiva.localidad == "Roberts"
    assert efectiva.lote == "Las Lilas"
    assert efectiva.lote_id == 7
    assert efectiva.estadio_fenologico == "V2"


def test_completar_con_sin_lote_abierto_usa_los_datos_del_audio():
    audio = RecorridaAudio(lote="Las Lilas", hibridos=[Hibrido(hibrido_variedad="9939")])
    efectiva = audio.completar_con(None)
    assert efectiva.lote == "Las Lilas"
    assert type(efectiva) is CabeceraLote


def test_es_otro_lote_que():
    abierta = CabeceraLote(lote="Las Lilas")
    assert RecorridaAudio(lote="las lilas").es_otro_lote_que(abierta) is False
    assert RecorridaAudio(lote="El Ombú").es_otro_lote_que(abierta) is True
    assert RecorridaAudio(lote=None).es_otro_lote_que(abierta) is False
    assert RecorridaAudio(lote="El Ombú").es_otro_lote_que(None) is False


def test_fichas_genera_una_por_hibrido_con_los_datos_del_lote():
    audio = RecorridaAudio(
        hibridos=[
            Hibrido(hibrido_variedad="9939", stand_valor=3),
            Hibrido(hibrido_variedad="9937", stand_valor=2.9, sin_plagas=True),
        ],
        transcripcion_original="texto del audio",
    )
    cabecera = CabeceraLote(provincia="Buenos Aires", lote="Las Lilas", lote_id=4)
    fichas = audio.fichas(cabecera)

    assert [f.hibrido_variedad for f in fichas] == ["9939", "9937"]
    assert all(f.lote == "Las Lilas" and f.provincia == "Buenos Aires" and f.lote_id == 4 for f in fichas)
    assert fichas[1].stand_valor == 2.9
    assert fichas[1].sin_plagas is True
    assert fichas[0].sin_plagas is False
    assert fichas[0].transcripcion_original == "texto del audio"


def test_fichas_sin_hibridos_es_lista_vacia():
    assert RecorridaAudio(lote="Las Lilas").fichas(CabeceraLote(lote="Las Lilas")) == []
