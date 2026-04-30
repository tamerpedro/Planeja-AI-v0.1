import streamlit as st
import requests
import pandas as pd

st.set_page_config(
    page_title="PlanejaIA",
    layout="wide"
)

st.title("PlanejaIA - Protótipo inicial")
st.write("Consulta simples à base pública do Compras.gov.br")

st.divider()

st.subheader("Consulta de item de material - CATMAT")

tipo_consulta = st.radio(
    "Tipo de consulta",
    ["Por descrição", "Por código CATMAT"],
    horizontal=True
)

url = "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial"

if tipo_consulta == "Por descrição":
    termo = st.text_input("Descrição do item", value="notebook")
else:
    codigo_item = st.text_input("Código do item de material", value="450340")

if st.button("Consultar Compras.gov.br"):
    if tipo_consulta == "Por descrição":
        params = {
            "pagina": 1,
            "tamanhoPagina": 20,
            "descricaoItem": termo
        }
    else:
        params = {
            "pagina": 1,
            "tamanhoPagina": 10,
            "codigoItem": int(codigo_item)
        }

    try:
        response = requests.get(
            url,
            params=params,
            headers={"accept": "application/json"},
            timeout=30
        )

        st.write("Status da requisição:", response.status_code)
        st.write("URL consultada:", response.url)

        if response.status_code == 200:
            data = response.json()
            resultado = data.get("resultado", [])

            st.write("Total de registros:", data.get("totalRegistros"))
            st.write("Total de páginas:", data.get("totalPaginas"))

            if resultado:
                df = pd.DataFrame(resultado)

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

                with st.expander("Ver retorno bruto da API"):
                    st.json(data)

            else:
                st.warning("A API respondeu, mas não retornou registros.")

        else:
            st.error("A API retornou erro.")
            st.text(response.text)

    except Exception as e:
        st.error("Erro ao consultar a API.")
        st.exception(e)
