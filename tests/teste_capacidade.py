"""Testes do núcleo de cálculo. Todos são determinísticos (sem internet).

Os valores esperados de capacidade foram calculados à mão para janeiro/2026
(31 dias: 4 segundas, 4 terças, 4 quartas, 5 quintas, 5 sextas, 5 sábados, 4 domingos).
Os parâmetros abaixo são fictícios.
"""
import datetime

import pandas as pd
import pytest
import requests

import capacidade
from capacidade import (
    REGIME_DIA_UTIL,
    REGIME_DOMINGO,
    REGIME_SABADO,
    Frota,
    Janelas,
    calcular_capacidade_bruta_periodo,
    calcular_janela_efetiva,
    distribuir_meta_anual,
    fator_aproveitamento,
    feriados_fallback,
    interpretar_data_pt,
    iterar_meses,
    limpar_serie_numerica,
    obter_data_pascoa,
    obter_feriados_nacionais,
    perfil_diurno,
    perfil_noturno,
)

FROTA = Frota(seg_diurno=1, ter_qui_diurno=2, sex_diurno=1, fds_diurno=3,
              seg_sex_noturno=2, sab_dom_noturno=1)
JANELAS = Janelas(seg_sex_diurno=4.0, fds_diurno=8.0, seg_sex_noturno=6.0,
                  sab_noturno=5.0, dom_noturno=4.0)
JAN_2026 = (2026, 1)


@pytest.fixture(autouse=True)
def cache_limpo():
    capacidade.limpar_cache_feriados()
    yield
    capacidade.limpar_cache_feriados()


# ------------------------------------------------------------------ janelas/frota
@pytest.mark.parametrize("janela, desconto, esperado", [
    (5.0, 1.0, 4.0), (2.0, 3.0, 0.0), (5.0, 0.0, 5.0), (0.0, 0.0, 0.0),
])
def test_calcular_janela_efetiva(janela, desconto, esperado):
    assert calcular_janela_efetiva(janela, desconto) == esperado


def test_janelas_com_desconto_aplica_a_todas_e_respeita_piso():
    resultado = Janelas(5.0, 10.0, 5.0, 6.0, 1.0).com_desconto(2.0)
    assert resultado == Janelas(3.0, 8.0, 3.0, 4.0, 0.0)


def test_frota_negativa_e_rejeitada():
    with pytest.raises(ValueError):
        Frota(-1, 1, 1, 1, 1, 1)


# ------------------------------------------------------------------ limpeza numérica
def test_limpar_serie_numerica_formato_brasileiro():
    serie = pd.Series(["1500,0", "100,5", "1.234,56", "0", "invalido", None])
    assert limpar_serie_numerica(serie).tolist() == [1500.0, 100.5, 1234.56, 0.0, 0.0, 0.0]


def test_limpar_serie_numerica_aceita_numeros_nativos():
    assert limpar_serie_numerica(pd.Series([10, 2.5])).tolist() == [10.0, 2.5]


# ------------------------------------------------------------------ meses
def test_iterar_meses_atravessa_virada_de_ano():
    assert list(iterar_meses((2025, 11), (2026, 2))) == [
        (2025, 11), (2025, 12), (2026, 1), (2026, 2)]


def test_iterar_meses_intervalo_invertido_e_vazio():
    assert list(iterar_meses((2026, 3), (2026, 1))) == []


@pytest.mark.parametrize("valor, esperado", [
    ("jan/26", (2026, 1)),
    ("março/2026", (2026, 3)),
    ("03/2026", (2026, 3)),
    ("12/03/2026", (2026, 3)),      # dia/mês/ano, não mês/ano
    ("15/03/2026", (2026, 3)),
    ("2012-03-01", (2012, 3)),      # ISO
    ("Dez", (None, 12)),
    (7, (None, 7)),
    (pd.Timestamp("2026-05-10"), (2026, 5)),
    ("texto qualquer", (None, None)),
    (None, (None, None)),
    (pd.NaT, (None, None)),
])
def test_interpretar_data_pt(valor, esperado):
    assert interpretar_data_pt(valor) == esperado


