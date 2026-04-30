import time
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
st.write("Normalização de objeto, seleção de CATMATs candidatos e consulta de preços no Compras.gov.br")

st.divider()


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def consultar_paginas(endpoint, params_base, tamanho_pagina=50, max_paginas=5, timeout=90):
    resultados = []
    erros = []
    total_registros = None
    total_paginas = None
    urls_consultadas = []

    for pagina in range(1, max_paginas + 1):
        params = dict(params_base)
        params["pagina"] = pagina
        params["tamanhoPagina"] = tamanho_pagina

        try:
            response = requests.get(
                f"{BASE_URL}{endpoint}",
                params=params,
                headers={"accept": "application/json"},
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

            time.sleep(0.3)

        except Exception as e:
            erros.append({
                "pagina": pagina,
                "status": "erro",
                "mensagem": str(e)
            })
            break

    return resultados, erros, total_registros, total_paginas, urls_consultadas


def texto_normalizado(valor):
    if pd.isna(valor):
        return ""
    return str(valor).upper().strip()


def contem_todos(texto, termos):
    texto = texto_normalizado(texto)
    return all(t.upper() in texto for t in termos if t)


def filtrar_catmat_notebook(df, filtros):
    df_filtrado = df.copy()

    if "descricaoItem" not in df_filtrado.columns:
        return df_filtrado

    df_filtrado["descricao_norm"] = df_filtrado["descricaoItem"].apply(texto_normalizado)

    # Tela
    if filtros["tela"] != "Não filtrar":
        df_filtrado = df_filtrado[
            df_filtrado["descricao_norm"].str.contains(filtros["tela"].upper(), na=False)
        ]

    # RAM
    if filtros["ram"] != "Não filtrar":
        df_filtrado = df_filtrado[
            df_filtrado["descricao_norm"].str.contains(filtros["ram"].upper(), na=False)
        ]

    # SSD
    if filtros["ssd"] != "Não filtrar":
        df_filtrado = df_filtrado[
            df_filtrado["descricao_norm"].str.contains(filtros["ssd"].upper(), na=False)
        ]

    # Núcleos
    if filtros["nucleos"] != "Não filtrar":
        df_filtrado = df_filtrado[
            df_filtrado["descricao_norm"].str.contains(filtros["nucleos"].upper(), na=False)
        ]

    # Garantia
    if filtros["garantia"] != "Não filtrar":
        df_filtrado = df_filtrado[
            df_filtrado["descricao_norm"].str.contains(filtros["garantia"].upper(), na=False)
        ]

    # Sistema operacional
    if filtros["sistema_operacional"] != "Não filtrar":
        df_filtrado = df_filtrado[
            df_filtrado["descricao_norm"].str.contains(filtros["sistema_operacional"].upper(), na=False)
        ]

    # Somente ativos
    if filtros["somente_ativos"] and "statusItem" in df_filtrado.columns:
        df_filtrado = df_filtrado[df_filtrado["statusItem"] == True]

    return df_filtrado.drop(columns=["descricao_norm"], errors="ignore")


def consultar_precos_multiplos_catmats(codigos_catmat, data_inicial, data_final, max_paginas_por_catmat=2):
    todos_resultados = []
    todos_erros = []
    urls = []

    progresso = st.progress(0)
    total = len(codigos_catmat)

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
            timeout=90
        )

        for item in resultados:
            item["catmat_consultado"] = int(codigo)

        todos_resultados.extend(resultados)
        todos_erros.extend(erros)
        urls.extend(urls_consultadas)

        progresso.progress((i + 1) / total)
        time.sleep(0.3)

    return todos_resultados, todos_erros, urls


def exibir_estatisticas_precos(df):
    st.subheader("Resumo estatístico dos preços")

    coluna_preco = None

    for candidata in ["precoUnitario", "valorUnitario", "preco_unitario"]:
        if candidata in df.columns:
            coluna_preco = candidata
            break

    if coluna_preco is None:
        st.warning("Não foi encontrada coluna de preço unitário no retorno da API.")
        return

    df[coluna_preco] = pd.to_numeric(df[coluna_preco], errors="coerce")
    df_precos = df.dropna(subset=[coluna_preco]).copy()

    if df_precos.empty:
        st.warning("Não há preços válidos para cálculo estatístico.")
        return

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Registros com preço", len(df_precos))
    col2.metric("Menor preço", f"R$ {df_precos[coluna_preco].min():,.2f}")
    col3.metric("Maior preço", f"R$ {df_precos[coluna_preco].max():,.2f}")
    col4.metric("Preço médio", f"R$ {df_precos[coluna_preco].mean():,.2f}")
    col5.metric("Mediana", f"R$ {df_precos[coluna_preco].median():,.2f}")

    st.caption(
        "A média pode ser sensível a outliers. Para planejamento preliminar, a mediana tende a ser uma referência mais robusta."
    )


