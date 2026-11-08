"""Simulador de Capacidade Produtiva (interface Streamlit).

Este arquivo só monta a tela. Para rodar:  streamlit run app.py
"""
import datetime
import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from capacidade import (
    REGIME_DOMINGO,
    REGIMES_FERIADO,
    Frota,
    Janelas,
    calcular_capacidade_bruta_periodo,
    distribuir_meta_anual,
    fator_aproveitamento,
    iterar_meses,
)
from planilha import CAMPOS, ErroPlanilha, detectar_colunas, ler_planilha, processar_planilha

MESES_PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
SEM_COLUNA = "(nenhuma)"
ROTULOS_CAMPOS = {
    "horas": "Horas",
    "escopo": "Escopo",
    "data": "Data ou Mês/Ano",
    "ano": "Ano",
    "mes": "Mês",
}

# ==============================================================================
# 1. CONFIGURAÇÃO DA PÁGINA
# ==============================================================================
st.set_page_config(
    page_title="Simulador de Capacidade Produtiva",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    "<style>div[data-testid='stMetricValue']{font-size:1.8rem;font-weight:700;}</style>",
    unsafe_allow_html=True,
)


# ==============================================================================
# 2. FUNÇÕES DE APOIO (cache e formatação)
# ==============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def capacidade_bruta_cacheada(inicio, fim, frota, janelas, considerar_feriados, regime):
    """Evita recalcular (e consultar a API de feriados) a cada interação."""
    return calcular_capacidade_bruta_periodo(
        inicio, fim, frota, janelas,
        considerar_feriados=considerar_feriados, regime_feriado=regime,
    )


@st.cache_data(show_spinner=False)
def planilha_modelo() -> bytes:
    """Planilha de exemplo (dados fictícios) para o usuário preencher."""
    ano = datetime.date.today().year
    modelo = pd.DataFrame({
        "Escopo": ["Inspeção", "Reparo", "Inspeção", "Reparo"],
        "Mês/Ano": [f"jan/{ano}", f"jan/{ano}", f"fev/{ano}", f"fev/{ano}"],
        "Horas_Totais": [120.0, 80.0, 110.0, 95.0],
    })
    buffer = io.BytesIO()
    modelo.to_excel(buffer, index=False, sheet_name="Plano")
    return buffer.getvalue()


def format_h(valor: float) -> str:
    return f"{valor:,.1f} h".replace(",", "X").replace(".", ",").replace("X", ".")


def rotulo_mes(ano_mes: tuple[int, int]) -> str:
    return f"{MESES_PT[ano_mes[1]]}/{ano_mes[0]}"


# ==============================================================================
# 3. BARRA LATERAL
# ==============================================================================
st.sidebar.title("Parâmetros de Entrada")

with st.sidebar.expander("Equipes por Turno", expanded=True):
    frota = Frota(
        seg_diurno=st.number_input("Segunda (Diurno)", min_value=0, value=1, step=1),
        ter_qui_diurno=st.number_input("Terça a Quinta (Diurno)", min_value=0, value=1, step=1),
        sex_diurno=st.number_input("Sexta (Diurno)", min_value=0, value=1, step=1),
        fds_diurno=st.number_input(
            "Sábado e Domingo (Diurno)", min_value=0, value=2, step=1,
            help="Também usado nos feriados (veja Configuração de Feriados).",
        ),
        seg_sex_noturno=st.number_input("Segunda a Sexta (Noturno)", min_value=0, value=2, step=1),
        sab_dom_noturno=st.number_input("Sábado e Domingo (Noturno)", min_value=0, value=2, step=1),
    )

with st.sidebar.expander("Janela de Manutenção (Horas Brutas)", expanded=False):
    desconto_janela = st.number_input(
        "Desconto por Janela (h)", min_value=0.0, value=0.0, step=0.5,
        help="Horas descontadas de cada janela (ex.: tempo de preparação e liberação).",
    )
    janelas = Janelas(
        seg_sex_diurno=st.number_input("Seg a Sex Diurno (h)", min_value=0.0, value=5.0, step=0.5),
        fds_diurno=st.number_input("Sáb e Dom Diurno (h)", min_value=0.0, value=10.0, step=0.5),
        seg_sex_noturno=st.number_input("Seg a Sex Noturno (h)", min_value=0.0, value=5.0, step=0.5),
        sab_noturno=st.number_input("Sábado Noturno (h)", min_value=0.0, value=6.0, step=0.5),
        dom_noturno=st.number_input("Domingo Noturno (h)", min_value=0.0, value=5.0, step=0.5),
    ).com_desconto(desconto_janela)

