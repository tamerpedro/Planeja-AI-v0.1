# Planeja AI

Aplicacao em Streamlit para consulta assistida de CATMAT/CATSER e precos publicos praticados, usando dados abertos do Compras.gov.br. O objetivo e apoiar pesquisas preliminares de precos e planejamento de compras publicas.

App publicado: https://planeja-ai.streamlit.app/

## O que o app faz

- Busca PDMs de materiais por termo, classe ou grupo.
- Carrega CATMATs relacionados a um PDM informado.
- Extrai caracteristicas textuais das descricoes dos CATMATs para ajudar na filtragem.
- Busca servicos CATSER por termo, classe ou grupo.
- Consulta precos praticados para CATMATs ou CATSERs selecionados.
- Exibe estatisticas basicas dos precos encontrados.
- Permite baixar os resultados em CSV e Excel.

## Fonte dos dados

O app consulta a API publica de dados abertos do Compras.gov.br:

- Base: `https://dadosabertos.compras.gov.br`
- Catalogo de materiais: modulo CATMAT/PDM
- Catalogo de servicos: modulo CATSER
- Pesquisa de precos: modulo de pesquisa de precos

A disponibilidade, formato e completude dos dados dependem da API externa. Falhas temporarias, mudancas de schema, lentidao ou ausencia de resultados podem afetar o comportamento do app.

## Limites metodologicos

Este app e uma ferramenta de apoio. Ele nao substitui analise tecnica, juridica ou metodologica exigida em processos formais de pesquisa de precos.

Pontos importantes:

- Resultados podem misturar itens semelhantes, mas nao necessariamente equivalentes.
- Diferencas de unidade de fornecimento, capacidade, marca, especificacao, regiao, fornecedor e orgao comprador podem distorcer comparacoes.
- Media, mediana e desvio padrao sao indicadores auxiliares, nao validacoes automaticas de preco aceitavel.
- A media pode ser sensivel a outliers; a mediana costuma ser uma referencia mais robusta em bases heterogeneas.
- Quando ha poucos registros, a confiabilidade estatistica e menor.
- O usuario deve revisar descricoes, unidades e contexto da contratacao antes de usar os dados como referencia.

## Como usar

### Consulta assistida por catalogo

1. Escolha entre produto/material (CATMAT) ou servico (CATSER).
2. Busque um PDM ou servico por termo.
3. Selecione o item de catalogo mais adequado.
4. Use os filtros de caracteristicas para refinar os itens candidatos.
5. Selecione um ou mais CATMATs/CATSERs comparaveis.
6. Informe periodo e quantidade maxima de paginas da consulta.
7. Consulte os precos e revise os resultados consolidados.
8. Baixe CSV ou Excel, se necessario.

### Consulta direta por CATMAT

Use esta aba quando ja souber o codigo CATMAT e quiser consultar precos diretamente.

## Como rodar localmente

Requisitos:

- Python 3.12
- `pip`

Instalacao:

```bash
pip install -r requirements.txt
```

Execucao:

```bash
streamlit run app.py
```

Depois, acesse o endereco local exibido pelo Streamlit, normalmente `http://localhost:8501`.

## Deploy no Streamlit Cloud

O repositorio inclui:

- `requirements.txt` com dependencias Python do app.
- `runtime.txt` definindo Python 3.12 para evitar incompatibilidades com runtimes mais novos.

No Streamlit Cloud, o app deve apontar para:

- Branch: `main`
- Arquivo principal: `app.py`

## Estrutura atual

```text
.
├── app.py
├── requirements.txt
└── runtime.txt
```

Atualmente a aplicacao esta concentrada em um unico arquivo (`app.py`). Uma melhoria futura recomendada e separar chamadas de API, regras de negocio, estatisticas e interface em modulos diferentes.

## Melhorias futuras sugeridas

- Adicionar testes automatizados para funcoes de normalizacao, filtros, estatisticas e exportacao.
- Melhorar a busca textual com ranking parcial, sinonimos e tolerancia a erros de digitacao.
- Destacar outliers e tamanho da amostra nos resultados.
- Separar mensagens tecnicas de diagnostico das mensagens destinadas ao usuario final.
- Modularizar o codigo para facilitar manutencao.
- Adicionar exemplos reais de pesquisa para materiais e servicos comuns.

## Aviso

Planeja AI nao e um sistema oficial do Compras.gov.br. Ele apenas consome dados publicos para facilitar consulta e organizacao preliminar de informacoes.
