# Teste de sincronização GitHub: comentário temporário no cabeçalho do app.
import re
import time
import unicodedata
from datetime import date

import pandas as pd
import requests
import streamlit as st


# ============================================================
# CONFIGURAÇÃO GERAL
# ============================================================

st.set_page_config(
    page_title="PlanejaIA",
    layout="wide"
)

BASE_URL = "https://dadosabertos.compras.gov.br"

st.title("PlanejaIA - Protótipo inicial")
st.write(
    "Da descrição de itens até CATMAT, preços públicos e minuta semiestruturada de DOD Dataprev."
)

st.divider()


# ============================================================
# FUNÇÕES UTILITÁRIAS
# ============================================================

def normalizar_texto(texto):
    if texto is None or pd.isna(texto):
        return ""

    texto = str(texto).upper().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


def formatar_brl(valor):
    if valor is None or pd.isna(valor):
        return "N/A"

    return (
        f"R$ {valor:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def primeira_coluna_existente(df, candidatas):
    if df is None or df.empty:
        return None

    for coluna in candidatas:
        if coluna in df.columns:
            return coluna

    return None


def limpar_session_state_precos():
    for chave in [
        "resultados_precos",
        "erros_precos",
        "urls_precos",
        "df_precos",
        "codigos_catmat_consultados",
        "data_inicial_precos_final",
        "data_final_precos_final"
    ]:
        st.session_state.pop(chave, None)


def limpar_session_state_catmat():
    for chave in [
        "df_itens_carac",
        "erros_itens",
        "total_itens",
        "paginas_itens",
        "urls_itens",
        "diagnostico_itens",
        "codigo_pdm_atual",
        "nome_pdm_atual"
    ]:
        st.session_state.pop(chave, None)

    limpar_session_state_precos()


# ============================================================
# FUNÇÕES DE API
# ============================================================

def consultar_endpoint(endpoint, params=None, timeout=120):
    if params is None:
        params = {}

    response = requests.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        headers={"accept": "application/json"},
        timeout=timeout
    )

    return response


def consultar_paginas(
    endpoint,
    params_base,
    tamanho_pagina=100,
    max_paginas=10,
    timeout=120,
    tentativas_por_pagina=3,
    pular_pagina_com_erro=True
):
    resultados = []
    erros = []
    urls_consultadas = []
    total_registros = None
    total_paginas = None

    for pagina in range(1, int(max_paginas) + 1):
        params = dict(params_base)
        params["pagina"] = pagina
        params["tamanhoPagina"] = int(tamanho_pagina)

        sucesso_pagina = False
        erro_atual = None

        for tentativa in range(1, int(tentativas_por_pagina) + 1):
            try:
                response = consultar_endpoint(
                    endpoint=endpoint,
                    params=params,
                    timeout=timeout
                )

                urls_consultadas.append(response.url)

                if response.status_code == 200:
                    try:
                        data = response.json()
                    except Exception as e:
                        erro_atual = {
                            "pagina": pagina,
                            "tentativa": tentativa,
                            "status": response.status_code,
                            "mensagem": f"Erro ao interpretar JSON: {str(e)}",
                            "url": response.url
                        }
                        time.sleep(0.8 * tentativa)
                        continue

                    if total_registros is None:
                        total_registros = data.get("totalRegistros")
                        total_paginas = data.get("totalPaginas")

                    pagina_resultados = data.get("resultado", [])

                    if not pagina_resultados:
                        return (
                            resultados,
                            erros,
                            total_registros,
                            total_paginas,
                            urls_consultadas
                        )

                    resultados.extend(pagina_resultados)
                    sucesso_pagina = True
                    break

                erro_atual = {
                    "pagina": pagina,
                    "tentativa": tentativa,
                    "status": response.status_code,
                    "mensagem": response.text,
                    "url": response.url
                }

                time.sleep(0.8 * tentativa)

            except Exception as e:
                erro_atual = {
                    "pagina": pagina,
                    "tentativa": tentativa,
                    "status": "erro",
                    "mensagem": str(e),
                    "url": f"{BASE_URL}{endpoint}"
                }

                time.sleep(0.8 * tentativa)

        if not sucesso_pagina:
            if erro_atual:
                erros.append(erro_atual)

            if not pular_pagina_com_erro:
                break

            continue

        try:
            total_registros_int = int(total_registros) if total_registros is not None else 0
        except Exception:
            total_registros_int = 0

        if total_registros_int > 0 and len(resultados) >= total_registros_int:
            break

        time.sleep(0.2)

    return resultados, erros, total_registros, total_paginas, urls_consultadas