with st.sidebar.expander("Configuração de Feriados", expanded=True):
    considerar_feriados = st.checkbox(
        "Considerar feriados no cálculo da capacidade", value=True,
        help="Se desmarcado, feriados são tratados como dias normais da semana.",
    )
    regime_feriado = st.selectbox(
        "Regime para feriados", options=REGIMES_FERIADO,
        index=REGIMES_FERIADO.index(REGIME_DOMINGO), disabled=not considerar_feriados,
        help=(
            "Turno noturno: perfil de sábado, domingo ou dia útil. "
            "Turno diurno: feriado usa o perfil de fim de semana, exceto feriado no "
            "domingo com o regime Dia Útil, que usa o perfil de terça a quinta."
        ),
    )

with st.sidebar.expander("Percentual de Perdas (%)", expanded=False):
    perdas = [
        st.number_input("Avaria de equipamento (%)", min_value=0.0, max_value=100.0, value=3.0, step=0.5),
        st.number_input("Ocorrências (%)", min_value=0.0, max_value=100.0, value=10.0, step=0.5),
        st.number_input("Outros (%)", min_value=0.0, max_value=100.0, value=2.0, step=0.5),
    ]
    modo_perdas = st.radio(
        "Combinação das perdas", options=["soma", "composto"], horizontal=True,
        help="Soma: 3% + 10% + 2% = 15%. Composto: cada perda incide sobre o que sobrou.",
    )
fator = fator_aproveitamento(perdas, modo=modo_perdas)
total_perdas_pct = 1.0 - fator

ano_atual = datetime.date.today().year
plano_mensal = distribuir_meta_anual(0.0, ano_atual)
escopos_mensais: dict[tuple[int, int], list[str]] = {}
resumo_escopo = None
df_planilha = None

