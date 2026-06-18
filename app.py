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

st.title("PlanejaIA - Consulta de preços públicos")
st.write(
    "Consulta de CATMATs e preços praticados em bases públicas para apoio ao planejamento da contratação."
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


def formatar_percentual(valor):
    if valor is None or pd.isna(valor):
        return "N/A"

    return f"{valor * 100:.2f}%".replace(".", ",")


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
    pular_pagina_com_erro=True,
    pausa_entre_paginas=0.2
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

        if pausa_entre_paginas:
            time.sleep(float(pausa_entre_paginas))

    return resultados, erros, total_registros, total_paginas, urls_consultadas


@st.cache_data(ttl=86400, show_spinner=False)
def carregar_catalogo_pdms(somente_ativos=True, max_paginas=40, tamanho_pagina=500):
    params = {}

    if somente_ativos:
        params["statusPdm"] = True

    resultados, erros, total_registros, total_paginas, urls = consultar_paginas(
        endpoint="/modulo-material/3_consultarPdmMaterial",
        params_base=params,
        tamanho_pagina=int(tamanho_pagina),
        max_paginas=int(max_paginas),
        timeout=10,
        tentativas_por_pagina=1,
        pular_pagina_com_erro=False,
        pausa_entre_paginas=0
    )

    df = pd.DataFrame(resultados)

    if not df.empty and "codigoPdm" in df.columns:
        df = (
            df
            .dropna(subset=["codigoPdm"])
            .drop_duplicates(subset=["codigoPdm"])
            .sort_values(["nomePdm", "codigoPdm"], na_position="last")
            .reset_index(drop=True)
        )

    diagnostico = {
        "total_registros_api": total_registros,
        "total_paginas_api": total_paginas,
        "quantidade_carregada": int(len(df)),
        "erros": erros,
        "urls": urls
    }

    return df, diagnostico


def localizar_pdms_por_termo(df_pdms, termo, limite=50):
    if df_pdms is None or df_pdms.empty:
        return pd.DataFrame()

    termo_normalizado = normalizar_texto(termo)
    tokens = [
        token
        for token in termo_normalizado.split()
        if len(token) >= 2
    ]

    if not tokens:
        return pd.DataFrame()

    df_busca = df_pdms.copy()

    for coluna in ["nomePdm", "nomeClasse", "nomeGrupo"]:
        if coluna not in df_busca.columns:
            df_busca[coluna] = ""

        df_busca[f"{coluna}_normalizado"] = df_busca[coluna].apply(normalizar_texto)

    texto_busca = (
        df_busca["nomePdm_normalizado"]
        + " "
        + df_busca["nomeClasse_normalizado"]
        + " "
        + df_busca["nomeGrupo_normalizado"]
    )

    mask = texto_busca.apply(
        lambda texto: all(token in texto for token in tokens)
    )

    encontrados = df_busca[mask].copy()

    if encontrados.empty:
        return encontrados

    def calcular_pontuacao(linha):
        nome_pdm = linha["nomePdm_normalizado"]
        nome_classe = linha["nomeClasse_normalizado"]
        nome_grupo = linha["nomeGrupo_normalizado"]

        if nome_pdm == termo_normalizado:
            return 0

        if nome_pdm.startswith(termo_normalizado):
            return 1

        if all(token in nome_pdm for token in tokens):
            return 2

        if all(token in nome_classe for token in tokens):
            return 3

        if all(token in nome_grupo for token in tokens):
            return 4

        return 5

    encontrados["pontuacao_busca"] = encontrados.apply(calcular_pontuacao, axis=1)
    encontrados["tamanho_nome_pdm"] = encontrados["nomePdm"].fillna("").astype(str).str.len()

    colunas_exibir = [
        "codigoPdm",
        "nomePdm",
        "codigoClasse",
        "nomeClasse",
        "codigoGrupo",
        "nomeGrupo",
        "statusPdm",
        "dataHoraAtualizacao"
    ]
    colunas_exibir = [coluna for coluna in colunas_exibir if coluna in encontrados.columns]

    return (
        encontrados
        .sort_values(["pontuacao_busca", "tamanho_nome_pdm", "nomePdm"], na_position="last")
        .head(int(limite))[colunas_exibir]
        .reset_index(drop=True)
    )


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

    preco_medio = float(df_precos[coluna_preco].mean())
    desvio_padrao = float(df_precos[coluna_preco].std(ddof=0))
    limite_superior = preco_medio + desvio_padrao
    limite_inferior = preco_medio - desvio_padrao
    coeficiente_variacao = (
        desvio_padrao / preco_medio
        if preco_medio > 0
        else None
    )
    desvio_acima_25 = (
        coeficiente_variacao > 0.25
        if coeficiente_variacao is not None
        else None
    )

    estatisticas = {
        "registros_com_preco": int(len(df_precos)),
        "menor_preco": float(df_precos[coluna_preco].min()),
        "maior_preco": float(df_precos[coluna_preco].max()),
        "preco_medio": preco_medio,
        "mediana": float(df_precos[coluna_preco].median()),
        "desvio_padrao": desvio_padrao,
        "limite_superior": limite_superior,
        "limite_inferior": limite_inferior,
        "coeficiente_variacao": coeficiente_variacao,
        "desvio_acima_25": desvio_acima_25
    }

    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric("Registros com preço", estatisticas["registros_com_preco"])
    col2.metric("Menor preço", formatar_brl(estatisticas["menor_preco"]))
    col3.metric("Maior preço", formatar_brl(estatisticas["maior_preco"]))
    col4.metric("Preço médio", formatar_brl(estatisticas["preco_medio"]))
    col5.metric("Mediana", formatar_brl(estatisticas["mediana"]))

    col6, col7, col8, col9 = st.columns(4)

    col6.metric("Desvio padrão", formatar_brl(estatisticas["desvio_padrao"]))
    col7.metric("Limite superior", formatar_brl(estatisticas["limite_superior"]))
    col8.metric("Limite inferior", formatar_brl(estatisticas["limite_inferior"]))
    col9.metric(
        "Desvio vs preço médio",
        formatar_percentual(estatisticas["coeficiente_variacao"])
    )

    if estatisticas["desvio_acima_25"] is None:
        st.info(
            "Não foi possível comparar o desvio padrão com 25% do preço médio, pois o preço médio é zero ou inválido."
        )
    elif estatisticas["desvio_acima_25"]:
        st.warning(
            "Indicador de dispersão: o desvio padrão está acima de 25% do preço médio."
        )
    else:
        st.success(
            "Indicador de dispersão: o desvio padrão está abaixo ou igual a 25% do preço médio."
        )

    st.caption(
        "A média pode ser distorcida por outliers. Para planejamento preliminar, "
        "a mediana tende a ser uma referência mais robusta. Os limites inferior e "
        "superior correspondem à média menos/mais um desvio padrão."
    )

    return estatisticas


# ============================================================
# INTERFACE
# ============================================================

aba_principal, aba_direta = st.tabs([
    "Consulta assistida por PDM",
    "Consulta direta por CATMAT"
])


# ============================================================
# ABA PRINCIPAL
# ============================================================

with aba_principal:
    st.session_state.setdefault("codigo_pdm_manual", 8435)
    st.session_state.setdefault("nome_pdm_manual", "Notebook")

    st.header("1. Localizar PDM")

    col_busca_pdm_1, col_busca_pdm_2, col_busca_pdm_3 = st.columns([3, 1, 1])

    with col_busca_pdm_1:
        termo_busca_pdm = st.text_input(
            "Buscar PDM por nome, classe ou grupo",
            value="notebook",
            placeholder="Ex.: notebook, monitor, impressora, licença",
            key="termo_busca_pdm"
        )

    with col_busca_pdm_2:
        limite_resultados_pdm = st.number_input(
            "Resultados",
            min_value=5,
            max_value=100,
            value=25,
            step=5,
            key="limite_resultados_pdm"
        )

    with col_busca_pdm_3:
        st.write("")
        st.write("")
        buscar_pdm = st.button("Buscar PDM")

    if buscar_pdm:
        if len(normalizar_texto(termo_busca_pdm)) < 2:
            st.session_state.pop("df_pdms_encontrados", None)
            st.session_state.pop("diagnostico_pdms", None)
            st.warning("Informe pelo menos dois caracteres para buscar PDM.")
        else:
            with st.spinner("Carregando catálogo de PDMs e filtrando resultados..."):
                df_catalogo_pdms, diagnostico_pdms = carregar_catalogo_pdms(
                    somente_ativos=True,
                    max_paginas=40,
                    tamanho_pagina=500
                )

                df_pdms_encontrados = localizar_pdms_por_termo(
                    df_catalogo_pdms,
                    termo_busca_pdm,
                    limite=int(limite_resultados_pdm)
                )

            st.session_state["df_pdms_encontrados"] = df_pdms_encontrados
            st.session_state["diagnostico_pdms"] = diagnostico_pdms

    df_pdms_encontrados = st.session_state.get("df_pdms_encontrados")

    if df_pdms_encontrados is not None:
        diagnostico_pdms = st.session_state.get("diagnostico_pdms", {})

        if df_pdms_encontrados.empty:
            if diagnostico_pdms.get("erros"):
                st.error("Não foi possível carregar o catálogo de PDMs na API do Compras.gov.br.")
            else:
                st.warning("Nenhum PDM encontrado para o termo informado.")
        else:
            st.success(f"{len(df_pdms_encontrados)} PDM(s) encontrado(s).")

            def formatar_opcao_pdm(indice):
                linha = df_pdms_encontrados.iloc[indice]
                return (
                    f"{int(linha['codigoPdm'])} - {linha.get('nomePdm', '')} "
                    f"| Classe {linha.get('codigoClasse', '')} - {linha.get('nomeClasse', '')}"
                )

            indice_pdm = st.selectbox(
                "Selecione o PDM localizado",
                options=list(range(len(df_pdms_encontrados))),
                format_func=formatar_opcao_pdm,
                key="indice_pdm_localizado"
            )

            col_usar_pdm_1, col_usar_pdm_2 = st.columns([1, 4])

            with col_usar_pdm_1:
                if st.button("Usar este PDM"):
                    linha_pdm = df_pdms_encontrados.iloc[int(indice_pdm)]
                    limpar_session_state_catmat()
                    st.session_state["codigo_pdm_manual"] = int(linha_pdm["codigoPdm"])
                    st.session_state["nome_pdm_manual"] = str(linha_pdm.get("nomePdm", ""))
                    st.rerun()

            with col_usar_pdm_2:
                st.caption(
                    "Ao usar um PDM localizado, os CATMATs e preços carregados anteriormente são limpos."
                )

            colunas_pdm = [
                "codigoPdm",
                "nomePdm",
                "codigoClasse",
                "nomeClasse",
                "codigoGrupo",
                "nomeGrupo"
            ]
            colunas_pdm = [coluna for coluna in colunas_pdm if coluna in df_pdms_encontrados.columns]
            st.dataframe(df_pdms_encontrados[colunas_pdm], use_container_width=True, hide_index=True)

        with st.expander("Diagnóstico da busca de PDM"):
            st.json(diagnostico_pdms)

    st.header("2. Confirmar PDM")

    col_pdm_1, col_pdm_2 = st.columns(2)

    with col_pdm_1:
        codigo_pdm = st.number_input(
            "Código PDM",
            min_value=1,
            step=1,
            key="codigo_pdm_manual"
        )

    with col_pdm_2:
        nome_pdm_informado = st.text_input(
            "Nome do PDM, opcional",
            key="nome_pdm_manual"
        )

    st.caption(
        "Você pode usar o localizador acima ou informar o código manualmente. "
        "Exemplo: Notebook = 8435; Tesoura = 249."
    )

    st.header("3. Carregar CATMATs do PDM")

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

        st.header("4. Filtros dinâmicos por características do PDM")

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

            st.header("5. CATMATs candidatos após filtros")
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

                st.header("6. Consulta de preços")

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

        st.header("7. Resultados consolidados de preços")

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

                descricoes = df_precos[colunas_desc].drop_duplicates()
                st.dataframe(descricoes.head(100), use_container_width=True)

            csv = df_precos.to_csv(index=False).encode("utf-8-sig")

            st.download_button(
                label="Baixar preços consolidados em CSV",
                data=csv,
                file_name="precos_consolidados_catmat.csv",
                mime="text/csv"
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
