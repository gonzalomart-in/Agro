import pytest

from bot.ficha import calcular_stand_pl_m_lineal


def test_36_plantas_en_5_metros():
    assert calcular_stand_pl_m_lineal(36, 5) == 7.2


def test_redondeo_a_dos_decimales():
    assert calcular_stand_pl_m_lineal(10, 3) == 3.33


def test_metros_cero_lanza_error():
    with pytest.raises(ValueError):
        calcular_stand_pl_m_lineal(10, 0)


def test_metros_negativos_lanza_error():
    with pytest.raises(ValueError):
        calcular_stand_pl_m_lineal(10, -1)
