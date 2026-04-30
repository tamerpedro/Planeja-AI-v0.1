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
    "Normalização dinâmica de objeto por PDM, extração de características técnicas, "
    "seleção de CATMATs candidatos e consulta de preços no Compras.gov.br."
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
    for coluna in candidatas:
        if coluna in df.columns:
            return coluna
    return None


def criar_colunas_normalizadas(df):
    df = df.copy()

    if df.empty:
        return df

    for coluna in df.columns:
        if df[coluna].dtype == "object":
            df[f"{coluna}_norm"] = df[coluna].apply(normalizar_texto)

    return df


def buscar_em_colunas_textuais(df, termo):
    if df.empty:
        return df.copy()

    termo_norm = normalizar_texto(termo)

    if not termo_norm:
        return df.copy()

    mascara = pd.Series(False, index=df.index)

    for coluna in df.columns:
        if df[coluna].dtype == "object" or coluna.endswith("_norm"):
            serie = (
                df[coluna]
                .fillna("")
                .astype(str)
                .apply(normalizar_texto)
            )

            mascara = mascara | serie.str.contains(
                termo_norm,
                na=False,
                regex=False
            )

    return df[mascara].copy()


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


def consultar_paginas(endpoint, params_base, tamanho_pagina=500, max_paginas=20, timeout=120):
    resultados = []
    erros = []
    urls_consultadas = []
    total_registros = None
    total_paginas = None

    for pagina in range(1, int(max_paginas) + 1):
        params = dict(params_base)
        params["pagina"] = pagina
        params["tamanhoPagina"] = tamanho_pagina

        try:
            response = consultar_endpoint(
                endpoint=endpoint,
                params=params,
                timeout=timeout
            )

            urls_consultadas.append(response.url)

            if response.status_code != 200:
                erros.append({
                    "pagina": pagina,
                    "status": response.status_code,
                    "mensagem": response.text,
                    "url": response.url
                })
                break

            data = response.json()

            if total_registros is None:
                total_registros = data.get("totalRegistros")
                total_paginas = data.get("totalPaginas")

            pagina_resultados = data.get("resultado", [])

            if not pagina_resultados:
                break

            resultados.extend(pagina_resultados)

            if total_paginas is not None and pagina >= int(total_paginas):
                break

            time.sleep(0.25)

        except Exception as e:
            erros.append({
                "pagina": pagina,
                "status": "erro",
                "mensagem": str(e)
            })
            break

    return resultados, erros, total_registros, total_paginas, urls_consultadas


@st.cache_data(ttl=3600)
def carregar_pdms_ativos(max_paginas=50):
    resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
        endpoint="/modulo-material/3_consultarPdmMaterial",
        params_base={"statusPdm": True},
        tamanho_pagina=500,
        max_paginas=max_paginas,
        timeout=120
    )

    df = pd.DataFrame(resultados)
    df = criar_colunas_normalizadas(df)

    return df, erros, total_registros, total_paginas, urls


@st.cache_data(ttl=1800)
def carregar_itens_por_pdm(codigo_pdm, somente_ativos=True, max_paginas=20):
    params = {
        "codigoPdm": int(codigo_pdm)
    }

    if somente_ativos:
        params["statusItem"] = True

    resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
        endpoint="/modulo-material/4_consultarItemMaterial",
        params_base=params,
        tamanho_pagina=500,
        max_paginas=max_paginas,
        timeout=120
    )

    df = pd.DataFrame(resultados)

    return df, erros, total_registros, total_paginas, urls


def consultar_precos_multiplos_catmats(codigos_catmat, data_inicial, data_final, max_paginas_por_catmat=2):
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
            tamanho_pagina=50,
            max_paginas=max_paginas_por_catmat,
            timeout=120
        )

        for item in resultados:
            item["catmat_consultado"] = int(codigo)

        todos_resultados.extend(resultados)
        todos_erros.extend(erros)
        urls.extend(urls_consultadas)

        progresso.progress((i + 1) / total)
        time.sleep(0.25)

    return todos_resultados, todos_erros, urls


# ============================================================
# FUNÇÕES DE CARACTERÍSTICAS CATMAT
# ============================================================

