"""Testes da leitura/normalização da planilha de plano."""
import pandas as pd
import pytest

from planilha import ErroPlanilha, detectar_colunas, processar_planilha


def test_horas_plano_nao_e_confundida_com_coluna_de_ano():
    df = pd.DataFrame({"Escopo": ["A"], "Horas_Plano": [2020], "Mês": [1]})
    colunas = detectar_colunas(df)
    assert colunas["horas"] == "Horas_Plano"
    assert colunas["ano"] is None


def test_mes_ano_conta_como_data_e_nao_como_mes_nem_ano():
    df = pd.DataFrame({"Escopo": ["A"], "Mês/Ano": ["jan/26"], "Horas_Totais": [10]})
    colunas = detectar_colunas(df)
    assert colunas["data"] == "Mês/Ano"
    assert colunas["ano"] is None and colunas["mes"] is None


def test_processa_horas_por_mes_e_escopo():
    df = pd.DataFrame({
        "Escopo": ["Inspeção", "Inspeção", "Reparo"],
        "Mês/Ano": ["jan/26", "jan/26", "fev/26"],
        "Horas_Totais": ["10,5", 20, "30"],
    })
    resultado = processar_planilha(df, ano_padrao=2026)
    assert resultado.horas_mensais == {(2026, 1): 30.5, (2026, 2): 30.0}
    assert resultado.escopos_mensais[(2026, 1)] == ["Inspeção"]
    assert resultado.resumo_escopo.iloc[0]["Escopo"] == "Inspeção"  # 30,5 h > 30 h
    assert resultado.resumo_escopo["Horas Totais"].sum() == pytest.approx(60.5)
    assert resultado.avisos == []


def test_meses_sem_demanda_entre_extremos_viram_zero():
    df = pd.DataFrame({"Escopo": ["A", "A"], "Mês/Ano": ["jan/26", "abr/26"],
                       "Horas_Totais": [10, 40]})
    horas = processar_planilha(df, 2026).horas_mensais
    assert horas == {(2026, 1): 10.0, (2026, 2): 0.0, (2026, 3): 0.0, (2026, 4): 40.0}


def test_coluna_mes_numerico_usa_ano_padrao():
    df = pd.DataFrame({"Escopo": ["A"], "Mês": [3], "Horas_Totais": [15]})
    assert processar_planilha(df, 2027).horas_mensais == {(2027, 3): 15.0}


def test_quantidade_vezes_horas_por_unidade():
    df = pd.DataFrame({"Escopo": ["A"], "Mês": [1], "Quantidade": [4],
                       "Horas_por_Unidade": [2.5]})
    assert processar_planilha(df, 2026).horas_mensais == {(2026, 1): 10.0}


def test_sem_coluna_de_horas_levanta_erro_em_vez_de_chutar_outra_coluna():
    df = pd.DataFrame({"Escopo": ["A"], "Ano": [2026], "Prioridade": [1]})
    with pytest.raises(ErroPlanilha):
        processar_planilha(df, 2026)


def test_linhas_sem_data_geram_aviso_e_ficam_fora_do_plano_mensal():
    df = pd.DataFrame({"Escopo": ["A", "B"], "Mês/Ano": ["jan/26", None],
                       "Horas_Totais": [10, 5]})
    resultado = processar_planilha(df, 2026)
    assert resultado.horas_mensais == {(2026, 1): 10.0}
    assert resultado.linhas_sem_data == 1
    assert resultado.horas_sem_data == 5.0
    assert resultado.resumo_escopo["Horas Totais"].sum() == 15.0
    assert any("sem data" in aviso for aviso in resultado.avisos)


def test_sem_nenhuma_data_distribui_igualmente_pelo_ano_padrao():
    df = pd.DataFrame({"Escopo": ["A", "B"], "Horas_Totais": [60, 60]})
    resultado = processar_planilha(df, 2026)
    assert len(resultado.horas_mensais) == 12
    assert set(resultado.horas_mensais.values()) == {10.0}
    assert any("igualmente" in aviso for aviso in resultado.avisos)


def test_mapeamento_manual_sobrescreve_deteccao():
    df = pd.DataFrame({"Atividade": ["A"], "Carga (h)": [8], "Mês": [2]})
    resultado = processar_planilha(df, 2026, mapeamento={"horas": "Carga (h)"})
    assert resultado.horas_mensais == {(2026, 2): 8.0}


def test_mapeamento_para_coluna_inexistente_levanta_erro():
    df = pd.DataFrame({"Escopo": ["A"], "Horas_Totais": [1]})
    with pytest.raises(ErroPlanilha):
        processar_planilha(df, 2026, mapeamento={"horas": "Não existe"})