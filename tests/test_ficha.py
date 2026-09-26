from bot.ficha import formatear_ficha
from bot.modelos import Enfermedad, Maleza, Plaga, RecorridaCampo, UmbralDanoEconomico, UnidadStand


def test_ficha_completa_no_marca_faltantes():
    ficha = RecorridaCampo(
        localidad="San Justo",
        lote="Lote 3",
        cultivo="soja",
        ensayo="E1",
        stand_valor=7.2,
        stand_unidad=UnidadStand.PL_M_LINEAL,
        estado_cultivo="bueno",
    )
    texto = formatear_ficha(ficha)
    assert "Faltan campos clave" not in texto
    assert "San Justo" in texto
    assert "Lote 3" in texto
    assert "7.2" in texto


def test_ficha_incompleta_marca_faltantes():
    ficha = RecorridaCampo(cultivo="maiz")
    texto = formatear_ficha(ficha)
    assert "Faltan campos clave" in texto
    assert "Localidad" in texto
    assert "Stand de plantas" in texto


def test_ficha_muestra_malezas_plagas_enfermedades():
    ficha = RecorridaCampo(
        cultivo="soja",
        malezas=[Maleza(nombre="yuyo colorado", tamano="grande", porcentaje_cobertura=30)],
        plagas=[Plaga(nombre="isoca", cantidad_por_metro_lineal=4.5, porcentaje_dano=12)],
        enfermedades=[Enfermedad(nombre="roya", porcentaje_incidencia=20, severidad="media")],
    )
    texto = formatear_ficha(ficha)
    assert "yuyo colorado" in texto
    assert "tamaño: grande" in texto
    assert "cobertura: 30" in texto
    assert "isoca" in texto
    assert "4.5/m lineal" in texto
    assert "roya" in texto
    assert "severidad: media" in texto


def test_ficha_sin_items_muestra_ninguna():
    ficha = RecorridaCampo(cultivo="soja")
    texto = formatear_ficha(ficha)
    assert "Malezas: ninguna" in texto
    assert "Plagas: ninguna" in texto
    assert "Enfermedades: ninguna" in texto


def test_umbral_default_no_evaluado():
    ficha = RecorridaCampo(cultivo="soja")
    assert ficha.umbral_dano_economico == UmbralDanoEconomico.NO_EVALUADO
    assert "no_evaluado" in formatear_ficha(ficha)