# ------------------------------------------------------------------ feriados
@pytest.mark.parametrize("ano, esperada", [
    (2023, datetime.date(2023, 4, 9)),
    (2024, datetime.date(2024, 3, 31)),
    (2025, datetime.date(2025, 4, 20)),
    (2026, datetime.date(2026, 4, 5)),
])
def test_obter_data_pascoa(ano, esperada):
    assert obter_data_pascoa(ano) == esperada


def test_feriados_fallback_inclui_moveis_de_2024():
    feriados = feriados_fallback(2024)
    assert "2024-02-13" in feriados  # Carnaval
    assert "2024-03-29" in feriados  # Sexta-feira Santa
    assert "2024-05-30" in feriados  # Corpus Christi
    assert "2024-11-20" in feriados  # Consciência Negra (nacional desde 2024)


def test_consciencia_negra_nao_e_nacional_antes_de_2024():
    assert "2023-11-20" not in feriados_fallback(2023)


def test_sem_api_usa_fallback(monkeypatch):
    def falha(*args, **kwargs):
        raise requests.ConnectionError("sem rede")
    monkeypatch.setattr(capacidade.requests, "get", falha)
    assert obter_feriados_nacionais(2024) == feriados_fallback(2024)


class _RespostaFalsa:
    status_code = 200

    def json(self):
        return [{"date": "2026-01-01"}, {"date": "2026-04-03"}]


def test_api_e_consultada_uma_vez_por_ano_gracas_ao_cache(monkeypatch):
    chamadas = []

    def get_falso(url, timeout):
        chamadas.append(url)
        return _RespostaFalsa()
    monkeypatch.setattr(capacidade.requests, "get", get_falso)

    assert obter_feriados_nacionais(2026) == {"2026-01-01", "2026-04-03"}
    assert obter_feriados_nacionais(2026) == {"2026-01-01", "2026-04-03"}
    assert len(chamadas) == 1


def test_apos_falha_a_api_nao_e_reconsultada_de_imediato(monkeypatch):
    chamadas = []

    def falha(*args, **kwargs):
        chamadas.append(1)
        raise requests.Timeout("lento")
    monkeypatch.setattr(capacidade.requests, "get", falha)

    obter_feriados_nacionais(2026)
    obter_feriados_nacionais(2027)
    assert len(chamadas) == 1


def test_usar_api_false_nao_toca_na_rede(monkeypatch):
    def proibido(*args, **kwargs):
        raise AssertionError("não deveria chamar a rede")
    monkeypatch.setattr(capacidade.requests, "get", proibido)
    assert obter_feriados_nacionais(2026, usar_api=False) == feriados_fallback(2026)


# ------------------------------------------------------------------ tabelas de perfil
@pytest.mark.parametrize("dia, feriado, regime, esperado", [
    (0, False, REGIME_DOMINGO, (1, 4.0)),    # segunda
    (1, False, REGIME_DOMINGO, (2, 4.0)),    # terça
    (3, False, REGIME_DOMINGO, (2, 4.0)),    # quinta
    (4, False, REGIME_DOMINGO, (1, 4.0)),    # sexta
    (5, False, REGIME_DOMINGO, (3, 8.0)),    # sábado
    (6, False, REGIME_DOMINGO, (3, 8.0)),    # domingo
    (2, True, REGIME_DOMINGO, (3, 8.0)),     # feriado em dia de semana
    (2, True, REGIME_DIA_UTIL, (3, 8.0)),    # idem, regime não altera
    (5, True, REGIME_DIA_UTIL, (3, 8.0)),    # feriado no sábado
    (6, True, REGIME_DIA_UTIL, (2, 4.0)),    # feriado no domingo + DU -> dia útil
    (6, True, REGIME_SABADO, (3, 8.0)),
])
def test_perfil_diurno(dia, feriado, regime, esperado):
    assert perfil_diurno(dia, feriado, regime, FROTA, JANELAS) == esperado


@pytest.mark.parametrize("dia, feriado, regime, esperado", [
    (0, False, REGIME_DOMINGO, (2, 6.0)),
    (4, False, REGIME_DOMINGO, (2, 6.0)),
    (5, False, REGIME_DOMINGO, (1, 5.0)),
    (6, False, REGIME_DOMINGO, (1, 4.0)),
    (2, True, REGIME_SABADO, (1, 5.0)),
    (2, True, REGIME_DOMINGO, (1, 4.0)),
    (2, True, REGIME_DIA_UTIL, (2, 6.0)),
])
def test_perfil_noturno(dia, feriado, regime, esperado):
    assert perfil_noturno(dia, feriado, regime, FROTA, JANELAS) == esperado