with st.sidebar.expander("Meta e Importação de Planilha", expanded=True):
    meta_manual = st.number_input("Meta Padrão Anual (h/ano)", min_value=0.0, value=3000.0, step=10.0)
    proporcional_dias = st.checkbox(
        "Distribuir a meta proporcionalmente aos dias do mês", value=True,
        help="Se desmarcado, divide a meta igualmente por 12.",
    )
    arquivo = st.file_uploader("Carregar Planilha Excel (.xlsx, .xls)", type=["xlsx", "xls"])
    st.download_button(
        "Baixar planilha-modelo", data=planilha_modelo(), file_name="modelo_plano.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    if arquivo is not None:
        try:
            df_planilha = ler_planilha(arquivo)
        except ErroPlanilha as erro:
            st.error(str(erro))

plano_mensal = distribuir_meta_anual(meta_manual, ano_atual, proporcional_dias)

if df_planilha is not None:
    # Expanders não podem ser aninhados no Streamlit: o mapeamento fica em um expander próprio.
    with st.sidebar.expander("Conferir colunas da planilha", expanded=False):
        detectadas = detectar_colunas(df_planilha)
        originais = {str(c): c for c in df_planilha.columns}
        opcoes = [SEM_COLUNA] + list(originais)
        mapeamento = {}
        for campo in CAMPOS:
            padrao = str(detectadas[campo]) if detectadas[campo] is not None else SEM_COLUNA
            escolha = st.selectbox(
                ROTULOS_CAMPOS[campo], opcoes, index=opcoes.index(padrao), key=f"coluna_{campo}",
            )
            mapeamento[campo] = originais.get(escolha)  # SEM_COLUNA não está em "originais" -> None
    try:
        resultado = processar_planilha(df_planilha, ano_atual, mapeamento)
        plano_mensal = resultado.horas_mensais
        escopos_mensais = resultado.escopos_mensais
        resumo_escopo = resultado.resumo_escopo
        for aviso in resultado.avisos:
            st.sidebar.warning(aviso)
    except ErroPlanilha as erro:
        st.sidebar.error(str(erro))
        st.sidebar.caption("Usando a meta padrão no lugar da planilha.")

meses_plano = sorted(plano_mensal)
plano_inicio, plano_fim = meses_plano[0], meses_plano[-1]
ultimo_dia = (datetime.date(plano_fim[0] + (plano_fim[1] == 12), plano_fim[1] % 12 + 1, 1)
              - datetime.timedelta(days=1))

with st.sidebar.expander("Período de Análise", expanded=True):
    intervalo = st.date_input(
        "Selecione o Intervalo:",
        value=(datetime.date(plano_inicio[0], plano_inicio[1], 1), ultimo_dia),
        format="DD/MM/YYYY",
        key=f"periodo_{plano_inicio}_{plano_fim}",  # muda o padrão quando o plano muda
        help="Os cálculos cobrem a interseção entre este intervalo e o período do plano.",
    )

if not isinstance(intervalo, (tuple, list)) or len(intervalo) != 2:
    st.info("Selecione a data inicial e a data final do período.")
    st.stop()

# ==============================================================================
# 4. CÁLCULOS
# ==============================================================================
inicio = max(plano_inicio, (intervalo[0].year, intervalo[0].month))
fim = min(plano_fim, (intervalo[1].year, intervalo[1].month))
periodo = list(iterar_meses(inicio, fim))
if not periodo:
    st.warning(
        f"O intervalo escolhido não coincide com o período do plano "
        f"({rotulo_mes(plano_inicio)} a {rotulo_mes(plano_fim)}). Ajuste o filtro de período."
    )
    st.stop()

capacidade_bruta = capacidade_bruta_cacheada(
    inicio, fim, frota, janelas, considerar_feriados, regime_feriado,
)
capacidade_efetiva = {ym: valor * fator for ym, valor in capacidade_bruta.items()}

bruta_total = sum(capacidade_bruta[ym] for ym in periodo)
efetiva_total = sum(capacidade_efetiva[ym] for ym in periodo)
meta_total = sum(plano_mensal.get(ym, 0.0) for ym in periodo)
saldo_total = efetiva_total - meta_total
atingimento = efetiva_total / meta_total * 100.0 if meta_total > 0 else 0.0

saldo_mensal = {ym: capacidade_efetiva[ym] - plano_mensal.get(ym, 0.0) for ym in periodo}
saldo_acumulado, acumulado = [], 0.0
for ym in periodo:
    acumulado += saldo_mensal[ym]
    saldo_acumulado.append(acumulado)
meses_em_deficit = [rotulo_mes(ym) for ym in periodo if saldo_mensal[ym] < 0]

# ==============================================================================
# 5. INTERFACE PRINCIPAL
# ==============================================================================
st.title("Simulação de Capacidade Produtiva")
st.caption(
    f"Período de análise: **{rotulo_mes(inicio)}** até **{rotulo_mes(fim)}** ({len(periodo)} meses)"
)

c1, c2, c3 = st.columns(3)
c1.metric("Capacidade Bruta (Total Período)", format_h(bruta_total),
          help="Volume teórico de horas disponíveis no período.")
c2.metric("Capacidade Efetiva (Total Período)", format_h(efetiva_total),
          delta=f"Perdas totais: {total_perdas_pct * 100:.2f}%", delta_color="inverse",
          help="Horas disponíveis após o desconto das perdas.")
c3.metric("Meta do Plano (Total Período)", format_h(meta_total),
          help="Demanda total de horas exigida pelo plano no período.")

st.divider()
st.subheader("Atingimento da Meta do Plano (Período)")
st.progress(min(1.0, max(0.0, efetiva_total / meta_total)) if meta_total > 0 else 0.0)

if saldo_total >= 0:
    st.success(
        f"**SUPERÁVIT DE CAPACIDADE NO PERÍODO**\n\n"
        f"• **Atingimento global:** {atingimento:.2f}%\n\n"
        f"• **Saldo:** a capacidade efetiva supera a meta em **+{format_h(saldo_total)}**."
    )
else:
    st.error(
        f"**DÉFICIT DE CAPACIDADE NO PERÍODO**\n\n"
        f"• **Atingimento global:** {atingimento:.2f}%\n\n"
        f"• **Horas faltantes:** faltam **{format_h(abs(saldo_total))}** de capacidade efetiva para cobrir o plano."
    )
if meses_em_deficit and saldo_total >= 0:
    st.info("Mesmo com superávit total, há meses em déficit: " + ", ".join(meses_em_deficit) + ".")
elif meses_em_deficit:
    st.caption(f"Meses em déficit ({len(meses_em_deficit)} de {len(periodo)}): " + ", ".join(meses_em_deficit))

st.divider()

# ------------------------------------------------------------------ gráfico
st.subheader("Capacidade Efetiva vs. Meta do Plano")
rotulos = [rotulo_mes(ym) for ym in periodo]
valores_efetiva = [capacidade_efetiva[ym] for ym in periodo]
valores_meta = [plano_mensal.get(ym, 0.0) for ym in periodo]
mostrar_texto = len(periodo) <= 12

fig = go.Figure()
fig.add_bar(
    x=rotulos, y=valores_efetiva, name="Capacidade efetiva (h)", marker_color="#2F6DB5",
    text=[f"{v:,.0f}" for v in valores_efetiva] if mostrar_texto else None, textposition="outside",
    hovertemplate="<b>%{x}</b><br>Capacidade efetiva: %{y:,.1f} h<extra></extra>",
)
fig.add_bar(
    x=rotulos, y=valores_meta, name="Meta do plano (h)", marker_color="#2E8B57",
    text=[f"{v:,.0f}" for v in valores_meta] if mostrar_texto else None, textposition="outside",
    hovertemplate="<b>%{x}</b><br>Meta do plano: %{y:,.1f} h<extra></extra>",
)
fig.add_scatter(
    x=rotulos, y=saldo_acumulado, name="Saldo acumulado (h)", mode="lines+markers",
    yaxis="y2", line=dict(color="#E0A030", width=3),
    hovertemplate="<b>%{x}</b><br>Saldo acumulado: %{y:,.1f} h<extra></extra>",
)
fig.update_layout(
    barmode="group", bargap=0.25, bargroupgap=0.1, height=500,
    margin=dict(l=20, r=20, t=50, b=40),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    xaxis=dict(title="Mês / Ano", showgrid=False, tickangle=0 if len(rotulos) <= 13 else -45),
    yaxis=dict(title="Horas por mês", gridcolor="rgba(128,128,128,0.25)"),
    yaxis2=dict(title="Saldo acumulado (h)", overlaying="y", side="right", showgrid=False),
)
st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------------------------------ tabela
tem_escopo = resumo_escopo is not None and not resumo_escopo.empty
linhas = []
total = {"Período": "TOTAL PERÍODO"}
if tem_escopo:
    total["Escopo"] = ", ".join(resumo_escopo["Escopo"].astype(str))
total.update({
    "Capacidade Bruta (h)": bruta_total, "Capacidade Efetiva (h)": efetiva_total,
    "Meta do Plano (h)": meta_total, "Saldo (h)": saldo_total,
})
linhas.append(total)
for ym in periodo:
    linha = {"Período": rotulo_mes(ym)}
    if tem_escopo:
        linha["Escopo"] = ", ".join(escopos_mensais.get(ym, [])) or "—"
    linha.update({
        "Capacidade Bruta (h)": capacidade_bruta[ym],
        "Capacidade Efetiva (h)": capacidade_efetiva[ym],
        "Meta do Plano (h)": plano_mensal.get(ym, 0.0),
        "Saldo (h)": saldo_mensal[ym],
    })
    linhas.append(linha)
df_detalhes = pd.DataFrame(linhas)

with st.expander("Tabela de Detalhamento por Período"):
    st.dataframe(
        df_detalhes.style.format({
            "Capacidade Bruta (h)": "{:,.1f}", "Capacidade Efetiva (h)": "{:,.1f}",
            "Meta do Plano (h)": "{:,.1f}", "Saldo (h)": "{:,.1f}",
        }),
        use_container_width=True, hide_index=True,
    )
    buffer = io.BytesIO()
    df_detalhes.to_excel(buffer, index=False, sheet_name="Capacidade")
    st.download_button(
        "Baixar tabela em Excel", data=buffer.getvalue(), file_name="capacidade_vs_meta.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ------------------------------------------------------------------ escopo
if tem_escopo:
    st.divider()
    st.subheader("Resumo do Plano por Escopo")
    col_pizza, col_tabela = st.columns(2)
    with col_pizza:
        fig_escopo = go.Figure(go.Pie(
            labels=resumo_escopo["Escopo"], values=resumo_escopo["Horas Totais"], hole=0.4,
            textinfo="label+percent",
            hovertemplate="<b>%{label}</b><br>Horas: %{value:,.1f} h<br>Participação: %{percent}<extra></extra>",
        ))
        fig_escopo.update_layout(
            title="Distribuição Percentual de Horas por Escopo", height=380,
            margin=dict(l=20, r=20, t=40, b=20),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_escopo, use_container_width=True)
    with col_tabela:
        st.markdown("##### Detalhamento Analítico por Escopo")
        st.dataframe(
            resumo_escopo.style.format({"Horas Totais": "{:,.1f} h", "% do Total": "{:.1f}%"}),
            use_container_width=True, hide_index=True,
        )