@st.cache_data(ttl=1800, show_spinner=False)
def carregar_itens_por_pdm(
    codigo_pdm,
    somente_ativos=True,
    max_paginas=10,
    tamanho_pagina=50
):
    """
    Consulta CATMATs por PDM com estratégia defensiva.

    Estratégia:
    1. Tenta primeiro com os parâmetros informados pelo usuário.
    2. Se a API retornar erro e nenhum resultado, reduz tamanhoPagina.
    3. Se ainda assim falhar, retorna diagnóstico sem afirmar que o PDM não possui CATMAT.
    4. Filtra ativos localmente apenas quando a coluna statusItem existir e tiver formato reconhecível.
    """

    params = {
        "codigoPdm": int(codigo_pdm)
    }

    tamanhos_tentativa = []

    for t in [int(tamanho_pagina), 25, 10, 5]:
        if t not in tamanhos_tentativa and t > 0:
            tamanhos_tentativa.append(t)

    melhor_df = pd.DataFrame()
    todos_erros = []
    todas_urls = []
    melhor_total_registros = None
    melhor_total_paginas = None
    consulta_teve_erro = False
    consulta_teve_sucesso = False

    for tamanho in tamanhos_tentativa:
        resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
            endpoint="/modulo-material/4_consultarItemMaterial",
            params_base=params,
            tamanho_pagina=tamanho,
            max_paginas=int(max_paginas),
            timeout=60,
            tentativas_por_pagina=2,
            pular_pagina_com_erro=True
        )

        todos_erros.extend(erros)
        todas_urls.extend(urls)

        if total_registros is not None:
            melhor_total_registros = total_registros

        if total_paginas is not None:
            melhor_total_paginas = total_paginas

        if erros:
            consulta_teve_erro = True

        if resultados:
            consulta_teve_sucesso = True
            melhor_df = pd.DataFrame(resultados)
            break

        time.sleep(0.5)

    df = melhor_df.copy()

    if somente_ativos and not df.empty and "statusItem" in df.columns:
        serie_status = df["statusItem"]

        if serie_status.dtype == bool:
            df = df[df["statusItem"] == True].copy()
        else:
            status_norm = serie_status.astype(str).apply(normalizar_texto)
            df = df[
                status_norm.isin(["TRUE", "1", "ATIVO", "SIM", "S"])
            ].copy()

    diagnostico = {
        "consulta_teve_sucesso": consulta_teve_sucesso,
        "consulta_teve_erro": consulta_teve_erro,
        "tentativas_tamanho_pagina": tamanhos_tentativa,
        "total_registros_api": melhor_total_registros,
        "total_paginas_api": melhor_total_paginas,
        "quantidade_carregada": int(len(df)),
        "erros": todos_erros,
        "urls": todas_urls
    }

    return (
        df,
        todos_erros,
        melhor_total_registros,
        melhor_total_paginas,
        todas_urls,
        diagnostico
    )


def consultar_precos_multiplos_catmats(
    codigos_catmat,
    data_inicial,
    data_final,
    max_paginas_por_catmat=2,
    tamanho_pagina=50
):
    todos_resultados = []
    todos_erros = []
    urls = []

    total = len(codigos_catmat)

    if total == 0:
        return todos_resultados, todos_erros, urls

    progresso = st.progress(0)

    for i, codigo in enumerate(codigos_catmat):
        params_base = {
            "codigoItemCatalogo": int(codigo),
            "dataCompraMin": data_inicial.strftime("%Y-%m-%d"),
            "dataCompraMax": data_final.strftime("%Y-%m-%d")
        }

        resultados, erros, _, _, urls_consultadas = consultar_paginas(
            endpoint="/modulo-pesquisa-preco/1_consultarMaterial",
            params_base=params_base,
            tamanho_pagina=int(tamanho_pagina),
            max_paginas=int(max_paginas_por_catmat),
            timeout=120,
            tentativas_por_pagina=3,
            pular_pagina_com_erro=True
        )

        for item in resultados:
            item["catmat_consultado"] = int(codigo)

        todos_resultados.extend(resultados)
        todos_erros.extend(erros)
        urls.extend(urls_consultadas)

        progresso.progress((i + 1) / total)
        time.sleep(0.2)

    return todos_resultados, todos_erros, urls


# ============================================================
# EXTRAÇÃO DINÂMICA DE CARACTERÍSTICAS
# ============================================================

def extrair_caracteristicas(descricao):
    """
    Extrai pares 'CHAVE: VALOR' da descrição do CATMAT.

    Exemplo:
    NOTEBOOK, TELA: SUPERIOR A 14 POL, MEMÓRIA RAM: MÍNIMO DE 16 GB
    vira:
    {
        "TELA": "SUPERIOR A 14 POL",
        "MEMORIA RAM": "MÍNIMO DE 16 GB"
    }
    """

    if descricao is None or pd.isna(descricao):
        return {}

    texto = str(descricao)
    partes = [p.strip() for p in texto.split(",")]

    caracteristicas = {}

    for parte in partes:
        if ":" in parte:
            chave, valor = parte.split(":", 1)

            chave = normalizar_texto(chave)
            valor = str(valor).strip().upper()

            if chave and valor:
                caracteristicas[chave] = valor

    return caracteristicas