def extrair_caracteristicas(descricao):
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
    if df_itens.empty:
        return df_itens.copy()

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
    if df.empty:
        return []

    colunas = [c for c in df.columns if c.startswith("CARAC_")]
    candidatas = []

    for coluna in colunas:
        serie = df[coluna].dropna().astype(str).str.strip()

        if serie.empty:
            continue

        qtd_distintos = serie.nunique()
        cobertura = len(serie) / len(df)

        if 2 <= qtd_distintos <= 80 and cobertura >= 0.05:
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
# ESTATÍSTICAS
# ============================================================

def exibir_estatisticas_precos(df):
    st.subheader("Resumo estatístico dos preços")

    coluna_preco = primeira_coluna_existente(
        df,
        ["precoUnitario", "valorUnitario", "preco_unitario", "valor_unitario"]
    )

    if coluna_preco is None:
        st.warning("Não foi encontrada coluna de preço unitário no retorno da API.")
        return

    df_calc = df.copy()
    df_calc[coluna_preco] = pd.to_numeric(df_calc[coluna_preco], errors="coerce")
    df_precos = df_calc.dropna(subset=[coluna_preco])

    if df_precos.empty:
        st.warning("Não há preços válidos para cálculo estatístico.")
        return

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Registros com preço", len(df_precos))
    col2.metric("Menor preço", formatar_brl(df_precos[coluna_preco].min()))
    col3.metric("Maior preço", formatar_brl(df_precos[coluna_preco].max()))
    col4.metric("Preço médio", formatar_brl(df_precos[coluna_preco].mean()))
    col5.metric("Mediana", formatar_brl(df_precos[coluna_preco].median()))

    st.caption(
        "A média pode ser distorcida por outliers. Para planejamento preliminar, "
        "a mediana tende a ser uma referência mais robusta."
    )


# ============================================================
# INTERFACE PRINCIPAL
# ============================================================

aba_dinamica, aba_direta = st.tabs([
    "Busca dinâmica por PDM e características",
    "Consulta direta por CATMAT"
])


# ============================================================
# ABA 1 - BUSCA DINÂMICA
# ============================================================

