import json
from datetime import datetime, timezone

from bot.exportar import calcular_fecha_desde, generar_excel, registros_a_dataframe


def _registro(**overrides):
    base = {
        "id": 1,
        "fecha_hora": datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc),
        "localidad": "San Justo",
        "lote": "Lote 3",
        "cultivo": "soja",
        "ensayo": "E1",
        "estadio_fenologico": "V6",
        "stand_valor": 7.2,
        "stand_unidad": "pl/m lineal",
        "estado_cultivo": "bueno",
        "malezas": json.dumps([{"nombre": "yuyo colorado", "tamano": "grande", "porcentaje_cobertura": 30, "observacion": None}]),
        "plagas": json.dumps([]),
        "enfermedades": json.dumps([{"nombre": "roya", "porcentaje_incidencia": 20, "severidad": "media", "observacion": None}]),
        "umbral_dano_economico": "no_evaluado",
        "acciones": "ninguna",
        "comentarios": "ok",
        "transcripcion_original": "texto",
    }
    base.update(overrides)
    return base


def test_fecha_hora_se_convierte_a_argentina_sin_timezone():
    df = registros_a_dataframe([_registro()])
    fecha = df.iloc[0]["fecha_hora"]
    assert fecha.tzinfo is None
    # 15:00 UTC == 12:00 hora Argentina (UTC-3)
    assert fecha.hour == 12


def test_aplanado_de_malezas_a_texto():
    df = registros_a_dataframe([_registro()])
    texto = df.iloc[0]["malezas"]
    assert "yuyo colorado" in texto
    assert "porcentaje_cobertura=30" in texto


def test_aplanado_de_lista_vacia_es_texto_vacio():
    df = registros_a_dataframe([_registro()])
    assert df.iloc[0]["plagas"] == ""


def test_generar_excel_produce_bytes_no_vacios():
    buffer = generar_excel([_registro()])
    assert len(buffer.getvalue()) > 0


def test_generar_excel_sin_registros_no_falla():
    buffer = generar_excel([])
    assert len(buffer.getvalue()) > 0


def test_calcular_fecha_desde_dias():
    fecha = calcular_fecha_desde(7)
    assert fecha is not None
    assert fecha.tzinfo is not None


def test_calcular_fecha_desde_none():
    assert calcular_fecha_desde(None) is None


def test_lista_vacia_confirmada_se_exporta_como_sin_presencia():
    df = registros_a_dataframe([_registro(plagas=json.dumps([]), sin_plagas=True)])
    assert df.iloc[0]["plagas"] == "Sin presencia"


def test_lista_vacia_no_confirmada_queda_vacia():
    df = registros_a_dataframe([_registro(plagas=json.dumps([]), sin_plagas=False)])
    assert df.iloc[0]["plagas"] == ""


def test_exporta_provincia_y_visita():
    df = registros_a_dataframe([_registro(provincia="Buenos Aires", visita_id=5)])
    assert df.iloc[0]["provincia"] == "Buenos Aires"
    assert df.iloc[0]["visita_id"] == 5