# ------------------------------------------------------------------ capacidade bruta
def test_capacidade_janeiro_2026_sem_feriados():
    # Diurno: seg 4x4 + ter/qua/qui 13x8 + sex 5x4 + sáb/dom 9x24 = 356
    # Noturno: seg-sex 22x12 + sáb 5x5 + dom 4x4 = 305
    resultado = calcular_capacidade_bruta_periodo(
        JAN_2026, JAN_2026, FROTA, JANELAS, considerar_feriados=False)
    assert resultado == {JAN_2026: 661.0}


@pytest.mark.parametrize("regime, esperado", [
    (REGIME_DOMINGO, 669.0),   # 01/01 (quinta): diurno 24 + noturno 4  vs. 20 normal
    (REGIME_SABADO, 670.0),    # noturno 5
    (REGIME_DIA_UTIL, 677.0),  # noturno 12
])
def test_capacidade_janeiro_2026_com_feriado_por_regime(regime, esperado):
    resultado = calcular_capacidade_bruta_periodo(
        JAN_2026, JAN_2026, FROTA, JANELAS,
        regime_feriado=regime, usar_api_feriados=False)
    assert resultado[JAN_2026] == esperado


def test_capacidade_multi_mes_tem_uma_chave_por_mes():
    resultado = calcular_capacidade_bruta_periodo(
        (2025, 12), (2026, 2), FROTA, JANELAS, usar_api_feriados=False)
    assert list(resultado) == [(2025, 12), (2026, 1), (2026, 2)]
    assert all(valor > 0 for valor in resultado.values())


def test_capacidade_periodo_invertido_devolve_vazio():
    assert calcular_capacidade_bruta_periodo(
        (2026, 3), (2026, 1), FROTA, JANELAS, usar_api_feriados=False) == {}


def test_capacidade_regime_invalido_levanta_erro():
    with pytest.raises(ValueError):
        calcular_capacidade_bruta_periodo(
            JAN_2026, JAN_2026, FROTA, JANELAS, regime_feriado="Quarta")


def test_desconto_de_janela_reduz_capacidade():
    com_desconto = JANELAS.com_desconto(1.0)
    base = calcular_capacidade_bruta_periodo(
        JAN_2026, JAN_2026, FROTA, JANELAS, considerar_feriados=False)[JAN_2026]
    reduzida = calcular_capacidade_bruta_periodo(
        JAN_2026, JAN_2026, FROTA, com_desconto, considerar_feriados=False)[JAN_2026]
    assert reduzida < base


# ------------------------------------------------------------------ perdas e meta
def test_fator_aproveitamento_soma():
    assert fator_aproveitamento([2, 3, 10, 2]) == pytest.approx(0.83)


def test_fator_aproveitamento_composto():
    assert fator_aproveitamento([2, 3, 10, 2], modo="composto") == pytest.approx(
        0.98 * 0.97 * 0.90 * 0.98)


def test_fator_aproveitamento_nunca_fica_negativo():
    assert fator_aproveitamento([100, 100, 50]) == 0.0


@pytest.mark.parametrize("percentuais, modo", [([-1], "soma"), ([101], "soma"), ([5], "xyz")])
def test_fator_aproveitamento_entradas_invalidas(percentuais, modo):
    with pytest.raises(ValueError):
        fator_aproveitamento(percentuais, modo=modo)


def test_distribuir_meta_preserva_o_total():
    meta = distribuir_meta_anual(3000.0, 2026)
    assert len(meta) == 12
    assert sum(meta.values()) == pytest.approx(3000.0)


def test_distribuir_meta_proporcional_aos_dias():
    meta = distribuir_meta_anual(3650.0, 2026)
    assert meta[(2026, 1)] == pytest.approx(310.0)   # 31 dias
    assert meta[(2026, 2)] == pytest.approx(280.0)   # 28 dias


def test_distribuir_meta_sem_proporcionalidade_divide_por_12():
    meta = distribuir_meta_anual(1200.0, 2026, proporcional_aos_dias=False)
    assert set(meta.values()) == {100.0}