"""Núcleo de cálculo do Simulador de Capacidade Produtiva.

Este módulo não depende do Streamlit: pode ser testado de forma isolada.

Premissas do modelo
-------------------
1. Capacidade bruta diária = soma, por turno, de (headcount x janela efetiva em horas).
2. Janela efetiva = janela informada - desconto fixo por janela (nunca negativa).
3. Capacidade efetiva = capacidade bruta x fator de aproveitamento (perdas).
4. Feriados nacionais mudam o perfil de frota/janela aplicado ao dia
   (tabelas em ``perfil_diurno`` e ``perfil_noturno``).

Feriados
--------
Os feriados vêm da BrasilAPI. Se a API falhar, usa-se um cálculo local
(feriados fixos + Carnaval, Sexta-feira Santa e Corpus Christi a partir da Páscoa).
Respostas da API ficam em cache; após uma falha, a API não é consultada
novamente por alguns minutos, para não travar a interface a cada interação.
"""
from __future__ import annotations

import calendar
import datetime
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass

import pandas as pd
import requests

AnoMes = tuple[int, int]

REGIME_DOMINGO = "Domingo"
REGIME_SABADO = "Sábado"
REGIME_DIA_UTIL = "Dia Útil (DU)"
REGIMES_FERIADO = (REGIME_DOMINGO, REGIME_SABADO, REGIME_DIA_UTIL)

MESES_PT = {
    "jan": 1, "janeiro": 1, "fev": 2, "fevereiro": 2,
    "mar": 3, "marco": 3, "março": 3, "abr": 4, "abril": 4,
    "mai": 5, "maio": 5, "jun": 6, "junho": 6,
    "jul": 7, "julho": 7, "ago": 8, "agosto": 8,
    "set": 9, "setembro": 9, "out": 10, "outubro": 10,
    "nov": 11, "novembro": 11, "dez": 12, "dezembro": 12,
}


# ==============================================================================
# PARÂMETROS
# ==============================================================================
@dataclass(frozen=True)
class Frota:
    """Quantidade de equipes disponíveis por perfil de dia/turno."""

    seg_diurno: int
    ter_qui_diurno: int
    sex_diurno: int
    fds_diurno: int
    seg_sex_noturno: int
    sab_dom_noturno: int

    def __post_init__(self) -> None:
        for campo, valor in asdict(self).items():
            if valor < 0:
                raise ValueError(f"Frota.{campo} não pode ser negativa: {valor}")


@dataclass(frozen=True)
class Janelas:
    """Janelas de manutenção (horas) por perfil de dia/turno."""

    seg_sex_diurno: float
    fds_diurno: float
    seg_sex_noturno: float
    sab_noturno: float
    dom_noturno: float

    def __post_init__(self) -> None:
        for campo, valor in asdict(self).items():
            if valor < 0:
                raise ValueError(f"Janelas.{campo} não pode ser negativa: {valor}")

    def com_desconto(self, desconto_h: float) -> Janelas:
        """Devolve novas janelas com o desconto aplicado a cada uma."""
        return Janelas(**{
            campo: calcular_janela_efetiva(valor, desconto_h)
            for campo, valor in asdict(self).items()
        })


# ==============================================================================
# FUNÇÕES AUXILIARES
# ==============================================================================
def calcular_janela_efetiva(janela_informada: float, desconto: float) -> float:
    """Janela menos o desconto, com piso em zero."""
    return max(0.0, float(janela_informada) - float(desconto))


def limpar_serie_numerica(serie: pd.Series | None) -> pd.Series:
    """Converte textos numéricos (inclusive no formato brasileiro) para float.

    Aceita "1500", "1500,5" e "1.234,56". Valores inválidos ou vazios viram 0.
    """
    if serie is None:
        return pd.Series(dtype=float)
    texto = serie.astype(str).str.strip()
    # Pontos que antecedem uma vírgula são separadores de milhar: "1.234,56".
    texto = texto.str.replace(r"\.(?=[\d.]*,)", "", regex=True)
    texto = texto.str.replace(",", ".", regex=False)
    return pd.to_numeric(texto, errors="coerce").fillna(0.0)


def iterar_meses(inicio: AnoMes, fim: AnoMes) -> Iterator[AnoMes]:
    """Gera (ano, mês) de ``inicio`` a ``fim`` inclusive. Vazio se fim < inicio."""
    ano, mes = inicio
    while (ano, mes) <= fim:
        yield ano, mes
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1


