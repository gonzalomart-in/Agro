import json
from unittest.mock import patch

import httpx
import pytest

from bot import extraccion
from bot.catalogo import EntradaCatalogo
from bot.config import Config
from bot.extraccion import (
    ExtraccionError,
    _audio_desde_paso_2,
    _esquema_json,
    _Paso2,
    extraer_recorrida,
    menciona_observaciones,
    menciona_relevamientos,
)
from bot.modelos import CabeceraLote, Lote, RecorridaAudio, UmbralDanoEconomico, UnidadStand


def _config_prueba() -> Config:
    return Config(
        telegram_bot_token="test",
        database_url="postgresql://x",
        ollama_host="http://ollama-test:11434",
        ollama_model="qwen2.5:3b",
        whisper_model="base",
        admin_user_ids=[],
    )


class OllamaFalso:
    """Reemplaza a `_llamar_ollama`: responde por paso y registra los pedidos."""

    def __init__(self, **respuestas):
        self.respuestas = {int(k.removeprefix("paso")): v for k, v in respuestas.items()}
        self.pedidos: list[tuple[int, str]] = []

    async def __call__(self, config, paso, prompt_usuario, max_tokens=1500):
        self.pedidos.append((paso, prompt_usuario))
        respuesta = self.respuestas[paso]
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta if isinstance(respuesta, str) else json.dumps(respuesta)

    def pasos(self):
        return [p for p, _ in self.pedidos]


async def _extraer(falso, transcripcion, **kw):
    with patch("bot.extraccion._llamar_ollama", new=falso):
        return await extraer_recorrida(_config_prueba(), transcripcion, **kw)


# ---------- flujo de pasos ----------

@pytest.mark.asyncio
async def test_audio_solo_de_stands_hace_un_unico_paso():
    falso = OllamaFalso(paso1={
        "provincia": "Buenos Aires", "localidad": "Roberts", "lote": "Las Lilas", "cultivo": "maíz",
        "hibridos": [{"hibrido_variedad": "9939", "stand_valor": 3}, {"hibrido_variedad": "9937", "stand_valor": 3.2}],
    })
    texto = "Roberts, Buenos Aires, lote Las Lilas, maíz. El 9939 tiene 3 plantas al metro y el 9937 3,2 plantas al metro"
    audio = await _extraer(falso, texto)

    assert falso.pasos() == [1]
    assert isinstance(audio, RecorridaAudio)
    assert audio.localidad == "Roberts" and audio.provincia == "Buenos Aires"
    assert [(h.hibrido_variedad, h.stand_valor) for h in audio.hibridos] == [("9939", 3), ("9937", 3.2)]
    assert all(h.stand_unidad == UnidadStand.PL_M_LINEAL for h in audio.hibridos)
    assert audio.transcripcion_original.startswith("Roberts, Buenos Aires")


@pytest.mark.asyncio
async def test_sin_stand_no_se_inventa_unidad():
    falso = OllamaFalso(paso1={"hibridos": [{"hibrido_variedad": "9939", "estado_cultivo": "bueno"}]})
    audio = await _extraer(falso, "el 9939 esta bueno")
    assert audio.hibridos[0].stand_unidad is None


@pytest.mark.asyncio
async def test_texto_vacio_del_modelo_queda_como_no_informado():
    falso = OllamaFalso(paso1={"localidad": "Rancagua", "lote": "", "hibridos": [{"hibrido_variedad": "46EA25", "estado_cultivo": " "}]})
    audio = await _extraer(falso, "localidad Rancagua, variedad 46EA25")
    assert audio.lote is None
    assert audio.hibridos[0].estado_cultivo is None

    # así otro audio del mismo lote puede completar el nombre después
    audio.combinar(RecorridaAudio(lote="Martín"))
    assert audio.lote == "Martín"


