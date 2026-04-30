import streamlit as st
import requests
import pandas as pd
import time

st.set_page_config(
    page_title="PlanejaIA",
    layout="wide"
)

st.title("PlanejaIA - Protótipo inicial")
st.write("Consulta simples à base pública do Compras.gov.br")

st.divider()

st.subheader("Consulta de item de material - CATMAT")

url = "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial"

tipo_consulta = st.radio(
    "Tipo de consulta",
    ["Por descrição", "Por código CATMAT"],
    horizontal=True
)

col1, col2, col3 = st.columns(3)

with col1:
    if tipo_consulta == "Por descrição":
        termo = st.text_input("Descrição do item", value="microcomputador")
    else:
        codigo_item = st.text_input("Código do item de material", value="450340")

with col2:
    tamanho_pagina = st.number_input(
        "Registros por página",
        min_value=10,
        max_value=100,
        value=50,
        step=10
    )

with col3:
    max_paginas = st.number_input(
        "Máximo de páginas",
        min_value=1,
        max_value=20,
        value=5,
        step=1
    )


def consultar_api(params_base, tamanho_pagina, max_paginas):
    resultados = []
    erros = []

    total_registros = None
    total_paginas = None

    for pagina in range(1, max_paginas + 1):
        params = dict(params_base)
        params["pagina"] = pagina
        params["tamanhoPagina"] = tamanho_pagina

        try:
            response = requests.get(
                url,
                params=params,
                headers={"accept": "application/json"},
                timeout=60
            )

            if response.status_code != 200:
                erros.append({
                    "pagina": pagina,
                    "status": response.status_code,
                    "mensagem": response.text
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

            if pagina >= int(data.get("totalPaginas", pagina)):
                break

            time.sleep(0.3)

        except Exception as e:
            erros.append({
                "pagina": pagina,
                "status": "erro",
                "mensagem": str(e)
            })
            break

    return resultados, erros, total_registros, total_paginas


if st.button("Consultar Compras.gov.br"):
    if tipo_consulta == "Por descrição":
        params_base = {
            "descricaoItem": termo.upper().strip()
        }
    else:
        params_base = {
            "codigoItem": int(codigo_item)
        }

    with st.spinner("Consultando API do Compras.gov.br..."):
        resultados, erros, total_registros, total_paginas = consultar_api(
            params_base=params_base,
            tamanho_pagina=int(tamanho_pagina),
            max_paginas=int(max_paginas)
        )

    st.write("Total de registros informado pela API:", total_registros)
    st.write("Total de páginas informado pela API:", total_paginas)
    st.write("Registros carregados no app:", len(resultados))

    if erros:
        st.warning("A consulta retornou erro em uma das páginas.")
        st.json(erros)

    if resultados:
        df = pd.DataFrame(resultados)

        colunas_exibir = [
            "codigoItem",
            "nomeGrupo",
            "nomeClasse",
            "nomePdm",
            "descricaoItem",
            "statusItem",
            "itemSustentavel",
            "dataHoraAtualizacao"
        ]

        colunas_existentes = [
            coluna for coluna in colunas_exibir if coluna in df.columns
        ]

        st.subheader("Resultado em tabela")
        st.dataframe(
            df[colunas_existentes],
            use_container_width=True
        )

        csv = df.to_csv(index=False).encode("utf-8-sig")

        st.download_button(
            label="Baixar resultado em CSV",
            data=csv,
            file_name="resultado_catmat.csv",
            mime="text/csv"
        )

        with st.expander("Ver dados brutos"):
            st.json(resultados)

    else:
        st.warning("A API respondeu, mas não retornou registros.")