def interpretar_data_pt(valor) -> tuple[int | None, int | None]:
    """Extrai (ano, mês) de datas, meses por extenso ou números em português.

    O ano pode vir como ``None`` quando só o mês é informado.
    """
    if valor is None or pd.isna(valor):
        return None, None
    if isinstance(valor, (datetime.datetime, datetime.date, pd.Timestamp)):
        return valor.year, valor.month
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        mes = int(valor)
        if 1 <= mes <= 12:
            return None, mes

    texto = str(valor).strip().lower()
    if texto.isdigit() and 1 <= int(texto) <= 12:
        return None, int(texto)
    if texto in MESES_PT:
        return None, MESES_PT[texto]

    # 2026-03 ou 2026-03-15
    iso = re.match(r"^(\d{4})-(\d{2})(?:-\d{2})?", texto)
    if iso and 1 <= int(iso.group(2)) <= 12:
        return int(iso.group(1)), int(iso.group(2))

    # 15/03/2026 (dia/mês/ano)
    completa = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", texto)
    if completa:
        mes, ano = int(completa.group(2)), int(completa.group(3))
        if ano < 100:
            ano += 2000
        if 1 <= mes <= 12:
            return ano, mes

    # jan/26, março-2026, "jan 26"
    nome_ano = re.search(r"([a-zçã]{3,9})[/\-\s]?(\d{2,4})", texto)
    if nome_ano and nome_ano.group(1) in MESES_PT:
        ano = int(nome_ano.group(2))
        if ano < 100:
            ano += 2000
        return ano, MESES_PT[nome_ano.group(1)]

    # 03/2026, 3-26
    mes_ano = re.match(r"^(\d{1,2})[/\-](\d{2,4})$", texto)
    if mes_ano:
        mes, ano = int(mes_ano.group(1)), int(mes_ano.group(2))
        if ano < 100:
            ano += 2000
        if 1 <= mes <= 12:
            return ano, mes

    try:
        convertida = pd.to_datetime(texto, errors="coerce", dayfirst=True)
        if pd.notna(convertida) and convertida.year >= 2000:
            return convertida.year, convertida.month
    except (ValueError, TypeError, OverflowError):
        pass
    return None, None


# ==============================================================================
# FERIADOS
# ==============================================================================
_URL_FERIADOS = "https://brasilapi.com.br/api/feriados/v1/{ano}"
_PAUSA_APOS_FALHA_S = 600.0
_cache_api: dict[int, frozenset[str]] = {}
_api_indisponivel_ate: float = float("-inf")


def limpar_cache_feriados() -> None:
    """Zera o cache e a pausa da API (útil em testes)."""
    global _api_indisponivel_ate
    _cache_api.clear()
    _api_indisponivel_ate = float("-inf")


def obter_data_pascoa(ano: int) -> datetime.date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    deslocamento = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * deslocamento) // 451
    mes = (h + deslocamento - 7 * m + 114) // 31
    dia = ((h + deslocamento - 7 * m + 114) % 31) + 1
    return datetime.date(ano, mes, dia)


def feriados_fallback(ano: int) -> set[str]:
    """Feriados nacionais calculados localmente (sem internet)."""
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:  # Consciência Negra virou feriado nacional em 2024
        fixos.append((11, 20))
    feriados = {datetime.date(ano, mes, dia).isoformat() for mes, dia in fixos}

    pascoa = obter_data_pascoa(ano)
    for deslocamento in (-47, -2, 60):  # Carnaval, Sexta-feira Santa, Corpus Christi
        feriados.add((pascoa + datetime.timedelta(days=deslocamento)).isoformat())
    return feriados


def _buscar_feriados_api(ano: int, timeout: float) -> frozenset[str] | None:
    global _api_indisponivel_ate
    if time.monotonic() < _api_indisponivel_ate:
        return None
    try:
        resposta = requests.get(_URL_FERIADOS.format(ano=ano), timeout=timeout)
        if resposta.status_code == 200:
            return frozenset(item["date"] for item in resposta.json())
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    _api_indisponivel_ate = time.monotonic() + _PAUSA_APOS_FALHA_S
    return None


def obter_feriados_nacionais(
    ano: int, usar_api: bool = True, timeout: float = 4.0
) -> set[str]:
    """Feriados nacionais do ano como textos 'AAAA-MM-DD'.

    Com ``usar_api=False`` usa apenas o cálculo local (determinístico).
    """
    if usar_api:
        if ano not in _cache_api:
            resultado = _buscar_feriados_api(ano, timeout)
            if resultado is not None:
                _cache_api[ano] = resultado
        if ano in _cache_api:
            return set(_cache_api[ano])
    return feriados_fallback(ano)


# ==============================================================================
# REGRAS DE PERFIL (TABELAS DE DECISÃO)
# ==============================================================================
def perfil_diurno(
    dia_semana: int, eh_feriado: bool, regime: str, frota: Frota, janelas: Janelas
) -> tuple[int, float]:
    """(veículos, janela em h) do turno diurno.

    dia_semana: 0=segunda ... 6=domingo.

    | Dia                                   | Perfil aplicado      |
    |---------------------------------------|----------------------|
    | Feriado em dia de semana              | fim de semana        |
    | Feriado no sábado                     | fim de semana        |
    | Feriado no domingo, regime Dia Útil   | terça a quinta       |
    | Feriado no domingo, outros regimes    | fim de semana        |
    | Sábado / domingo                      | fim de semana        |
    | Segunda / sexta                       | próprio de cada dia  |
    | Terça a quinta                        | terça a quinta       |

    """
    fim_de_semana = (frota.fds_diurno, janelas.fds_diurno)
    dia_util = (frota.ter_qui_diurno, janelas.seg_sex_diurno)
    if eh_feriado:
        if dia_semana == 6 and regime == REGIME_DIA_UTIL:
            return dia_util
        return fim_de_semana
    if dia_semana in (5, 6):
        return fim_de_semana
    if dia_semana == 0:
        return frota.seg_diurno, janelas.seg_sex_diurno
    if dia_semana == 4:
        return frota.sex_diurno, janelas.seg_sex_diurno
    return dia_util