def criar_tabela_caracteristicas(df_itens):
    if df_itens is None or df_itens.empty:
        return pd.DataFrame()

    coluna_descricao = primeira_coluna_existente(
        df_itens,
        ["descricaoItem", "descricao", "descricao_item"]
    )

    if coluna_descricao is None:
        return df_itens.copy()

    registros = []

    for _, row in df_itens.iterrows():
        base = row.to_dict()
        caracteristicas = extrair_caracteristicas(row.get(coluna_descricao))

        for chave, valor in caracteristicas.items():
            base[f"CARAC_{chave}"] = valor

        registros.append(base)

    return pd.DataFrame(registros)


def identificar_colunas_caracteristicas(df):
    if df is None or df.empty:
        return []

    colunas = [c for c in df.columns if c.startswith("CARAC_")]
    candidatas = []

    for coluna in colunas:
        serie = df[coluna].dropna().astype(str).str.strip()

        if serie.empty:
            continue

        qtd_distintos = serie.nunique()
        cobertura = len(serie) / len(df)

        if 1 <= qtd_distintos <= 100 and cobertura >= 0.03:
            candidatas.append({
                "coluna": coluna,
                "nome": coluna.replace("CARAC_", ""),
                "qtd_distintos": qtd_distintos,
                "cobertura": cobertura
            })

    candidatas = sorted(
        candidatas,
        key=lambda x: (x["cobertura"], -x["qtd_distintos"]),
        reverse=True
    )

    return candidatas


def aplicar_filtros_dinamicos(df, filtros):
    df_filtrado = df.copy()

    for coluna, valores in filtros.items():
        if valores:
            df_filtrado = df_filtrado[df_filtrado[coluna].isin(valores)]

    return df_filtrado


# ============================================================
# ESTATÍSTICAS E RESUMOS
# ============================================================

def exibir_estatisticas_precos(df):
    st.subheader("Resumo estatístico dos preços")

    coluna_preco = primeira_coluna_existente(
        df,
        ["precoUnitario", "valorUnitario", "preco_unitario", "valor_unitario"]
    )

    if coluna_preco is None:
        st.warning("Não foi encontrada coluna de preço unitário no retorno da API.")
        return {}

    df_calc = df.copy()
    df_calc[coluna_preco] = pd.to_numeric(df_calc[coluna_preco], errors="coerce")
    df_precos = df_calc.dropna(subset=[coluna_preco])

    if df_precos.empty:
        st.warning("Não há preços válidos para cálculo estatístico.")
        return {}

    estatisticas = {
        "registros_com_preco": int(len(df_precos)),
        "menor_preco": float(df_precos[coluna_preco].min()),
        "maior_preco": float(df_precos[coluna_preco].max()),
        "preco_medio": float(df_precos[coluna_preco].mean()),
        "mediana": float(df_precos[coluna_preco].median())
    }

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Registros com preço", estatisticas["registros_com_preco"])
    col2.metric("Menor preço", formatar_brl(estatisticas["menor_preco"]))
    col3.metric("Maior preço", formatar_brl(estatisticas["maior_preco"]))
    col4.metric("Preço médio", formatar_brl(estatisticas["preco_medio"]))
    col5.metric("Mediana", formatar_brl(estatisticas["mediana"]))

    st.caption(
        "A média pode ser distorcida por outliers. Para planejamento preliminar, "
        "a mediana tende a ser uma referência mais robusta."
    )

    return estatisticas


def gerar_resumo_dod(
    demanda_texto,
    quantidade,
    codigo_pdm,
    nome_pdm,
    codigos_catmat,
    df_precos,
    estatisticas,
    data_inicial,
    data_final
):
    fornecedor_col = primeira_coluna_existente(
        df_precos,
        ["nomeFornecedor", "fornecedor", "razaoSocialFornecedor"]
    )

    orgao_col = primeira_coluna_existente(
        df_precos,
        ["nomeOrgao", "orgao", "nomeOrgaoSuperior"]
    )

    descricao_col = primeira_coluna_existente(
        df_precos,
        ["descricaoItem", "descricao", "descricao_item"]
    )

    fornecedores = []
    orgaos = []
    descricoes = []

    if fornecedor_col:
        fornecedores = (
            df_precos[fornecedor_col]
            .fillna("Não informado")
            .value_counts()
            .head(10)
            .index
            .tolist()
        )

    if orgao_col:
        orgaos = (
            df_precos[orgao_col]
            .fillna("Não informado")
            .value_counts()
            .head(10)
            .index
            .tolist()
        )

    if descricao_col:
        descricoes = (
            df_precos[descricao_col]
            .dropna()
            .drop_duplicates()
            .head(10)
            .tolist()
        )

    resumo = {
        "demanda": demanda_texto,
        "quantidade_estimada": quantidade,
        "codigo_pdm": codigo_pdm,
        "nome_pdm": nome_pdm,
        "catmats_considerados": codigos_catmat,
        "periodo_pesquisa": {
            "data_inicial": data_inicial.strftime("%Y-%m-%d"),
            "data_final": data_final.strftime("%Y-%m-%d")
        },
        "registros_de_preco": int(len(df_precos)),
        "estatisticas": {
            "registros_com_preco": estatisticas.get("registros_com_preco"),
            "menor_preco": formatar_brl(estatisticas.get("menor_preco")),
            "maior_preco": formatar_brl(estatisticas.get("maior_preco")),
            "preco_medio": formatar_brl(estatisticas.get("preco_medio")),
            "mediana": formatar_brl(estatisticas.get("mediana"))
        },
        "fornecedores_recorrentes": fornecedores,
        "orgaos_compradores_recorrentes": orgaos,
        "descricoes_de_itens_encontradas": descricoes
    }

    return resumo


