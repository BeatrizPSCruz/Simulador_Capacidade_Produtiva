# Simulador de Capacidade Produtiva

Aplicação web que responde, em poucos cliques, a uma pergunta recorrente do planejamento de manutenção:
**"com a frota e as janelas de manutenção que temos, conseguimos cobrir as horas que o plano exige?"**

[Visão geral](docs/visao_geral.png)

**Demo:** _(link do Streamlit Cloud após o deploy)_

## O problema

Esse tipo de simulação era feito em uma planilha Excel com muitas abas e fórmulas encadeadas.
Cada simulação levava **cerca de 1 a 2 horas** e dependia de quem conhecia a planilha: era preciso saber
em quais células mexer, em que ordem, e o que cada resultado significava. Para trocar de cenário
(mais uma equipe, uma janela menor, outro regime de feriado), era necessário refazer o processo.

## A solução

Uma interface em que o usuário só informa o que conhece da operação (frota, janelas, perdas, plano)
e vê o resultado imediatamente.

| Antes (planilha) | Agora (aplicação web) |
|---|---|
| ~1 a 2 h por simulação | Resultado atualizado a cada alteração de parâmetro |
| Exige treinamento e conhecimento das fórmulas | Parâmetros rotulados, com explicação em cada campo |
| Regras de feriado embutidas nas fórmulas | Regime de feriado selecionável e documentado |
| Estrutura fixa de uma única operação | Qualquer coordenação: basta mudar os parâmetros ou a planilha de plano |
| Plano digitado dentro do modelo | Upload da planilha, com conferência das colunas detectadas |
| Resultado só numérico | Gráfico mensal, saldo acumulado e alerta dos meses em déficit |

O ganho principal não é só o tempo: é **simplificar e generalizar** o processo, de modo que qualquer
pessoa da equipe consiga simular cenários sem treinamento sobre a planilha.

## Como usar (3 passos)

1. **Informe headcount e as janelas** na barra lateral (equipes por turno e horas de janela por perfil de dia).
2. **Informe o plano:** digite uma meta anual ou carregue a planilha com as horas demandadas (há um modelo para baixar).
3. **Leia o resultado:** capacidade bruta e efetiva, saldo contra a meta, gráfico mensal e meses em déficit.
   A tabela de detalhamento pode ser exportada para Excel.

## Como o cálculo funciona

```
capacidade bruta do dia = Σ por turno (equipes × janela efetiva)
janela efetiva          = janela informada − desconto por janela (mínimo 0)
capacidade efetiva      = capacidade bruta × fator de aproveitamento
saldo                   = capacidade efetiva − horas exigidas pelo plano
```

**Perdas** (avarias, ocorrências, outros) podem ser combinadas por *soma* (2% + 3% + 10% + 2% = 17%)
ou de forma *composta* (cada perda incide sobre o que sobrou).

**Meta mensal:** a meta anual é distribuída proporcionalmente aos dias de cada mês (fevereiro recebe menos que janeiro).
Opcionalmente, divide-se igualmente por 12.

### Regras de feriado

Os feriados nacionais vêm da [BrasilAPI](https://brasilapi.com.br). Se a API estiver indisponível, o app usa um
cálculo local (feriados fixos + Carnaval, Sexta-feira Santa e Corpus Christi a partir da data da Páscoa).

## Formato da planilha de plano

Uma linha por demanda, com pelo menos a coluna de **horas**. As demais são opcionais:

| Coluna | Exemplos de nome aceitos |
|---|---|
| Horas | `Horas_Totais`, `Horas_Necessarias`, `Total_Horas` (ou `Quantidade` × `Horas_por_Unidade`) |
| Escopo | `Escopo`, `Projeto`, `Serviço`, `Atividade`, `Tipo` |
| Período | `Mês/Ano` (ex.: `jan/26`) ou `Data`; ou colunas separadas `Mês` e `Ano` |

O app mostra quais colunas detectou e permite corrigir manualmente. Linhas sem data são sinalizadas
(ficam fora do plano mensal, mas entram no resumo por escopo). Baixe a planilha-modelo dentro do app.

## Executar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

Requer Python 3.10 ou superior.

A lógica de negócio (`capacidade.py`, `planilha.py`) não depende do Streamlit e tem cobertura de testes
determinísticos (sem acesso à internet): cálculo de Páscoa e feriados, regras por perfil, capacidade mensal,
perdas, distribuição da meta, interpretação de datas em português e leitura da planilha.

## Estrutura

```
app.py            interface (Streamlit)
capacidade.py     regras de capacidade, feriados, perdas e meta
planilha.py       leitura e normalização da planilha de plano
tests/            testes automatizados
```

## Premissas e limitações

- O modelo compara **capacidade e demanda por mês**; não faz a programação detalhada de cada ordem de serviço.
- Headcount e janelas são tratadas por **perfil de dia/turno**, sem exceções pontuais (por exemplo, uma parada especial).
- As perdas são percentuais globais, iguais para todos os turnos.
- Feriados considerados: apenas os **nacionais**.