def perfil_noturno(
    dia_semana: int, eh_feriado: bool, regime: str, frota: Frota, janelas: Janelas
) -> tuple[int, float]:
    """(equipes, janela em h) do turno noturno.

    | Dia                     | Perfil aplicado                      |
    |-------------------------|--------------------------------------|
    | Feriado, regime Sábado  | sábado                               |
    | Feriado, regime Domingo | domingo                              |
    | Feriado, regime DU      | segunda a sexta                      |
    | Sábado                  | sábado                               |
    | Domingo                 | domingo                              |
    | Demais dias             | segunda a sexta                      |
    """
    sabado = (frota.sab_dom_noturno, janelas.sab_noturno)
    domingo = (frota.sab_dom_noturno, janelas.dom_noturno)
    dia_util = (frota.seg_sex_noturno, janelas.seg_sex_noturno)
    if eh_feriado:
        return {REGIME_SABADO: sabado, REGIME_DOMINGO: domingo}.get(regime, dia_util)
    if dia_semana == 5:
        return sabado
    if dia_semana == 6:
        return domingo
    return dia_util


# ==============================================================================
# CAPACIDADE
# ==============================================================================
def calcular_capacidade_bruta_periodo(
    inicio: AnoMes,
    fim: AnoMes,
    frota: Frota,
    janelas: Janelas,
    considerar_feriados: bool = True,
    regime_feriado: str = REGIME_DOMINGO,
    usar_api_feriados: bool = True,
) -> dict[AnoMes, float]:
    """Capacidade bruta (h) por mês entre ``inicio`` e ``fim`` inclusive.

    ``janelas`` deve ser a janela já descontada (veja ``Janelas.com_desconto``).
    """
    if regime_feriado not in REGIMES_FERIADO:
        raise ValueError(f"Regime inválido: {regime_feriado!r}. Use um de {REGIMES_FERIADO}")

    capacidade = dict.fromkeys(iterar_meses(inicio, fim), 0.0)
    if not capacidade:
        return capacidade

    feriados_por_ano = (
        {ano: obter_feriados_nacionais(ano, usar_api_feriados)
         for ano in range(inicio[0], fim[0] + 1)}
        if considerar_feriados else {}
    )

    dia = datetime.date(inicio[0], inicio[1], 1)
    ultimo = datetime.date(fim[0], fim[1], calendar.monthrange(*fim)[1])
    while dia <= ultimo:
        eh_feriado = dia.isoformat() in feriados_por_ano.get(dia.year, ())
        dia_semana = dia.weekday()
        v_d, j_d = perfil_diurno(dia_semana, eh_feriado, regime_feriado, frota, janelas)
        v_n, j_n = perfil_noturno(dia_semana, eh_feriado, regime_feriado, frota, janelas)
        capacidade[(dia.year, dia.month)] += v_d * j_d + v_n * j_n
        dia += datetime.timedelta(days=1)
    return capacidade


def fator_aproveitamento(percentuais_perda: Sequence[float], modo: str = "soma") -> float:
    """Fração da capacidade bruta que sobra após as perdas (0 a 1).

    modo="soma":     perdas somadas (ex.: 2% + 3% + 10% + 2% = 17% -> 0,83).
    modo="composto": perdas aplicadas em sequência (0,98 x 0,97 x 0,90 x 0,98).
    Nunca devolve valor negativo.
    """
    if any(p < 0 or p > 100 for p in percentuais_perda):
        raise ValueError("Cada percentual de perda deve estar entre 0 e 100.")
    if modo == "soma":
        return max(0.0, 1.0 - sum(percentuais_perda) / 100.0)
    if modo == "composto":
        fator = 1.0
        for perda in percentuais_perda:
            fator *= 1.0 - perda / 100.0
        return fator
    raise ValueError(f"Modo inválido: {modo!r}. Use 'soma' ou 'composto'.")


def distribuir_meta_anual(
    meta_anual: float, ano: int, proporcional_aos_dias: bool = True
) -> dict[AnoMes, float]:
    """Divide a meta anual (h) pelos 12 meses.

    Proporcional aos dias: fevereiro recebe menos que janeiro, o que evita
    distorcer o saldo mensal. Sem proporcionalidade, divide por 12.
    """
    dias_ano = 366 if calendar.isleap(ano) else 365
    meta = {}
    for mes in range(1, 13):
        if proporcional_aos_dias:
            meta[(ano, mes)] = meta_anual * calendar.monthrange(ano, mes)[1] / dias_ano
        else:
            meta[(ano, mes)] = meta_anual / 12.0
    return meta