def gerar_texto_base_dod_dataprev(resumo, metadados_dod):
    demanda = resumo.get("demanda", "")
    quantidade = resumo.get("quantidade_estimada", "")
    codigo_pdm = resumo.get("codigo_pdm", "")
    nome_pdm = resumo.get("nome_pdm", "")
    catmats = resumo.get("catmats_considerados", [])
    estat = resumo.get("estatisticas", {})
    periodo = resumo.get("periodo_pesquisa", {})

    motivacao = metadados_dod.get("motivacao", "Nova contratação")
    riscos = metadados_dod.get("riscos", "Descontinuidade operacional, atraso na entrega de serviços e aumento de custos por aquisição emergencial.")
    resultados = metadados_dod.get("resultados", "Padronização do objeto, contratação tempestiva e melhoria da eficiência operacional.")
    data_prevista = metadados_dod.get("data_prevista", "A definir")
    fornecedores = metadados_dod.get("fornecedores", "Sem fornecedor previamente definido")
    areas_internas = metadados_dod.get("areas_internas", "Área demandante; área de contratações; área técnica")
    clientes_externos = metadados_dod.get("clientes_externos", "Não se aplica")
    info_adicionais = metadados_dod.get("informacoes_adicionais", "Pesquisa baseada em dados abertos do Compras.gov.br.")
    notas = metadados_dod.get("notas", "Documento preliminar para refinamento em ETP/TR.")
    anexos = metadados_dod.get("anexos", "Anexar planilha de preços e memória de cálculo.")

    itens_descritos = resumo.get("descricoes_de_itens_encontradas", [])
    itens_preview = "\n".join([f"- {d}" for d in itens_descritos[:10]]) if itens_descritos else "- Item detalhado na demanda textual."

    opcoes = [
        "( ) Atendimento de obrigação legal/regulatória",
        "( ) Continuidade de contrato existente",
        "(X) Nova contratação",
        "( ) Expansão/evolução de solução existente"
    ]

    texto = f"""
# Documento de Oficialização da Demanda (DOD) - Dataprev (minuta semiestruturada)

## 2.1. CONTEXTO DE NEGÓCIO
A demanda atende ao planejamento da área requisitante para viabilizar aquisição/contratação aderente ao PDM {codigo_pdm} - {nome_pdm}, com base em evidências de compras públicas e rastreabilidade dos CATMATs selecionados.

## 3. CONTEXTO DA DEMANDA

### 3.1. SITUAÇÃO ATUAL
Processo atual com classificação manual de PDM/CATMAT, maior tempo de ciclo e baixa padronização da justificativa técnica.

### 3.2. ESCOPO DA DEMANDA
Demanda registrada: **{demanda}**.
Quantidade estimada: **{quantidade}**.

### 3.3. MOTIVAÇÃO DA DEMANDA

#### 3.3.1. Assinalar com um X a opção que se enquadra na motivação da demanda:
{chr(10).join(opcoes)}
Motivação detalhada informada: {motivacao}.

#### 3.3.2. Descrever os riscos envolvidos, caso a contratação não seja realizada.
{riscos}

#### 3.3.3. Descrever os resultados a serem alcançados com a contratação.
{resultados}

### 3.4. DATA PREVISTA PARA DISPONIBILIZAÇÃO DA DEMANDA
{data_prevista}

### 3.5. FORNECEDOR(ES) (SE HOUVER)
{fornecedores}

### 3.6. DESCRIÇÃO DOS OBJETOS E QUANTIDADES ENVOLVIDAS

#### 3.6.1. Para contratos EXISTENTES:
Não se aplica nesta minuta preliminar (validar com área gestora do contrato, se houver).

#### 3.6.2. Para NOVA contratação:
- PDM de referência: **{codigo_pdm} - {nome_pdm}**
- CATMATs considerados: **{", ".join([str(c) for c in catmats]) if catmats else "A definir"}**
- Itens observados na base pública:
{itens_preview}
- Período de pesquisa de preços: **{periodo.get("data_inicial")} a {periodo.get("data_final")}**
- Registros com preço válido: **{estat.get("registros_com_preco")}**
- Mediana preliminar: **{estat.get("mediana")}**

### 3.7. SERVIÇOS ASSOCIADOS A DEMANDA:
Marcar os serviços aplicáveis no refinamento do ETP/TR.

#### 3.7.2. Para cada serviço, selecionado no item anterior, descrever as condições mínimas obrigatórias:

##### 3.7.2.1. Orientação Técnica
Caso aplicável, prever orientação para especificação, implantação e boas práticas de uso do objeto contratado.

##### 3.7.2.2. Capacitação Técnica
Caso aplicável, prever capacitação de usuários/gestores, com carga horária, público-alvo e material didático.

##### 3.7.2.3. Suporte Técnico
Caso aplicável, prever níveis de serviço (SLA), canais de atendimento e janelas de suporte.

## 4. ÁREAS E PAPÉIS ENVOLVIDOS

### 4.1. ÁREAS INTERNAS (DATAPREV)
{areas_internas}

### 4.2. CLIENTES EXTERNOS QUE FARÃO USO DA SOLUÇÃO/SOFTWARE (OU SERÃO BENEFICIADOS DIRETAMENTE)
{clientes_externos}

## 5. INFORMAÇÕES ADICIONAIS
{info_adicionais}

## 6. NOTAS
{notas}

## 7. ANEXOS
{anexos}
"""

    return texto.strip()