@pytest.mark.asyncio
async def test_paso_2_solo_si_el_audio_habla_de_relevamientos():
    falso = OllamaFalso(
        paso1={"hibridos": [{"hibrido_variedad": "9939", "stand_valor": 3}, {"hibrido_variedad": "9937", "stand_valor": 2.9}]},
        paso2={
            "hallazgos": [
                {"tipo": "maleza", "nombre": "rama negra", "hibrido": "9939", "porcentaje": 5},
                {"tipo": "plaga", "nombre": "oruga cortadora", "hibrido": "9939", "por_metro": 0.5},
            ],
            "ausencias": [{"tipo": "enfermedades"}],
        },
    )
    audio = await _extraer(
        falso, "el 9939 tiene 3, el 9937 2,9. No hay enfermedades en ninguno. En el 9939 hay rama negra al 5% y oruga cortadora 0,5 por metro"
    )
    assert falso.pasos() == [1, 2]
    assert audio.sin_enfermedades is True
    primero, segundo = audio.hibridos
    assert primero.malezas[0].nombre == "rama negra" and primero.malezas[0].porcentaje_cobertura == 5
    assert primero.plagas[0].cantidad_por_metro_lineal == 0.5
    assert segundo.malezas == [] and segundo.plagas == []
    # el "no hay enfermedades" general llega a los dos híbridos
    assert all(h.sin_enfermedades for h in audio.hibridos_efectivos())


@pytest.mark.asyncio
async def test_paso_3_solo_si_menciona_umbral_acciones_o_comentarios():
    falso = OllamaFalso(
        paso1={"hibridos": [{"hibrido_variedad": "9939", "stand_valor": 3}]},
        paso2={},
        paso3={"umbral_dano_economico": "superado", "acciones": "aplicar insecticida", "comentarios": "No hay comentarios"},
        paso4={"aplicaciones": [{"producto": "insecticida"}]},
    )
    audio = await _extraer(falso, "el 9939 tiene 3 plantas. Hay que aplicar insecticida, el umbral está superado")
    # "aplicar" también dispara el paso 4 (productos)
    assert falso.pasos() == [1, 2, 3, 4]
    assert [a.producto for a in audio.aplicaciones] == ["insecticida"]
    assert audio.umbral_dano_economico == UmbralDanoEconomico.SUPERADO
    assert audio.acciones == "aplicar insecticida"
    assert audio.comentarios is None  # relleno del modelo descartado
    assert audio.hibridos_efectivos()[0].acciones == "aplicar insecticida"


@pytest.mark.asyncio
async def test_extraccion_reintenta_si_json_invalido_y_luego_funciona():
    falso = OllamaFalso(paso1="x")
    respuestas = iter(["no es json valido", json.dumps({"localidad": "Rafaela", "cultivo": "maiz"})])

    async def cambiante(config, paso, prompt, max_tokens=1500):
        falso.pedidos.append((paso, prompt))
        return next(respuestas)

    audio = await _extraer(cambiante, "localidad Rafaela, maiz")
    assert audio.localidad == "Rafaela"
    assert audio.hibridos == []
    assert len(falso.pedidos) == 2


@pytest.mark.asyncio
async def test_extraccion_falla_tras_dos_intentos():
    falso = OllamaFalso(paso1=httpx.ConnectError("no se pudo conectar"))
    with pytest.raises(ExtraccionError):
        await _extraer(falso, "transcripcion")
    assert falso.pasos() == [1, 1]


@pytest.mark.asyncio
async def test_si_falla_el_paso_2_no_se_descarta_en_silencio():
    falso = OllamaFalso(paso1={"hibridos": [{"hibrido_variedad": "9939"}]}, paso2=httpx.ConnectError("x"))
    with pytest.raises(ExtraccionError, match="paso 2"):
        await _extraer(falso, "no hay enfermedades")


# ---------- lo que recibe el modelo ----------

@pytest.mark.asyncio
async def test_paso_1_recibe_catalogo_de_lotes_vocabulario_y_lote_en_curso():
    falso = OllamaFalso(paso1={"lote": "Lote 3", "lote_id": 1})
    vocabulario = [
        EntradaCatalogo("hibrido", "9939"),
        EntradaCatalogo("maleza", "rama negra", ["conyza"]),
    ]
    audio = await _extraer(
        falso,
        "hablando del lote tres",
        lotes_existentes=[Lote(id=1, nombre="Lote 3", localidad="San Justo", cultivo_habitual="soja")],
        cabecera_abierta=CabeceraLote(localidad="Roberts", lote="Las Lilas"),
        vocabulario=vocabulario,
    )
    prompt = falso.pedidos[0][1]
    assert audio.lote_id == 1
    assert "San Justo" in prompt
    assert "Híbridos/variedades: 9939" in prompt
    assert "Contexto del lote en curso" in prompt and "Las Lilas" in prompt
    # las malezas no van al paso 1: el modelo chico las tomaría como candidatas
    assert "rama negra" not in prompt


