import streamlit as st
import requests
import pandas as pd
import time
from datetime import date

st.set_page_config(
    page_title="PlanejaIA",
    layout="wide"
)

st.title("PlanejaIA - Protótipo inicial")
st.write("Consulta inicial aos dados públicos do Compras.gov.br")

st.divider()

BASE_URL = "https://dadosabertos.compras.gov.br"


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

            time.sleep(0.4)

        except Exception as e:
            erros.append({
                "pagina": pagina,
                "status": "erro",
                "mensagem": str(e)
            })
            break

    return resultados, erros, total_registros, total_paginas, urls_consultadas


aba_precos, aba_catmat = st.tabs([
    "Pesquisa de preços por CATMAT",
    "Teste de catálogo CATMAT"
])


with aba_precos:
    st.subheader("Pesquisa de preços praticados")

    st.info(
        "Nesta versão mínima, informe diretamente o código CATMAT. "
        "A busca textual livre será tratada depois."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        codigo_item = st.text_input(
            "Código CATMAT",
            value="450340"
        )

    with col2:
        tamanho_pagina = st.number_input(
            "Registros por página",
            min_value=10,
            max_value=100,
            value=50,
            step=10,
            key="preco_tamanho"
        )

    with col3:
        max_paginas = st.number_input(
            "Máximo de páginas",
            min_value=1,
            max_value=20,
            value=5,
            step=1,
            key="preco_paginas"
        )

    col4, col5 = st.columns(2)

    with col4:
        data_inicial = st.date_input(
            "Data inicial",
            value=date(2024, 1, 1)
        )

    with col5:
        data_final = st.date_input(
            "Data final",
            value=date.today()
        )

    if st.button("Consultar preços praticados"):
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


with aba_catmat:
    st.subheader("Consulta direta ao catálogo CATMAT")

    st.info(
        "Use esta aba apenas para testar um código CATMAT conhecido. "
        "A consulta textual por descrição neste endpoint tem comportamento instável."
    )

    codigo_catmat = st.text_input(
        "Código do item de material",
        value="450340",
        key="catmat_codigo"
    )

    if st.button("Consultar item CATMAT"):
        params_base = {
            "codigoItem": int(codigo_catmat)
        }

        with st.spinner("Consultando catálogo CATMAT..."):
            resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
                endpoint="/modulo-material/4_consultarItemMaterial",
                params_base=params_base,
                tamanho_pagina=10,
                max_paginas=1,
                timeout=60
            )

        st.write("Total de registros informado pela API:", total_registros)
        st.write("Registros carregados no app:", len(resultados))

        with st.expander("URLs consultadas"):
            st.write(urls)

        if erros:
            st.warning("A consulta retornou erro.")
            st.json(erros)

        if resultados:
            df = pd.DataFrame(resultados)
            st.dataframe(df, use_container_width=True)

            with st.expander("Ver dados brutos"):
                st.json(resultados)

        else:
            st.warning("Nenhum registro retornado.")