# ============================================================
# INTERFACE
# ============================================================

aba_principal, aba_direta = st.tabs([
    "Demanda → PDM manual → filtros → preços → DOD",
    "Consulta direta por CATMAT"
])


# ============================================================
# ABA PRINCIPAL
# ============================================================

with aba_principal:
    st.header("1. Descrever demanda")

    demanda_texto = st.text_area(
        "Descreva a demanda",
        value=(
            "Precisamos comprar 500 notebooks corporativos com tela de 16 polegadas, "
            "32 GB de RAM, SSD NVMe de 512 GB, Wi-Fi 6, TPM 2.0 e garantia on site de 60 meses."
        ),
        height=120
    )

    col_demanda_1, col_demanda_2, col_demanda_3 = st.columns(3)

    with col_demanda_1:
        quantidade = st.number_input(
            "Quantidade estimada",
            min_value=1,
            value=500,
            step=1
        )

    with col_demanda_2:
        codigo_pdm = st.number_input(
            "Código PDM",
            min_value=1,
            value=8435,
            step=1
        )

    with col_demanda_3:
        nome_pdm_informado = st.text_input(
            "Nome do PDM, opcional",
            value="Notebook"
        )

    st.caption(
        "Nesta versão, o PDM é informado manualmente. Exemplo: Notebook = 8435; Tesoura = 249."
    )

    st.header("2. Carregar CATMATs do PDM")

    col_catmat_1, col_catmat_2, col_catmat_3 = st.columns(3)

    with col_catmat_1:
        somente_ativos = st.checkbox("Somente CATMATs ativos", value=True)

    with col_catmat_2:
        max_paginas_catmat = st.number_input(
            "Máximo de páginas de CATMAT",
            min_value=1,
            max_value=30,
            value=5,
            step=1
        )

    with col_catmat_3:
        tamanho_pagina_catmat = st.number_input(
            "Registros por página no CATMAT",
            min_value=5,
            max_value=50,
            value=10,
            step=5
        )

    if st.button("Carregar CATMATs do PDM"):
        limpar_session_state_catmat()

        with st.spinner("Consultando CATMATs do PDM informado..."):
            (
                df_itens,
                erros_itens,
                total_itens,
                paginas_itens,
                urls_itens,
                diagnostico_itens
            ) = carregar_itens_por_pdm(
                codigo_pdm=int(codigo_pdm),
                somente_ativos=somente_ativos,
                max_paginas=int(max_paginas_catmat),
                tamanho_pagina=int(tamanho_pagina_catmat)
            )

        df_itens_carac = criar_tabela_caracteristicas(df_itens)

        st.session_state["demanda_texto"] = demanda_texto
        st.session_state["quantidade"] = int(quantidade)
        st.session_state["codigo_pdm_atual"] = int(codigo_pdm)
        st.session_state["nome_pdm_atual"] = nome_pdm_informado
        st.session_state["df_itens_carac"] = df_itens_carac
        st.session_state["erros_itens"] = erros_itens
        st.session_state["total_itens"] = total_itens
        st.session_state["paginas_itens"] = paginas_itens
        st.session_state["urls_itens"] = urls_itens
        st.session_state["diagnostico_itens"] = diagnostico_itens

    if "df_itens_carac" in st.session_state:
        df_itens_carac = st.session_state["df_itens_carac"]
        erros_itens = st.session_state.get("erros_itens", [])
        total_itens = st.session_state.get("total_itens")
        paginas_itens = st.session_state.get("paginas_itens")
        urls_itens = st.session_state.get("urls_itens", [])
        diagnostico_itens = st.session_state.get("diagnostico_itens", {})

        st.header("3. Filtros dinâmicos por características do PDM")

        st.write("Total de CATMATs informado pela API:", total_itens)
        st.write("Total de páginas informado pela API:", paginas_itens)
        st.write("CATMATs carregados no app:", len(df_itens_carac))

        with st.expander("Diagnóstico da consulta de CATMAT"):
            st.write("Colunas retornadas/criadas:")
            st.write(list(df_itens_carac.columns))
            st.write("URLs consultadas:")
            st.write(urls_itens)

            if diagnostico_itens:
                st.write("Diagnóstico consolidado:")
                st.json(diagnostico_itens)

            if not df_itens_carac.empty:
                st.dataframe(df_itens_carac.head(20), use_container_width=True)

        if erros_itens:
            st.warning("A consulta de CATMAT retornou erro em uma ou mais páginas.")
            st.json(erros_itens)

        if df_itens_carac.empty:
            if diagnostico_itens.get("consulta_teve_erro"):
                st.error(
                    "A API do Compras.gov.br retornou erro ao consultar os CATMATs deste PDM. "
                    "Isso não significa que o PDM não possua CATMATs."
                )

                st.info(
                    "Reduza a quantidade de páginas, use poucos registros por página ou tente novamente em outro momento."
                )

                with st.expander("Diagnóstico técnico da consulta"):
                    st.json(diagnostico_itens)

            else:
                st.warning(
                    "Nenhum CATMAT foi retornado para o PDM informado."
                )

                with st.expander("Diagnóstico da consulta"):
                    st.json(diagnostico_itens)

        else:
            candidatas = identificar_colunas_caracteristicas(df_itens_carac)

            st.caption(
                "Os filtros abaixo são extraídos automaticamente da descrição dos CATMATs do PDM informado. "
                "Esta é uma extração textual heurística."
            )

            filtros = {}

            if not candidatas:
                st.warning("Não foram identificadas características estruturáveis na descrição dos itens.")
            else:
                limite_superior = min(20, len(candidatas))

                max_filtros = st.slider(
                    "Quantidade máxima de características exibidas",
                    min_value=1,
                    max_value=limite_superior,
                    value=min(10, limite_superior),
                    key="max_filtros_dinamicos"
                )

                cols = st.columns(3)

                for i, item in enumerate(candidatas[:max_filtros]):
                    coluna = item["coluna"]
                    nome = item["nome"]

                    valores = (
                        df_itens_carac[coluna]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .sort_values()
                        .unique()
                        .tolist()
                    )

                    with cols[i % 3]:
                        selecionados = st.multiselect(
                            label=f"{nome}",
                            options=valores,
                            default=[],
                            key=f"filtro_{st.session_state.get('codigo_pdm_atual')}_{coluna}"
                        )

                    filtros[coluna] = selecionados

            df_filtrado = aplicar_filtros_dinamicos(df_itens_carac, filtros)

            st.header("4. CATMATs candidatos após filtros")
            st.write("CATMATs após filtros:", len(df_filtrado))

            colunas_base = [
                "codigoItem",
                "nomePdm",
                "descricaoItem",
                "statusItem",
                "itemSustentavel",
                "dataHoraAtualizacao"
            ]

            colunas_carac = [c for c in df_filtrado.columns if c.startswith("CARAC_")]
            colunas_exibir = [
                c for c in colunas_base + colunas_carac if c in df_filtrado.columns
            ]

            if colunas_exibir:
                st.dataframe(
                    df_filtrado[colunas_exibir].head(500),
                    use_container_width=True
                )
            else:
                st.dataframe(
                    df_filtrado.head(500),
                    use_container_width=True
                )

            codigo_item_col = primeira_coluna_existente(
                df_filtrado,
                ["codigoItem", "codigo_item", "codItem", "idItem"]
            )

            if df_filtrado.empty:
                st.warning("Nenhum CATMAT permaneceu após os filtros.")
            elif codigo_item_col is None:
                st.error("Não foi possível identificar a coluna de código CATMAT.")
            else:
                codigos_disponiveis = (
                    df_filtrado[codigo_item_col]
                    .dropna()
                    .astype(int)
                    .drop_duplicates()
                    .tolist()
                )

                max_default = min(10, len(codigos_disponiveis))
                codigos_default = codigos_disponiveis[:max_default]

                codigos_selecionados = st.multiselect(
                    "Selecione os CATMATs para consulta de preços",
                    options=codigos_disponiveis,
                    default=codigos_default,
                    key="codigos_catmat_selecionados"
                )

                st.header("5. Consulta de preços")

                col_preco_1, col_preco_2, col_preco_3, col_preco_4 = st.columns(4)

                with col_preco_1:
                    data_inicial = st.date_input(
                        "Data inicial",
                        value=date(2024, 1, 1),
                        key="data_inicial_precos"
                    )

                with col_preco_2:
                    data_final = st.date_input(
                        "Data final",
                        value=date.today(),
                        key="data_final_precos"
                    )

                with col_preco_3:
                    max_paginas_preco = st.number_input(
                        "Máximo de páginas por CATMAT",
                        min_value=1,
                        max_value=10,
                        value=2,
                        step=1,
                        key="max_paginas_preco"
                    )

                with col_preco_4:
                    tamanho_pagina_preco = st.number_input(
                        "Registros por página em preços",
                        min_value=10,
                        max_value=100,
                        value=50,
                        step=10,
                        key="tamanho_pagina_preco"
                    )

                if st.button("Consultar preços dos CATMATs selecionados"):
                    limpar_session_state_precos()

                    if not codigos_selecionados:
                        st.warning("Selecione pelo menos um CATMAT.")
                    else:
                        with st.spinner("Consultando preços praticados..."):
                            resultados_precos, erros_precos, urls_precos = consultar_precos_multiplos_catmats(
                                codigos_catmat=codigos_selecionados,
                                data_inicial=data_inicial,
                                data_final=data_final,
                                max_paginas_por_catmat=int(max_paginas_preco),
                                tamanho_pagina=int(tamanho_pagina_preco)
                            )

                        df_precos = pd.DataFrame(resultados_precos)

                        st.session_state["resultados_precos"] = resultados_precos
                        st.session_state["erros_precos"] = erros_precos
                        st.session_state["urls_precos"] = urls_precos
                        st.session_state["df_precos"] = df_precos
                        st.session_state["codigos_catmat_consultados"] = codigos_selecionados
                        st.session_state["data_inicial_precos_final"] = data_inicial
                        st.session_state["data_final_precos_final"] = data_final

    if "df_precos" in st.session_state:
        df_precos = st.session_state["df_precos"]
        erros_precos = st.session_state.get("erros_precos", [])
        urls_precos = st.session_state.get("urls_precos", [])

        st.header("6. Resultados consolidados de preços")

        st.write("Registros de preços carregados:", len(df_precos))

        with st.expander("URLs consultadas na pesquisa de preços"):
            st.write(urls_precos)

        if erros_precos:
            st.warning("Algumas consultas de preço retornaram erro.")
            st.json(erros_precos)

        if df_precos.empty:
            st.warning("Nenhum preço retornado para os CATMATs selecionados.")
        else:
            st.dataframe(df_precos, use_container_width=True)

            estatisticas = exibir_estatisticas_precos(df_precos)

            fornecedor_col = primeira_coluna_existente(
                df_precos,
                ["nomeFornecedor", "fornecedor", "razaoSocialFornecedor"]
            )

            if fornecedor_col:
                st.subheader("Fornecedores mais recorrentes")
                fornecedores = (
                    df_precos[fornecedor_col]
                    .fillna("Não informado")
                    .value_counts()
                    .reset_index()
                )
                fornecedores.columns = ["Fornecedor", "Ocorrências"]
                st.dataframe(fornecedores.head(20), use_container_width=True)

            orgao_col = primeira_coluna_existente(
                df_precos,
                ["nomeOrgao", "orgao", "nomeOrgaoSuperior"]
            )

            if orgao_col:
                st.subheader("Órgãos compradores mais recorrentes")
                orgaos = (
                    df_precos[orgao_col]
                    .fillna("Não informado")
                    .value_counts()
                    .reset_index()
                )
                orgaos.columns = ["Órgão", "Ocorrências"]
                st.dataframe(orgaos.head(20), use_container_width=True)

            descricao_col = primeira_coluna_existente(
                df_precos,
                ["descricaoItem", "descricao", "descricao_item"]
            )

            if descricao_col:
                st.subheader("Descrições de itens encontradas")
                colunas_desc = ["catmat_consultado", descricao_col]
                colunas_desc = [c for c in colunas_desc if c in df_precos.columns]

                descricoes = df_precos[colunas_desc].drop_duplicates()
                st.dataframe(descricoes.head(100), use_container_width=True)

            csv = df_precos.to_csv(index=False).encode("utf-8-sig")

            st.download_button(
                label="Baixar preços consolidados em CSV",
                data=csv,
                file_name="precos_consolidados_catmat.csv",
                mime="text/csv"
            )

            st.header("7. Insumos preliminares para DOD")

            resumo = gerar_resumo_dod(
                demanda_texto=st.session_state.get("demanda_texto", demanda_texto),
                quantidade=st.session_state.get("quantidade", quantidade),
                codigo_pdm=st.session_state.get("codigo_pdm_atual", codigo_pdm),
                nome_pdm=st.session_state.get("nome_pdm_atual", nome_pdm_informado),
                codigos_catmat=st.session_state.get("codigos_catmat_consultados", []),
                df_precos=df_precos,
                estatisticas=estatisticas,
                data_inicial=st.session_state.get("data_inicial_precos_final", date(2024, 1, 1)),
                data_final=st.session_state.get("data_final_precos_final", date.today())
            )

            with st.expander("Resumo estruturado para envio ao ChatGPT"):
                st.json(resumo)

            st.subheader("Parâmetros de preenchimento do DOD Dataprev")
            col_dod_1, col_dod_2 = st.columns(2)
            with col_dod_1:
                motivacao_dod = st.text_input("Motivação detalhada", value="Nova contratação para atendimento da demanda.")
                riscos_dod = st.text_area("Riscos se não contratar", value="Interrupção ou degradação dos serviços e aumento de risco operacional.", height=100)
                resultados_dod = st.text_area("Resultados esperados", value="Melhorar eficiência e garantir continuidade operacional com objeto padronizado.", height=100)
                data_prevista_dod = st.date_input("Data prevista para disponibilização")
            with col_dod_2:
                fornecedores_dod = st.text_area("Fornecedor(es), se houver", value="A definir após fase de seleção.", height=80)
                areas_dod = st.text_area("Áreas internas Dataprev", value="Área demandante; área técnica; área de contratações.", height=80)
                clientes_dod = st.text_area("Clientes externos beneficiados", value="Unidades usuárias do serviço/solução.", height=80)
                infos_dod = st.text_area("Informações adicionais", value="Pesquisa baseada em dados abertos de compras públicas.", height=80)
            metadados_dod = {
                "motivacao": motivacao_dod,
                "riscos": riscos_dod,
                "resultados": resultados_dod,
                "data_prevista": data_prevista_dod.strftime("%Y-%m-%d"),
                "fornecedores": fornecedores_dod,
                "areas_internas": areas_dod,
                "clientes_externos": clientes_dod,
                "informacoes_adicionais": infos_dod,
                "notas": "Minuta semiestruturada gerada automaticamente para revisão da área técnica e de contratações.",
                "anexos": "1) Planilha de preços consolidados; 2) Lista de CATMATs selecionados; 3) Evidências de pesquisa no Compras.gov.br."
            }

            texto_dod = gerar_texto_base_dod_dataprev(resumo, metadados_dod)

            st.text_area(
                "Texto-base preliminar para DOD",
                value=texto_dod,
                height=500
            )