@pytest.mark.asyncio
async def test_paso_2_no_recibe_vocabulario_pero_los_nombres_se_unifican_despues():
    vocabulario = [EntradaCatalogo("hibrido", "ST9939"), EntradaCatalogo("maleza", "rama negra", ["conyza"])]
    falso = OllamaFalso(
        paso1={"hibridos": [{"hibrido_variedad": "9939", "stand_valor": 3}]},
        paso2={"hallazgos": [{"tipo": "maleza", "nombre": "conyza", "hibrido": "ST9939", "porcentaje": 5}]},
    )
    audio = await _extraer(falso, "el 9939 tiene 3 plantas y hay conyza al 5%", vocabulario=vocabulario)

    prompt_2 = falso.pedidos[1][1]
    assert "rama negra" not in prompt_2 and "Vocabulario" not in prompt_2
    assert "Híbridos detectados:" in prompt_2
    assert audio.hibridos[0].malezas[0].nombre == "rama negra"


@pytest.mark.asyncio
async def test_extraccion_es_independiente_de_otros_audios():
    falso = OllamaFalso(paso1={"hibridos": [{"hibrido_variedad": "9941", "stand_valor": 3.1}]})
    audio = await _extraer(falso, "el 9941 tiene 3,1 plantas al metro")
    assert "Borrador" not in falso.pedidos[0][1]
    assert audio.transcripcion_original == "el 9941 tiene 3,1 plantas al metro"


# ---------- decisiones de cuándo hacer cada paso ----------

@pytest.mark.parametrize("texto", [
    "hay rama negra", "No hay enfermedades en ninguno", "sin malezas", "oruga cortadora 0,5 por metro",
    "cobertura del 5 por ciento", "presencia de roya", "el 9939 tiene 5%",
])
def test_menciona_relevamientos_si(texto):
    assert menciona_relevamientos(texto, []) is True


@pytest.mark.parametrize("texto", [
    "el 9939 tiene 3 plantas al metro, el 9937 tiene 3,2 plantas al metro",
    "Localidad Roberts, provincia de Buenos Aires, lote Las Lilas, cultivo de maíz, estadio V2",
])
def test_menciona_relevamientos_no_para_audios_de_stand(texto):
    assert menciona_relevamientos(texto, []) is False


def test_menciona_relevamientos_reconoce_nombres_del_vocabulario():
    vocabulario = [EntradaCatalogo("maleza", "Capín", ["barbecho"])]
    assert menciona_relevamientos("en el 9939 aparece barbecho", vocabulario) is True
    assert menciona_relevamientos("en el 9939 aparece otra cosa", vocabulario) is False


def test_menciona_observaciones():
    assert menciona_observaciones("el umbral está superado") is True
    assert menciona_observaciones("hay que aplicar hoy") is True
    assert menciona_observaciones("el 9939 tiene 3 plantas al metro") is False


# ---------- acomodo del paso 2 ----------

def test_ausencia_es_general_salvo_que_se_nombre_el_hibrido_en_la_misma_frase():
    paso2 = _Paso2.model_validate({"ausencias": [
        {"tipo": "enfermedades", "hibrido": "9939"},
        {"tipo": "plagas", "hibrido": "9937"},
    ]})
    audio = _audio_desde_paso_2(paso2, "el 9939 tiene 3, el 9937 tiene 2. No hay enfermedades en ninguno, en el 9937 no hay plagas")
    assert audio.sin_enfermedades is True and audio.sin_plagas is False
    assert [(h.hibrido_variedad, h.sin_plagas) for h in audio.hibridos] == [("9937", True)]


def test_hallazgo_inventado_que_no_esta_en_el_audio_se_descarta():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "maleza", "nombre": "rama negra", "hibrido": "9939", "porcentaje": 100},
        {"tipo": "plaga", "nombre": "Orugas cortadoras", "hibrido": "9939", "por_metro": 0.5},
    ]})
    audio = _audio_desde_paso_2(paso2, "en el 9939 hay oruga cortadora, 0,5 por metro")
    assert audio.hibridos[0].malezas == []
    assert audio.hibridos[0].plagas[0].nombre == "Orugas cortadoras"