with aba_dinamica:
    st.header("1. Buscar família/PDM do objeto")

    col_busca_1, col_busca_2 = st.columns([2, 1])

    with col_busca_1:
        termo_busca = st.text_input(
            "Digite o objeto ou família de material",
            value="notebook",
            help="Exemplos: notebook, monitor, teclado, mouse, cadeira, switch"
        )

    with col_busca_2:
        max_paginas_pdm = st.number_input(
            "Máximo de páginas de PDM",
            min_value=1,
            max_value=100,
            value=50,
            step=5,
            key="max_paginas_pdm"
        )

    if st.button("Carregar e buscar PDMs ativos"):
        with st.spinner("Carregando PDMs ativos do Compras.gov.br..."):
            df_pdms, erros_pdm, total_pdm, paginas_pdm, urls_pdm = carregar_pdms_ativos(
                max_paginas=int(max_paginas_pdm)
            )

        st.session_state["df_pdms"] = df_pdms
        st.session_state["erros_pdm"] = erros_pdm
        st.session_state["total_pdm"] = total_pdm
        st.session_state["paginas_pdm"] = paginas_pdm
        st.session_state["urls_pdm"] = urls_pdm

        # Limpa resultados dependentes de busca anterior
        for chave in [
            "df_itens_carac",
            "resultados_precos",
            "erros_precos",
            "urls_precos",
            "codigo_pdm_atual"
        ]:
            st.session_state.pop(chave, None)

    if "df_pdms" not in st.session_state:
        st.info("Clique em 'Carregar e buscar PDMs ativos' para iniciar.")
    else:
        df_pdms = st.session_state["df_pdms"]
        erros_pdm = st.session_state.get("erros_pdm", [])
        total_pdm = st.session_state.get("total_pdm")
        paginas_pdm = st.session_state.get("paginas_pdm")
        urls_pdm = st.session_state.get("urls_pdm", [])

        st.subheader("PDMs carregados")

        st.write("Total de registros informado pela API:", total_pdm)
        st.write("Total de páginas informado pela API:", paginas_pdm)
        st.write("Registros carregados no app:", len(df_pdms))

        with st.expander("Diagnóstico da consulta de PDM"):
            st.write("Colunas retornadas pela API:")
            st.write(list(df_pdms.columns))
            st.write("URLs consultadas:")
            st.write(urls_pdm)

            if not df_pdms.empty:
                st.dataframe(df_pdms.head(20), use_container_width=True)

        if erros_pdm:
            st.warning("A consulta de PDM retornou erro em uma das páginas.")
            st.json(erros_pdm)

        df_pdm_filtrado = buscar_em_colunas_textuais(df_pdms, termo_busca)

        st.write("PDMs encontrados para o termo:", len(df_pdm_filtrado))

        if df_pdm_filtrado.empty:
            st.warning("Nenhum PDM encontrado para o termo informado.")
        else:
            codigo_pdm_col = primeira_coluna_existente(
                df_pdm_filtrado,
                ["codigoPdm", "codigo_pdm", "codPdm", "idPdm"]
            )

            nome_pdm_col = primeira_coluna_existente(
                df_pdm_filtrado,
                ["nomePdm", "nome_pdm", "descricaoPdm", "descricao"]
            )

            codigo_classe_col = primeira_coluna_existente(
                df_pdm_filtrado,
                ["codigoClasse", "codigo_classe", "codClasse"]
            )

            nome_classe_col = primeira_coluna_existente(
                df_pdm_filtrado,
                ["nomeClasse", "nome_classe"]
            )

            colunas_pdm_preferidas = [
                "codigoPdm",
                "nomePdm",
                "codigoClasse",
                "nomeClasse",
                "codigoGrupo",
                "nomeGrupo",
                "statusPdm"
            ]

            colunas_pdm_existentes = [
                c for c in colunas_pdm_preferidas if c in df_pdm_filtrado.columns
            ]

            if colunas_pdm_existentes:
                st.dataframe(
                    df_pdm_filtrado[colunas_pdm_existentes].head(200),
                    use_container_width=True
                )
            else:
                st.dataframe(
                    df_pdm_filtrado.head(200),
                    use_container_width=True
                )

            if codigo_pdm_col is None:
                st.error(
                    "Não foi possível identificar a coluna de código do PDM no retorno da API. "
                    "Abra o diagnóstico acima e verifique os nomes das colunas retornadas."
                )
            else:
                opcoes_pdm = []

                for _, row in df_pdm_filtrado.iterrows():
                    codigo_pdm_valor = row.get(codigo_pdm_col)
                    nome_pdm_valor = row.get(nome_pdm_col, "") if nome_pdm_col else ""
                    codigo_classe_valor = row.get(codigo_classe_col, "") if codigo_classe_col else ""
                    nome_classe_valor = row.get(nome_classe_col, "") if nome_classe_col else ""

                    label = (
                        f"{int(codigo_pdm_valor)} - {nome_pdm_valor} "
                        f"| Classe {codigo_classe_valor} - {nome_classe_valor}"
                    )

                    opcoes_pdm.append(label)

                pdm_selecionado_label = st.selectbox(
                    "Selecione o PDM",
                    options=opcoes_pdm,
                    key="pdm_selecionado_label"
                )

                codigo_pdm = int(pdm_selecionado_label.split(" - ")[0])

                st.header("2. Carregar CATMATs do PDM selecionado")

                col_item_1, col_item_2, col_item_3 = st.columns(3)

                with col_item_1:
                    somente_ativos = st.checkbox(
                        "Somente itens ativos",
                        value=True,
                        key="somente_ativos_itens"
                    )

                with col_item_2:
                    max_paginas_itens = st.number_input(
                        "Máximo de páginas de CATMAT",
                        min_value=1,
                        max_value=50,
                        value=20,
                        step=1,
                        key="max_paginas_itens"
                    )

                with col_item_3:
                    max_catmats_default = st.number_input(
                        "Máximo de CATMATs pré-selecionados",
                        min_value=1,
                        max_value=50,
                        value=10,
                        step=1,
                        key="max_catmats_default"
                    )

                if st.button("Carregar CATMATs deste PDM"):
                    with st.spinner("Consultando itens CATMAT do PDM selecionado..."):
                        df_itens, erros_itens, total_itens, paginas_itens, urls_itens = carregar_itens_por_pdm(
                            codigo_pdm=codigo_pdm,
                            somente_ativos=somente_ativos,
                            max_paginas=int(max_paginas_itens)
                        )

                    df_itens_carac = criar_tabela_caracteristicas(df_itens)

                    st.session_state["codigo_pdm_atual"] = codigo_pdm
                    st.session_state["df_itens_carac"] = df_itens_carac
                    st.session_state["erros_itens"] = erros_itens
                    st.session_state["total_itens"] = total_itens
                    st.session_state["paginas_itens"] = paginas_itens
                    st.session_state["urls_itens"] = urls_itens

                    for chave in [
                        "resultados_precos",
                        "erros_precos",
                        "urls_precos"
                    ]:
                        st.session_state.pop(chave, None)

    if "df_itens_carac" in st.session_state:
        df_itens_carac = st.session_state["df_itens_carac"]
        erros_itens = st.session_state.get("erros_itens", [])
        total_itens = st.session_state.get("total_itens")
        paginas_itens = st.session_state.get("paginas_itens")
        urls_itens = st.session_state.get("urls_itens", [])

        st.header("3. Filtros dinâmicos de características")

        st.write("Total de CATMATs informado pela API:", total_itens)
        st.write("Total de páginas informado pela API:", paginas_itens)
        st.write("CATMATs carregados no app:", len(df_itens_carac))

        with st.expander("Diagnóstico da consulta de CATMAT"):
            st.write("Colunas retornadas/criadas:")
            st.write(list(df_itens_carac.columns))
            st.write("URLs consultadas:")
            st.write(urls_itens)

            if not df_itens_carac.empty:
                st.dataframe(df_itens_carac.head(20), use_container_width=True)

        if erros_itens:
            st.warning("A consulta de CATMAT retornou erro em uma das páginas.")
            st.json(erros_itens)

        if df_itens_carac.empty:
            st.warning("Nenhum CATMAT retornado para o PDM selecionado.")
        else:
            candidatas = identificar_colunas_caracteristicas(df_itens_carac)

            st.caption(
                "Os filtros abaixo são extraídos automaticamente da descrição dos CATMATs. "
                "Como a descrição não é uma estrutura perfeita de dados, esta etapa usa heurística textual."
            )

            filtros = {}

            if not candidatas:
                st.warning("Não foram identificadas características estruturáveis na descrição dos itens.")
            else:
                limite_superior = min(20, len(candidatas))

                if limite_superior == 1:
                    max_filtros = 1
                else:
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
                            nome,
                            options=valores,
                            default=[],
                            key=f"filtro_{st.session_state.get('codigo_pdm_atual', 'pdm')}_{coluna}"
                        )

                    filtros[coluna] = selecionados

            df_filtrado = aplicar_filtros_dinamicos(df_itens_carac, filtros)

            st.subheader("4. CATMATs candidatos após filtros")
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
                    df_filtrado[colunas_exibir].head(300),
                    use_container_width=True
                )
            else:
                st.dataframe(
                    df_filtrado.head(300),
                    use_container_width=True
                )

            codigo_item_col = primeira_coluna_existente(
                df_filtrado,
                ["codigoItem", "codigo_item", "codItem", "idItem"]
            )

            if df_filtrado.empty:
                st.warning("Nenhum CATMAT permaneceu após os filtros.")
            elif codigo_item_col is None:
                st.error(
                    "Não foi possível identificar a coluna de código CATMAT. "
                    "Abra o diagnóstico da consulta de CATMAT e verifique os nomes das colunas."
                )
            else:
                max_catmats_default_valor = int(
                    st.session_state.get("max_catmats_default", 10)
                )

                codigos_disponiveis = (
                    df_filtrado[codigo_item_col]
                    .dropna()
                    .astype(int)
                    .drop_duplicates()
                    .tolist()
                )

                codigos_default = codigos_disponiveis[:max_catmats_default_valor]

                codigos_selecionados = st.multiselect(
                    "Selecione os CATMATs para consulta de preços",
                    options=codigos_disponiveis,
                    default=codigos_default,
                    key="codigos_catmat_selecionados"
                )

                st.header("5. Consulta de preços dos CATMATs selecionados")

                col_data_1, col_data_2, col_data_3 = st.columns(3)

                with col_data_1:
                    data_inicial = st.date_input(
                        "Data inicial",
                        value=date(2024, 1, 1),
                        key="dinamica_data_inicial"
                    )

                with col_data_2:
                    data_final = st.date_input(
                        "Data final",
                        value=date.today(),
                        key="dinamica_data_final"
                    )

                with col_data_3:
                    max_paginas_preco = st.number_input(
                        "Máximo de páginas por CATMAT",
                        min_value=1,
                        max_value=10,
                        value=2,
                        step=1,
                        key="dinamica_max_paginas_preco"
                    )

                if st.button("Consultar preços dos CATMATs selecionados"):
                    if not codigos_selecionados:
                        st.warning("Selecione pelo menos um CATMAT.")
                    else:
                        with st.spinner("Consultando preços praticados..."):
                            resultados_precos, erros_precos, urls_precos = consultar_precos_multiplos_catmats(
                                codigos_catmat=codigos_selecionados,
                                data_inicial=data_inicial,
                                data_final=data_final,
                                max_paginas_por_catmat=int(max_paginas_preco)
                            )

                        st.session_state["resultados_precos"] = resultados_precos
                        st.session_state["erros_precos"] = erros_precos
                        st.session_state["urls_precos"] = urls_precos

    if "resultados_precos" in st.session_state:
        resultados_precos = st.session_state["resultados_precos"]
        erros_precos = st.session_state.get("erros_precos", [])
        urls_precos = st.session_state.get("urls_precos", [])

        st.header("6. Resultados consolidados de preços")

        st.write("Registros de preços carregados:", len(resultados_precos))

        with st.expander("URLs consultadas na pesquisa de preços"):
            st.write(urls_precos)

        if erros_precos:
            st.warning("Algumas consultas de preço retornaram erro.")
            st.json(erros_precos)

        if resultados_precos:
            df_precos = pd.DataFrame(resultados_precos)

            st.dataframe(df_precos, use_container_width=True)

            exibir_estatisticas_precos(df_precos)

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

                descricoes = (
                    df_precos[colunas_desc]
                    .drop_duplicates()
                )
                st.dataframe(descricoes, use_container_width=True)

            csv = df_precos.to_csv(index=False).encode("utf-8-sig")

            st.download_button(
                label="Baixar preços consolidados em CSV",
                data=csv,
                file_name="precos_consolidados_catmat.csv",
                mime="text/csv"
            )
        else:
            st.warning("Nenhum preço retornado para os CATMATs selecionados.")


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
        data_inicial = st.date_input(
            "Data inicial",
            value=date(2024, 1, 1),
            key="direto_data_inicial"
        )

    with col5:
        data_final = st.date_input(
            "Data final",
            value=date.today(),
            key="direto_data_final"
        )

    if st.button("Consultar preços praticados", key="direto_botao"):
        try:
            params_base = {
                "codigoItemCatalogo": int(codigo_item),
                "dataCompraMin": data_inicial.strftime("%Y-%m-%d"),
                "dataCompraMax": data_final.strftime("%Y-%m-%d")
            }

            with st.spinner("Consultando Pesquisa de Preços..."):
                resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
                    endpoint="/modulo-pesquisa-preco/1_consultarMaterial",
                    params_base=params_base,
                    tamanho_pagina=int(tamanho_pagina),
                    max_paginas=int(max_paginas),
                    timeout=120
                )

            st.write("Total de registros informado pela API:", total_registros)
            st.write("Total de páginas informado pela API:", total_paginas)
            st.write("Registros carregados no app:", len(resultados))

            with st.expander("URLs consultadas"):
                st.write(urls)

            if erros:
                st.warning("A consulta retornou erro em uma das páginas.")
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