# ============================================================
# ABA 2 - CONSULTA DIRETA POR CATMAT
# ============================================================

with aba_direta:
    st.header("Consulta direta por CATMAT")

    st.info(
        "Use esta aba quando você já souber o código CATMAT e quiser consultar diretamente os preços praticados."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        codigo_item = st.text_input(
            "Código CATMAT",
            value="450340",
            key="direto_codigo_catmat"
        )

    with col2:
        tamanho_pagina = st.number_input(
            "Registros por página",
            min_value=10,
            max_value=100,
            value=50,
            step=10,
            key="direto_tamanho"
        )

    with col3:
        max_paginas = st.number_input(
            "Máximo de páginas",
            min_value=1,
            max_value=20,
            value=5,
            step=1,
            key="direto_paginas"
        )

    col4, col5 = st.columns(2)

    with col4:
        data_inicial_direta = st.date_input(
            "Data inicial",
            value=date(2024, 1, 1),
            key="direto_data_inicial"
        )

    with col5:
        data_final_direta = st.date_input(
            "Data final",
            value=date.today(),
            key="direto_data_final"
        )

    if st.button("Consultar preços praticados", key="direto_botao"):
        try:
            params_base = {
                "codigoItemCatalogo": int(codigo_item),
                "dataCompraMin": data_inicial_direta.strftime("%Y-%m-%d"),
                "dataCompraMax": data_final_direta.strftime("%Y-%m-%d")
            }

            with st.spinner("Consultando Pesquisa de Preços..."):
                resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
                    endpoint="/modulo-pesquisa-preco/1_consultarMaterial",
                    params_base=params_base,
                    tamanho_pagina=int(tamanho_pagina),
                    max_paginas=int(max_paginas),
                    timeout=120,
                    tentativas_por_pagina=3,
                    pular_pagina_com_erro=True
                )

            st.write("Total de registros informado pela API:", total_registros)
            st.write("Total de páginas informado pela API:", total_paginas)
            st.write("Registros carregados no app:", len(resultados))

            with st.expander("URLs consultadas"):
                st.write(urls)

            if erros:
                st.warning("A consulta retornou erro em uma ou mais páginas.")
                st.json(erros)

            if resultados:
                df = pd.DataFrame(resultados)

                st.subheader("Resultado em tabela")
                st.dataframe(df, use_container_width=True)

                exibir_estatisticas_precos(df)

                csv = df.to_csv(index=False).encode("utf-8-sig")

                st.download_button(
                    label="Baixar resultado em CSV",
                    data=csv,
                    file_name="pesquisa_precos_catmat.csv",
                    mime="text/csv"
                )

                with st.expander("Ver dados brutos"):
                    st.json(resultados)
            else:
                st.warning("Nenhum registro retornado para os parâmetros informados.")

        except ValueError:
            st.error("O código CATMAT deve ser numérico.")
