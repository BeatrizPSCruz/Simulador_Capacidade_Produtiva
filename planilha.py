"""Leitura e normalização da planilha de plano (horas demandadas por mês/escopo).

Separado do Streamlit para ser testável. A detecção de colunas é por palavras
inteiras (e não por trecho de texto): "Horas_Plano" não é confundida com
uma coluna de ano. Quando a detecção não é segura, o app pode passar um
``mapeamento`` manual escolhido pelo usuário.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from capacidade import AnoMes, interpretar_data_pt, iterar_meses, limpar_serie_numerica

CAMPOS = ("horas", "escopo", "data", "ano", "mes")

_HORAS_TOTAIS = (
    "horas_totais", "horas totais", "horas_necessarias", "horas necessarias",
    "horas_plano", "qtd_horas", "tempo_h", "total_horas", "horas_tot",
)
_QUANTIDADE = {"quantidade", "qtd", "qtde", "unidades"}
_HORAS_UNITARIAS = ("horas_por_unidade", "horas/unidade", "tempo_unidade", "h_unit", "horas_unit")
_ESCOPO = {"escopo", "scope", "projeto", "servico", "atividade", "grupo", "tipo"}
_EXCLUI_HORAS = {"unid", "unidade", "unit", "por"}


class ErroPlanilha(ValueError):
    """Planilha fora do formato esperado."""


@dataclass
class ResultadoPlanilha:
    horas_mensais: dict[AnoMes, float]
    escopos_mensais: dict[AnoMes, list[str]]
    resumo_escopo: pd.DataFrame
    linhas_sem_data: int = 0
    horas_sem_data: float = 0.0
    avisos: list[str] = field(default_factory=list)


def _normalizar(nome) -> str:
    sem_acento = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode()
    return sem_acento.strip().lower()


def _tokens(nome_normalizado: str) -> set[str]:
    return set(re.findall(r"[a-z]+", nome_normalizado))


def ler_planilha(arquivo) -> pd.DataFrame:
    """Lê .xlsx/.xls convertendo falhas em ``ErroPlanilha``."""
    try:
        return pd.read_excel(arquivo)
    except Exception as erro:  # noqa: BLE001 - qualquer falha de leitura vira erro amigável
        raise ErroPlanilha(f"Não foi possível ler a planilha: {erro}") from erro


def detectar_colunas(df: pd.DataFrame) -> dict[str, str | None]:
    """Sugere qual coluna cumpre cada papel (horas, escopo, data, ano, mes)."""
    achadas: dict[str, str | None] = dict.fromkeys(CAMPOS)
    for coluna in df.columns:
        nome = _normalizar(coluna)
        palavras = _tokens(nome)
        mes_ano = {"mes", "ano"} <= palavras

        if achadas["data"] is None and (
            mes_ano or palavras & {"data", "date", "dt", "periodo"}
        ):
            achadas["data"] = coluna
        elif achadas["ano"] is None and palavras & {"ano", "year"}:
            achadas["ano"] = coluna
        elif achadas["mes"] is None and palavras & {"mes", "month"}:
            achadas["mes"] = coluna

        if achadas["horas"] is None and any(k in nome for k in _HORAS_TOTAIS):
            achadas["horas"] = coluna
        if achadas["escopo"] is None and palavras & _ESCOPO:
            achadas["escopo"] = coluna

    if achadas["horas"] is None:
        for coluna in df.columns:
            nome = _normalizar(coluna)
            palavras = _tokens(nome)
            if "horas" in palavras and not (palavras & _EXCLUI_HORAS) and "/" not in nome:
                achadas["horas"] = coluna
                break
    return achadas


def _ano_valido(valor) -> int | None:
    try:
        ano = int(float(str(valor).replace(",", ".")))
    except (ValueError, TypeError):
        return None
    return ano if 2000 <= ano <= 2100 else None


def _serie_horas(df: pd.DataFrame, colunas: dict[str, str | None]) -> pd.Series:
    if colunas["horas"] is not None:
        return limpar_serie_numerica(df[colunas["horas"]])

    nomes = {coluna: _normalizar(coluna) for coluna in df.columns}
    coluna_qtd = next((c for c, n in nomes.items() if _tokens(n) & _QUANTIDADE), None)
    coluna_unit = next((c for c, n in nomes.items() if any(k in n for k in _HORAS_UNITARIAS)), None)
    if coluna_qtd is not None and coluna_unit is not None:
        return limpar_serie_numerica(df[coluna_qtd]) * limpar_serie_numerica(df[coluna_unit])

    raise ErroPlanilha(
        "Não encontrei a coluna de horas (nem 'quantidade' x 'horas por unidade'). "
        f"Colunas disponíveis: {', '.join(map(str, df.columns))}. "
        "Escolha a coluna manualmente."
    )


def processar_planilha(
    df: pd.DataFrame,
    ano_padrao: int,
    mapeamento: dict[str, str | None] | None = None,
) -> ResultadoPlanilha:
    """Converte a planilha em horas demandadas por (ano, mês) e por escopo.

    ``mapeamento`` sobrescreve a detecção automática, ex.: {"horas": "Carga (h)"}.
    """
    df = df.reset_index(drop=True).copy()
    colunas = detectar_colunas(df)
    for campo, coluna in (mapeamento or {}).items():
        if campo not in CAMPOS:
            raise ErroPlanilha(f"Campo de mapeamento desconhecido: {campo!r}")
        if coluna is not None and coluna not in df.columns:
            raise ErroPlanilha(f"Coluna {coluna!r} não existe na planilha.")
        colunas[campo] = coluna

    avisos: list[str] = []
    df["_horas"] = _serie_horas(df, colunas)

    coluna_escopo = colunas["escopo"]
    if coluna_escopo is None:
        textuais = [c for c in df.columns
                    if c != "_horas" and not pd.api.types.is_numeric_dtype(df[c])
                    and not pd.api.types.is_datetime64_any_dtype(df[c])]
        if textuais:
            coluna_escopo = textuais[0]
            avisos.append(f"Coluna de escopo não identificada; usei '{coluna_escopo}'.")
    df["_escopo"] = (
        df[coluna_escopo].fillna("Geral").astype(str).str.strip()
        if coluna_escopo is not None else "Geral"
    )

    resumo = df.groupby("_escopo")["_horas"].sum().reset_index()
    resumo.columns = ["Escopo", "Horas Totais"]
    total = resumo["Horas Totais"].sum()
    resumo["% do Total"] = resumo["Horas Totais"] / total * 100.0 if total > 0 else 0.0
    resumo = resumo.sort_values("Horas Totais", ascending=False).reset_index(drop=True)

    horas_mensais: dict[AnoMes, float] = {}
    escopos_mensais: dict[AnoMes, list[str]] = {}
    linhas_sem_data, horas_sem_data = 0, 0.0

    for _, linha in df.iterrows():
        ano = mes = None
        if colunas["data"] is not None and pd.notna(linha[colunas["data"]]):
            ano, mes = interpretar_data_pt(linha[colunas["data"]])
        if mes is None and colunas["mes"] is not None and pd.notna(linha[colunas["mes"]]):
            _, mes = interpretar_data_pt(linha[colunas["mes"]])
        if colunas["ano"] is not None and pd.notna(linha[colunas["ano"]]):
            ano = _ano_valido(linha[colunas["ano"]]) or ano
        if mes is not None and ano is None:
            ano = ano_padrao

        if ano is None or mes is None:
            linhas_sem_data += 1
            horas_sem_data += float(linha["_horas"])
            continue
        chave = (ano, mes)
        horas_mensais[chave] = horas_mensais.get(chave, 0.0) + float(linha["_horas"])
        lista = escopos_mensais.setdefault(chave, [])
        if linha["_escopo"] not in lista:
            lista.append(linha["_escopo"])

    if not horas_mensais:
        avisos.append(
            "Nenhuma linha tem data/mês identificável; distribuí o total "
            f"igualmente pelos 12 meses de {ano_padrao}."
        )
        escopos = list(resumo["Escopo"])
        for mes in range(1, 13):
            horas_mensais[(ano_padrao, mes)] = float(df["_horas"].sum()) / 12.0
            escopos_mensais[(ano_padrao, mes)] = escopos
    else:
        chaves = sorted(horas_mensais)
        for chave in iterar_meses(chaves[0], chaves[-1]):
            horas_mensais.setdefault(chave, 0.0)
            escopos_mensais.setdefault(chave, [])
        if linhas_sem_data:
            avisos.append(
                f"{linhas_sem_data} linha(s) ({horas_sem_data:,.1f} h) sem data foram "
                "excluídas do plano mensal, mas entram no resumo por escopo."
            )

    return ResultadoPlanilha(
        horas_mensais=horas_mensais,
        escopos_mensais=escopos_mensais,
        resumo_escopo=resumo,
        linhas_sem_data=linhas_sem_data,
        horas_sem_data=horas_sem_data,
        avisos=avisos,
    )