def test_plaga_con_la_cantidad_por_metro_en_el_campo_de_porcentaje_se_corrige():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "plaga", "nombre": "oruga", "porcentaje": 0.5, "detalle": "0,5 por metro"},
    ]})
    plaga = _audio_desde_paso_2(paso2, "hay oruga 0,5 por metro").plagas[0]
    assert plaga.cantidad_por_metro_lineal == 0.5
    assert plaga.porcentaje_dano is None


def test_detalle_que_solo_repite_un_numero_se_descarta_pero_un_tamano_se_conserva():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "maleza", "nombre": "rama negra", "porcentaje": 5, "detalle": "5% de cobertura"},
        {"tipo": "maleza", "nombre": "capín", "detalle": "elongada"},
    ]})
    malezas = _audio_desde_paso_2(paso2, "hay rama negra al 5% de cobertura y capín elongada").malezas
    assert malezas[0].tamano is None
    assert malezas[1].tamano == "elongada"


def test_campos_cruzados_el_hibrido_como_nombre_de_la_maleza():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "maleza", "nombre": "ST9939", "hibrido": "ST9939", "porcentaje": 5, "detalle": "rama negra"},
    ]})
    audio = _audio_desde_paso_2(paso2, "en el ST9939 hay rama negra al 5%")
    assert audio.hibridos[0].malezas[0].nombre == "rama negra"


def test_hallazgo_sin_hibrido_queda_como_dato_general():
    paso2 = _Paso2.model_validate({"hallazgos": [{"tipo": "enfermedad", "nombre": "roya", "porcentaje": 20}]})
    audio = _audio_desde_paso_2(paso2, "hay roya en el 20% del lote")
    assert audio.hibridos == []
    assert audio.enfermedades[0].porcentaje_incidencia == 20


# ---------- esquema compacto ----------

def test_esquema_compacto_no_tiene_variantes_null_ni_defaults_ni_campos_que_el_modelo_no_escribe():
    texto = json.dumps(_esquema_json(1))
    assert '"null"' not in texto and '"default"' not in texto and '"title"' not in texto
    assert "latitud" not in texto
    assert "stand_unidad" not in texto
    assert "malezas" not in texto  # las malezas no se piden en el paso 1

    esquema_2 = json.dumps(_esquema_json(2))
    assert "hallazgos" in esquema_2 and "ausencias" in esquema_2
    assert "stand_valor" not in esquema_2


def test_prompt_de_paso_1_y_2_comparten_el_comienzo_para_reutilizar_la_memoria_de_ollama():
    assert extraccion.PROMPT_COMUN
    for paso in (1, 2, 3):
        assert (extraccion.PROMPT_COMUN + extraccion._PASOS[paso][1]).startswith(extraccion.PROMPT_COMUN)


# ---------- a quién se le atribuye una maleza ----------

def test_maleza_dicha_al_final_de_la_lista_es_del_lote_entero_aunque_el_modelo_la_ponga_en_un_hibrido():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "maleza", "nombre": "rama negra", "hibrido": "DM46i20", "porcentaje": 5},
    ]})
    texto = "la DM46i20 tiene 3 plantas al metro, la ST46EA23 tiene 2,9 plantas al metro. Hay rama negra al 5%"
    audio = _audio_desde_paso_2(paso2, texto, ["DM46i20", "ST46EA23"])
    assert audio.hibridos == []
    assert audio.malezas[0].nombre == "rama negra"


def test_maleza_nombrada_en_la_misma_frase_que_el_hibrido_es_de_ese_hibrido():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "maleza", "nombre": "rama negra", "hibrido": "DM46i20", "porcentaje": 5},
    ]})
    texto = "el ST46EA23 tiene 3 plantas. En el DM46i20 hay rama negra al 5%"
    audio = _audio_desde_paso_2(paso2, texto, ["DM46i20", "ST46EA23"])
    assert audio.malezas == []
    assert audio.hibridos[0].malezas[0].nombre == "rama negra"


def test_referencia_a_ese_hibrido_en_la_frase_siguiente_se_respeta():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "maleza", "nombre": "yuyo colorado", "hibrido": "5010", "porcentaje": 10},
    ]})
    texto = "el 5010 tiene 2,5 plantas al metro, y en ese hay yuyo colorado al 10%. El 5020 tiene 3"
    audio = _audio_desde_paso_2(paso2, texto, ["5010", "5020"])
    assert audio.malezas == []
    assert audio.hibridos[0].hibrido_variedad == "5010"
    assert audio.hibridos[0].malezas[0].porcentaje_cobertura == 10