# ============================================================
# ABAS DO APP
# ============================================================

aba_normalizacao, aba_precos_direto = st.tabs([
    "Normalizar objeto e buscar preços",
    "Consulta direta por CATMAT"
])


# ============================================================
# ABA 1 - NORMALIZAÇÃO DO OBJETO
# ============================================================

with aba_normalizacao:
    st.header("1. Normalização do objeto")

    objeto = st.selectbox(
        "Objeto genérico",
        [
            "Notebook",
            "Monitor",
            "Desktop",
            "Teclado",
            "Mouse"
        ],
        index=0
    )

    if objeto != "Notebook":
        st.warning(
            "Nesta versão inicial, a normalização automática está implementada apenas para Notebook. "
            "Os demais objetos serão adicionados depois."
        )
        st.stop()

    st.info(
        "Para Notebook, será usado inicialmente o PDM 8435, identificado no Catálogo Compras.gov.br."
    )

    codigo_pdm = 8435

    st.subheader("2. Características técnicas mínimas")

    col1, col2, col3 = st.columns(3)

    with col1:
        filtro_tela = st.selectbox(
            "Tela",
            [
                "Não filtrar",
                "SUPERIOR A 14 POL",
                "14 POL",
                "15 POL",
                "16 POL"
            ],
            index=1
        )

        filtro_ram = st.selectbox(
            "Memória RAM",
            [
                "Não filtrar",
                "MÍNIMO DE 8 GB",
                "SUPERIOR A 8 GB",
                "MÍNIMO DE 16 GB",
                "MÍNIMO DE 32 GB",
                "MÍNIMO DE 64 GB"
            ],
            index=3
        )

    with col2:
        filtro_ssd = st.selectbox(
            "Armazenamento SSD",
            [
                "Não filtrar",
                "480 A 1.000 GB",
                "MÍNIMO DE 512 GB",
                "MÍNIMO DE 1 TB",
                "1 TB"
            ],
            index=0
        )

        filtro_nucleos = st.selectbox(
            "Núcleos por processador",
            [
                "Não filtrar",
                "SUPERIOR A 8",
                "MÍNIMO DE 8",
                "MÍNIMO DE 10",
                "MÍNIMO DE 14"
            ],
            index=0
        )

    with col3:
        filtro_garantia = st.selectbox(
            "Garantia on site",
            [
                "Não filtrar",
                "12 MESES",
                "36 MESES",
                "48 MESES",
                "60 MESES"
            ],
            index=2
        )

        filtro_so = st.selectbox(
            "Sistema operacional",
            [
                "Não filtrar",
                "PROPRIETÁRIO",
                "SEM SISTEMA OPERACIONAL"
            ],
            index=1
        )

    somente_ativos = st.checkbox("Somente itens ativos", value=True)

    col_a, col_b, col_c = st.columns(3)

    with col_a:
        tamanho_pagina_catmat = st.number_input(
            "Registros por página no CATMAT",
            min_value=10,
            max_value=100,
            value=100,
            step=10
        )

    with col_b:
        max_paginas_catmat = st.number_input(
            "Máximo de páginas no CATMAT",
            min_value=1,
            max_value=20,
            value=5,
            step=1
        )

    with col_c:
        max_catmats_para_preco = st.number_input(
            "Máximo de CATMATs para consultar preços",
            min_value=1,
            max_value=30,
            value=10,
            step=1
        )

    if st.button("Buscar CATMATs candidatos"):
        params_base = {
            "codigoPdm": codigo_pdm
        }

        with st.spinner("Consultando catálogo CATMAT por PDM..."):
            resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
                endpoint="/modulo-material/4_consultarItemMaterial",
                params_base=params_base,
                tamanho_pagina=int(tamanho_pagina_catmat),
                max_paginas=int(max_paginas_catmat),
                timeout=90
            )

        st.session_state["catmat_resultados_brutos"] = resultados
        st.session_state["catmat_erros"] = erros
        st.session_state["catmat_urls"] = urls
        st.session_state["catmat_total_registros"] = total_registros
        st.session_state["catmat_total_paginas"] = total_paginas

    if "catmat_resultados_brutos" in st.session_state:
        resultados = st.session_state["catmat_resultados_brutos"]
        erros = st.session_state.get("catmat_erros", [])
        urls = st.session_state.get("catmat_urls", [])
        total_registros = st.session_state.get("catmat_total_registros")
        total_paginas = st.session_state.get("catmat_total_paginas")

        st.subheader("3. CATMATs candidatos")

        st.write("Total de registros informado pela API:", total_registros)
        st.write("Total de páginas informado pela API:", total_paginas)
        st.write("Registros carregados no app:", len(resultados))

        with st.expander("URLs consultadas no catálogo"):
            st.write(urls)

        if erros:
            st.warning("A consulta ao catálogo retornou erro em uma das páginas.")
            st.json(erros)

        if resultados:
            df_catmat = pd.DataFrame(resultados)

            filtros = {
                "tela": filtro_tela,
                "ram": filtro_ram,
                "ssd": filtro_ssd,
                "nucleos": filtro_nucleos,
                "garantia": filtro_garantia,
                "sistema_operacional": filtro_so,
                "somente_ativos": somente_ativos
            }

            df_filtrado = filtrar_catmat_notebook(df_catmat, filtros)

            st.write("CATMATs após filtros técnicos:", len(df_filtrado))

            colunas_catmat = [
                "codigoItem",
                "nomePdm",
                "descricaoItem",
                "statusItem",
                "itemSustentavel",
                "dataHoraAtualizacao"
            ]

            colunas_existentes = [c for c in colunas_catmat if c in df_filtrado.columns]

            st.dataframe(
                df_filtrado[colunas_existentes],
                use_container_width=True
            )

            if not df_filtrado.empty:
                codigos_disponiveis = df_filtrado["codigoItem"].dropna().astype(int).tolist()

                codigos_default = codigos_disponiveis[:int(max_catmats_para_preco)]

                codigos_selecionados = st.multiselect(
                    "Selecione os CATMATs para consulta de preços",
                    options=codigos_disponiveis,
                    default=codigos_default
                )

                st.subheader("4. Consulta de preços para os CATMATs selecionados")

                col_data_1, col_data_2, col_paginas = st.columns(3)

                with col_data_1:
                    data_inicial = st.date_input(
                        "Data inicial",
                        value=date(2024, 1, 1),
                        key="normalizacao_data_inicial"
                    )

                with col_data_2:
                    data_final = st.date_input(
                        "Data final",
                        value=date.today(),
                        key="normalizacao_data_final"
                    )

                with col_paginas:
                    max_paginas_preco = st.number_input(
                        "Máximo de páginas por CATMAT",
                        min_value=1,
                        max_value=10,
                        value=2,
                        step=1
                    )

                if st.button("Consultar preços dos CATMATs selecionados"):
                    if not codigos_selecionados:
                        st.warning("Selecione pelo menos um CATMAT.")
                    else:
                        with st.spinner("Consultando preços para múltiplos CATMATs..."):
                            resultados_precos, erros_precos, urls_precos = consultar_precos_multiplos_catmats(
                                codigos_catmat=codigos_selecionados,
                                data_inicial=data_inicial,
                                data_final=data_final,
                                max_paginas_por_catmat=int(max_paginas_preco)
                            )

                        st.session_state["precos_resultados"] = resultados_precos
                        st.session_state["precos_erros"] = erros_precos
                        st.session_state["precos_urls"] = urls_precos

                if "precos_resultados" in st.session_state:
                    resultados_precos = st.session_state["precos_resultados"]
                    erros_precos = st.session_state.get("precos_erros", [])
                    urls_precos = st.session_state.get("precos_urls", [])

                    st.subheader("5. Resultados consolidados de preços")

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

                        if "nomeFornecedor" in df_precos.columns:
                            st.subheader("Fornecedores mais recorrentes")
                            fornecedores = (
                                df_precos["nomeFornecedor"]
                                .fillna("Não informado")
                                .value_counts()
                                .reset_index()
                            )
                            fornecedores.columns = ["Fornecedor", "Ocorrências"]
                            st.dataframe(fornecedores.head(10), use_container_width=True)

                        if "nomeOrgao" in df_precos.columns:
                            st.subheader("Órgãos compradores mais recorrentes")
                            orgaos = (
                                df_precos["nomeOrgao"]
                                .fillna("Não informado")
                                .value_counts()
                                .reset_index()
                            )
                            orgaos.columns = ["Órgão", "Ocorrências"]
                            st.dataframe(orgaos.head(10), use_container_width=True)

                        if "descricaoItem" in df_precos.columns:
                            st.subheader("Descrições de itens encontradas")
                            descricoes = (
                                df_precos[["catmat_consultado", "descricaoItem"]]
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

            else:
                st.warning("Nenhum CATMAT permaneceu após os filtros técnicos.")
        else:
            st.warning("Nenhum CATMAT retornado pela API.")


# ============================================================
# ABA 2 - CONSULTA DIRETA POR CATMAT
# ============================================================

with aba_precos_direto:
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
                timeout=90
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
