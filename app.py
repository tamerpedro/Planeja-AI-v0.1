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

st.subheader("Consulta de teste")

url = "https://dadosabertos.compras.gov.br/modulo-material/4_consultarItemMaterial"

codigo_item = st.text_input(
    "Código do item de material",
    value="461506"
)

if st.button("Consultar Compras.gov.br"):
    params = {
        "codigoItem": codigo_item
    }

    try:
        response = requests.get(url, params=params, timeout=30)

        st.write("Status da requisição:", response.status_code)
        st.write("URL consultada:", response.url)

        if response.status_code == 200:
            data = response.json()

            st.subheader("Retorno bruto da API")
            st.json(data)

            if isinstance(data, dict):
                for chave, valor in data.items():
                    if isinstance(valor, list):
                        st.subheader(f"Tabela: {chave}")
                        st.dataframe(pd.DataFrame(valor))
        else:
            st.error("A API retornou erro.")
            st.text(response.text)

    except Exception as e:
        st.error("Erro ao consultar a API.")
        st.exception(e)