# ---------- datos del paso 1 que el modelo inventa ----------

from bot.modelos import Hibrido  # noqa: E402

VOCABULARIO_MARELLI = [
    EntradaCatalogo("hibrido", n, [s]) for n, s in
    (("BRV8181", "8181"), ("DK7272", "7272"), ("N7765", "7765"), ("ST9939", "9939"), ("ST9937", "9937"))
]


def _hibridos(*datos) -> list[Hibrido]:
    return [Hibrido(hibrido_variedad=n, stand_valor=s, stand_unidad=UnidadStand.PL_M_LINEAL if s else None) for n, s in datos]


def test_audio_real_hibrido_que_no_se_nombro_se_descarta():
    # transcripción real ya sin el eco de la pista de Whisper; el modelo agregó el BRV8181 de la pista
    texto = (
        "Localidad San Pedro, Buenos Aires, Lotte Marelli, Cultivo Maíz, Ensayo Comparativo de Rendimiento. "
        "El ST9939 tiene 3,5 plantas al metro. El ST9937 tiene 4 plantas al metro. El N7765 tiene 4 plantas al metro."
    )
    audio = RecorridaAudio(
        provincia="Buenos Aires", localidad="San Pedro", lote="Marelli", cultivo="maíz", ensayo="comparativo de rendimiento",
        hibridos=_hibridos(("BRV8181", 3.5), ("ST9939", 3.5), ("ST9937", 4), ("N7765", 4)),
    )
    extraccion._sin_datos_inventados(audio, texto, VOCABULARIO_MARELLI, [])
    assert [(h.hibrido_variedad, h.stand_valor) for h in audio.hibridos] == [("ST9939", 3.5), ("ST9937", 4), ("N7765", 4)]
    assert (audio.localidad, audio.lote, audio.cultivo, audio.ensayo) == ("San Pedro", "Marelli", "maíz", "comparativo de rendimiento")


def test_lo_copiado_del_ejemplo_del_pedido_se_descarta():
    texto = "Localidad Rancagua, soja, R3. La DM46i20 está bien."
    audio = RecorridaAudio(
        provincia="Buenos Aires", localidad="Roberts", lote="El Ombú", cultivo="soja", estadio_fenologico="V4",
        hibridos=[*_hibridos(("5010", 2.5)), Hibrido(hibrido_variedad="DM46i20", stand_valor=3, estado_cultivo="muy bueno")],
    )
    extraccion._sin_datos_inventados(audio, texto, [], [])
    assert (audio.provincia, audio.localidad, audio.lote, audio.cultivo, audio.estadio_fenologico) == (None, None, None, "soja", None)
    [h] = audio.hibridos
    assert (h.hibrido_variedad, h.stand_valor, h.stand_unidad, h.estado_cultivo) == ("DM46i20", None, None, None)


def test_stand_de_otro_hibrido_no_se_le_pasa_y_el_calculado_vale():
    texto = "El 9939 tiene 7 plantas en 2 metros. El 9937 tiene 4 plantas al metro."
    audio = RecorridaAudio(hibridos=_hibridos(("9939", 3.5), ("9937", 3.5)))
    extraccion._sin_datos_inventados(audio, texto, [], [])
    assert [(h.hibrido_variedad, h.stand_valor) for h in audio.hibridos] == [("9939", 3.5), ("9937", None)]


def test_codigo_mal_transcripto_se_tolera_pero_no_un_hibrido_duplicado():
    # "BRV7172" es como Whisper escribió DK7272; "9939" no se dijo, solo "9937"
    texto = "El BRV7172 tiene 3,8 plantas al metro. El 9937 tiene 4 plantas al metro."
    audio = RecorridaAudio(hibridos=_hibridos(("7272", 3.8), ("9937", 4), ("9939", 4)))
    extraccion._sin_datos_inventados(audio, texto, VOCABULARIO_MARELLI, [])
    assert [(h.hibrido_variedad, h.stand_valor) for h in audio.hibridos] == [("7272", 3.8), ("9937", 4)]


def test_lote_del_catalogo_nombrado_distinto_se_respeta():
    audio = RecorridaAudio(lote="Lote Tres", lote_id=3)
    extraccion._sin_datos_inventados(audio, "estoy en el lote 3, todo bien", [], [Lote(id=3, nombre="Lote Tres")])
    assert (audio.lote, audio.lote_id) == ("Lote Tres", 3)


@pytest.mark.parametrize("estadio, queda", [
    ("V2", True), ("R5.5", True), ("VT", True), ("floración", True), ("La 46EA25", False), ("bueno", False),
])
def test_estadio_que_no_es_un_estadio_se_descarta(estadio, queda):
    audio = RecorridaAudio(estadio_fenologico=estadio)
    extraccion._sin_datos_inventados(audio, f"Localidad Rancagua, {estadio}, todo bien", [], [])
    assert (audio.estadio_fenologico == estadio) is queda


@pytest.mark.parametrize("dicho, esperado", [
    ("Lotte-Marelli", "Marelli"), ("Lote Las Lilas", "Las Lilas"), ("Lote 3", "Lote 3"), ("El Ombú", "El Ombú"),
])
def test_la_palabra_lote_no_queda_en_el_nombre(dicho, esperado):
    audio = RecorridaAudio(lote=dicho)
    extraccion._sin_datos_inventados(audio, f"estoy en {dicho}", [], [])
    assert audio.lote == esperado


def test_lote_ya_cargado_con_otro_formato_se_reconoce():
    audio = RecorridaAudio(lote="Lotte-Marelli")
    extraccion._sin_datos_inventados(audio, "Lotte-Marelli, maíz", [], [Lote(id=9, nombre="Lote Marelli")])
    assert (audio.lote, audio.lote_id) == ("Lote Marelli", 9)


def test_hibrido_sin_nombre_con_stand_no_dicho_se_descarta():
    audio = RecorridaAudio(hibridos=[Hibrido(stand_valor=3.5)])
    extraccion._sin_datos_inventados(audio, "El lote está bien, sin novedades.", [], [])
    assert audio.hibridos == []
    audio = RecorridaAudio(hibridos=[Hibrido(stand_valor=3.5)])
    extraccion._sin_datos_inventados(audio, "Stand de tres y medio, 3,5 plantas por metro.", [], [])
    assert audio.hibridos[0].stand_valor == 3.5


# ---------- "no hay" que el modelo inventa ----------

TEXTO_RANCAGUA = (
    "Localidad Rancawa, Cultivo, Soja, Variedad 46EA25, estado de cultivo bueno, estadio fenológico R3, "
    "presencia de mancha marrón, aplicar fungicida ya que hubo lluvias de más de 40 milímetros."
)


def test_audio_real_los_no_hay_inventados_no_borran_la_mancha_marron():
    # respuesta real de qwen2.5:3b para este audio
    paso2 = _Paso2.model_validate({
        "hallazgos": [{"tipo": "enfermedad", "nombre": "Mancha marrón", "hibrido": "ST46EA25", "porcentaje": 100,
                       "detalle": "presencia de mancha marrón"}],
        "ausencias": [{"tipo": "plagas", "hibrido": "ST46EA25"}, {"tipo": "malezas", "hibrido": "ST46EA25"},
                      {"tipo": "enfermedades", "hibrido": "ST46EA25"}],
    })
    audio = _audio_desde_paso_2(paso2, TEXTO_RANCAGUA, ["ST46EA25"])
    assert [(e.nombre, e.porcentaje_incidencia) for e in audio.enfermedades] == [("Mancha marrón", None)]
    assert (audio.sin_malezas, audio.sin_plagas, audio.sin_enfermedades) == (False, False, False)
    assert audio.hibridos == []


@pytest.mark.parametrize("texto, tipo", [
    ("no hay malezas", "malezas"),
    ("No hay presencia de malezas en el lote", "malezas"),
    ("no hay plagas, malezas ni enfermedades", "enfermedades"),
    ("sin síntomas de enfermedades", "enfermedades"),
    ("no se observan plagas", "plagas"),
    ("malezas, ninguna", "malezas"),
    ("enfermedades no hay", "enfermedades"),
    ("tampoco hay plagas", "plagas"),
    ("sin plaga ni enfermedades y tampoco hay malesas", "malezas"),
    ("el lote está libre de malezas", "malezas"),
])
def test_no_hay_dicho_se_reconoce(texto, tipo):
    assert extraccion._se_dice_que_no_hay(texto, tipo)


@pytest.mark.parametrize("texto, tipo", [
    (TEXTO_RANCAGUA, "malezas"),
    (TEXTO_RANCAGUA, "plagas"),
    (TEXTO_RANCAGUA, "enfermedades"),
    ("no hay plagas pero hay malezas", "malezas"),
    ("no hay plagas. Las malezas están controladas", "malezas"),
    ("hay mancha marrón, no hay plagas", "enfermedades"),
])
def test_no_hay_que_no_se_dijo_no_se_reconoce(texto, tipo):
    assert not extraccion._se_dice_que_no_hay(texto, tipo)


def test_no_hay_no_borra_lo_nombrado_en_el_mismo_audio():
    paso2 = _Paso2.model_validate({
        "hallazgos": [{"tipo": "maleza", "nombre": "yuyo colorado"}],
        "ausencias": [{"tipo": "malezas"}],
    })
    audio = _audio_desde_paso_2(paso2, "hay algo de yuyo colorado en la cabecera, en el resto no hay malezas")
    assert [m.nombre for m in audio.malezas] == ["yuyo colorado"]
    assert audio.sin_malezas is False


# ---------- números que el modelo inventa ----------

def test_presencia_sin_numero_no_inventa_incidencia():
    # audio real: el modelo puso 100% de incidencia y en el audio no se dice ningún porcentaje
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "enfermedad", "nombre": "mancha marrón", "porcentaje": 100},
    ]})
    texto = (
        "Localidad Rancawa, Cultivo, Soja, Variedad 46EA25, estado de cultivo bueno, estadio fenológico R3, "
        "presencia de mancha marrón, aplicar fungicida ya que hubo lluvias de más de 40 milímetros."
    )
    audio = _audio_desde_paso_2(paso2, texto, ["46EA25"])
    assert audio.enfermedades[0].nombre == "mancha marrón"
    assert audio.enfermedades[0].porcentaje_incidencia is None


def test_un_numero_de_otra_cosa_no_cuenta_como_incidencia():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "enfermedad", "nombre": "mancha marrón", "porcentaje": 40},
    ]})
    texto = "presencia de mancha marrón, aplicar fungicida ya que hubo lluvias de más de 40 milímetros"
    assert _audio_desde_paso_2(paso2, texto).enfermedades[0].porcentaje_incidencia is None


@pytest.mark.parametrize("texto", [
    "hay mancha marrón con una incidencia del 20%",
    "hay mancha marrón, con 20% de incidencia",
    "mancha marrón al 20 por ciento",
    "mancha marrón, incidencia veinte",
])
def test_incidencia_dicha_se_conserva(texto):
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "enfermedad", "nombre": "mancha marrón", "porcentaje": 20},
    ]})
    assert _audio_desde_paso_2(paso2, texto).enfermedades[0].porcentaje_incidencia == 20


def test_el_porcentaje_de_otro_hallazgo_no_se_le_pasa():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "enfermedad", "nombre": "mancha marrón", "porcentaje": 10},
        {"tipo": "enfermedad", "nombre": "roya", "porcentaje": 10},
    ]})
    audio = _audio_desde_paso_2(paso2, "hay mancha marrón, roya al 10%")
    assert [(e.nombre, e.porcentaje_incidencia) for e in audio.enfermedades] == [("mancha marrón", None), ("roya", 10)]


def test_plaga_por_metro_inventada_se_descarta_y_la_dicha_en_metros_se_calcula():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "plaga", "nombre": "isoca", "por_metro": 2},
        {"tipo": "plaga", "nombre": "chinche", "por_metro": 1.5},
    ]})
    audio = _audio_desde_paso_2(paso2, "hay isoca. Hay chinche, 3 en 2 metros")
    assert [(p.nombre, p.cantidad_por_metro_lineal) for p in audio.plagas] == [("isoca", None), ("chinche", 1.5)]


def test_los_decimales_no_parten_las_frases():
    paso2 = _Paso2.model_validate({"hallazgos": [
        {"tipo": "plaga", "nombre": "oruga cortadora", "hibrido": "9939", "por_metro": 0.5},
    ]})
    audio = _audio_desde_paso_2(paso2, "en el 9939 hay oruga cortadora 0,5 por metro", ["9939"])
    assert audio.hibridos[0].plagas[0].cantidad_por_metro_lineal == 0.5
