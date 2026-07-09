# Teste de sincronização GitHub: comentário temporário no cabeçalho do app.
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import BytesIO

import pandas as pd
import requests
import streamlit as st


# ============================================================
# CONFIGURAÇÃO GERAL
# ============================================================

st.set_page_config(
    page_title="Planeja AI",
    layout="wide"
)

BASE_URL = "https://dadosabertos.compras.gov.br"
TIPO_MATERIAL = "Produto/material (CATMAT)"
TIPO_SERVICO = "Serviço (CATSER)"
STOPWORDS_BUSCA = {
    "A",
    "AS",
    "AO",
    "AOS",
    "COM",
    "DA",
    "DAS",
    "DE",
    "DO",
    "DOS",
    "E",
    "EM",
    "NA",
    "NAS",
    "NO",
    "NOS",
    "O",
    "OS",
    "OU",
    "PARA",
    "POR"
}

st.title("Planeja AI - Consulta de preços públicos")
st.write(
    "Consulta de CATMAT/CATSER e preços praticados em bases públicas para apoio a pesquisa de preços."
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


def extrair_tokens_busca(termo):
    termo_normalizado = normalizar_texto(termo)

    return [
        token
        for token in termo_normalizado.split()
        if len(token) >= 2 and token not in STOPWORDS_BUSCA
    ]


def parsear_codigos_catalogo(texto):
    codigos = []
    valores_invalidos = []

    for parte in re.split(r"[,;\s]+", str(texto or "").strip()):
        if not parte:
            continue

        if parte.isdigit() and int(parte) > 0:
            codigos.append(int(parte))
        else:
            valores_invalidos.append(parte)

    return list(dict.fromkeys(codigos)), valores_invalidos


def primeira_coluna_existente(df, candidatas):
    if df is None or df.empty:
        return None

    for coluna in candidatas:
        if coluna in df.columns:
            return coluna

    return None


def ajustar_larguras_excel(writer):
    for worksheet in writer.book.worksheets:
        worksheet.freeze_panes = "A2"

        for coluna in worksheet.columns:
            valores = [
                "" if celula.value is None else str(celula.value)
                for celula in coluna
            ]
            largura = min(max(len(valor) for valor in valores) + 2, 60)
            worksheet.column_dimensions[coluna[0].column_letter].width = max(largura, 12)


def gerar_excel_precos_consolidados(
    df_precos,
    df_precos_analise,
    diagnostico_precos,
    urls_precos
):
    arquivo = BytesIO()

    with pd.ExcelWriter(arquivo, engine="openpyxl") as writer:
        df_precos.to_excel(
            writer,
            sheet_name="precos_consolidados",
            index=False
        )

        df_precos_analise.to_excel(
            writer,
            sheet_name="precos_para_analise",
            index=False
        )

        if diagnostico_precos:
            pd.DataFrame(diagnostico_precos).to_excel(
                writer,
                sheet_name="diagnostico",
                index=False
            )

        if urls_precos:
            pd.DataFrame({"url": urls_precos}).to_excel(
                writer,
                sheet_name="urls_consultadas",
                index=False
            )

        ajustar_larguras_excel(writer)

    arquivo.seek(0)
    return arquivo.getvalue()


def limpar_session_state_precos():
    for chave in [
        "resultados_precos",
        "erros_precos",
        "urls_precos",
        "df_precos",
        "codigos_catmat_consultados",
        "codigos_catalogo_consultados",
        "tipo_catalogo_precos",
        "coluna_codigo_consultado_precos",
        "arquivo_csv_precos",
        "diagnostico_precos_por_codigo",
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


def limpar_session_state_servicos():
    for chave in [
        "df_servicos_encontrados",
        "diagnostico_servicos",
        "codigo_servico_atual",
        "nome_servico_atual"
    ]:
        st.session_state.pop(chave, None)

    for chave in list(st.session_state.keys()):
        if chave.startswith("filtro_servico_") or chave.startswith("catser_selecionados_"):
            st.session_state.pop(chave, None)

    st.session_state.pop("catser_adicionais_servico", None)

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

    def consultar_pagina_pdm(pagina):
        params_pagina = dict(params)
        params_pagina["pagina"] = int(pagina)
        params_pagina["tamanhoPagina"] = int(tamanho_pagina)

        try:
            response = consultar_endpoint(
                endpoint="/modulo-material/3_consultarPdmMaterial",
                params=params_pagina,
                timeout=15
            )

            if response.status_code != 200:
                return {
                    "resultado": [],
                    "total_registros": None,
                    "total_paginas": None,
                    "url": response.url,
                    "erro": {
                        "pagina": pagina,
                        "status": response.status_code,
                        "mensagem": response.text,
                        "url": response.url
                    }
                }

            data = response.json()

            return {
                "resultado": data.get("resultado", []),
                "total_registros": data.get("totalRegistros"),
                "total_paginas": data.get("totalPaginas"),
                "url": response.url,
                "erro": None
            }

        except Exception as e:
            return {
                "resultado": [],
                "total_registros": None,
                "total_paginas": None,
                "url": f"{BASE_URL}/modulo-material/3_consultarPdmMaterial",
                "erro": {
                    "pagina": pagina,
                    "status": "erro",
                    "mensagem": str(e),
                    "url": f"{BASE_URL}/modulo-material/3_consultarPdmMaterial"
                }
            }

    primeira_pagina = consultar_pagina_pdm(1)
    resultados = list(primeira_pagina["resultado"])
    erros = []
    urls = [primeira_pagina["url"]]
    total_registros = primeira_pagina["total_registros"]
    total_paginas = primeira_pagina["total_paginas"]

    if primeira_pagina["erro"]:
        erros.append(primeira_pagina["erro"])

    paginas_a_consultar = []

    if not erros:
        try:
            paginas_disponiveis = int(total_paginas) if total_paginas is not None else int(max_paginas)
        except Exception:
            paginas_disponiveis = int(max_paginas)

        total_paginas_consulta = min(paginas_disponiveis, int(max_paginas))
        paginas_a_consultar = list(range(2, total_paginas_consulta + 1))

    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {
            executor.submit(consultar_pagina_pdm, pagina): pagina
            for pagina in paginas_a_consultar
        }

        for futuro in as_completed(futuros):
            retorno = futuro.result()
            resultados.extend(retorno["resultado"])
            urls.append(retorno["url"])

            if retorno["erro"]:
                erros.append(retorno["erro"])

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
    tokens = extrair_tokens_busca(termo)

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


@st.cache_data(ttl=86400, show_spinner=False)
def carregar_catalogo_servicos(somente_ativos=True, max_paginas=10, tamanho_pagina=500):
    params = {}

    if somente_ativos:
        params["statusServico"] = True

    def consultar_pagina_servico(pagina):
        params_pagina = dict(params)
        params_pagina["pagina"] = int(pagina)
        params_pagina["tamanhoPagina"] = int(tamanho_pagina)

        try:
            response = consultar_endpoint(
                endpoint="/modulo-servico/6_consultarItemServico",
                params=params_pagina,
                timeout=15
            )

            if response.status_code != 200:
                return {
                    "resultado": [],
                    "total_registros": None,
                    "total_paginas": None,
                    "url": response.url,
                    "erro": {
                        "pagina": pagina,
                        "status": response.status_code,
                        "mensagem": response.text,
                        "url": response.url
                    }
                }

            data = response.json()

            return {
                "resultado": data.get("resultado", []),
                "total_registros": data.get("totalRegistros"),
                "total_paginas": data.get("totalPaginas"),
                "url": response.url,
                "erro": None
            }

        except Exception as e:
            return {
                "resultado": [],
                "total_registros": None,
                "total_paginas": None,
                "url": f"{BASE_URL}/modulo-servico/6_consultarItemServico",
                "erro": {
                    "pagina": pagina,
                    "status": "erro",
                    "mensagem": str(e),
                    "url": f"{BASE_URL}/modulo-servico/6_consultarItemServico"
                }
            }

    primeira_pagina = consultar_pagina_servico(1)
    resultados = list(primeira_pagina["resultado"])
    erros = []
    urls = [primeira_pagina["url"]]
    total_registros = primeira_pagina["total_registros"]
    total_paginas = primeira_pagina["total_paginas"]

    if primeira_pagina["erro"]:
        erros.append(primeira_pagina["erro"])

    paginas_a_consultar = []

    if not erros:
        try:
            paginas_disponiveis = int(total_paginas) if total_paginas is not None else int(max_paginas)
        except Exception:
            paginas_disponiveis = int(max_paginas)

        total_paginas_consulta = min(paginas_disponiveis, int(max_paginas))
        paginas_a_consultar = list(range(2, total_paginas_consulta + 1))

    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {
            executor.submit(consultar_pagina_servico, pagina): pagina
            for pagina in paginas_a_consultar
        }

        for futuro in as_completed(futuros):
            retorno = futuro.result()
            resultados.extend(retorno["resultado"])
            urls.append(retorno["url"])

            if retorno["erro"]:
                erros.append(retorno["erro"])

    df = pd.DataFrame(resultados)

    if not df.empty and "codigoServico" in df.columns:
        df = (
            df
            .dropna(subset=["codigoServico"])
            .drop_duplicates(subset=["codigoServico"])
            .sort_values(["nomeServico", "codigoServico"], na_position="last")
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


def localizar_servicos_por_termo(df_servicos, termo, limite=50):
    if df_servicos is None or df_servicos.empty:
        return pd.DataFrame()

    termo_normalizado = normalizar_texto(termo)
    tokens = extrair_tokens_busca(termo)

    if not tokens:
        return pd.DataFrame()

    df_busca = df_servicos.copy()

    for coluna in ["nomeServico", "nomeClasse", "nomeGrupo", "nomeDivisao", "nomeSecao"]:
        if coluna not in df_busca.columns:
            df_busca[coluna] = ""

        df_busca[f"{coluna}_normalizado"] = df_busca[coluna].apply(normalizar_texto)

    texto_busca = (
        df_busca["nomeServico_normalizado"]
        + " "
        + df_busca["nomeClasse_normalizado"]
        + " "
        + df_busca["nomeGrupo_normalizado"]
        + " "
        + df_busca["nomeDivisao_normalizado"]
        + " "
        + df_busca["nomeSecao_normalizado"]
    )

    mask = texto_busca.apply(
        lambda texto: all(token in texto for token in tokens)
    )

    encontrados = df_busca[mask].copy()

    if encontrados.empty:
        return encontrados

    def calcular_pontuacao(linha):
        nome_servico = linha["nomeServico_normalizado"]
        nome_classe = linha["nomeClasse_normalizado"]
        nome_grupo = linha["nomeGrupo_normalizado"]

        if nome_servico == termo_normalizado:
            return 0

        if nome_servico.startswith(termo_normalizado):
            return 1

        if all(token in nome_servico for token in tokens):
            return 2

        if all(token in nome_classe for token in tokens):
            return 3

        if all(token in nome_grupo for token in tokens):
            return 4

        return 5

    encontrados["pontuacao_busca"] = encontrados.apply(calcular_pontuacao, axis=1)
    encontrados["tamanho_nome_servico"] = encontrados["nomeServico"].fillna("").astype(str).str.len()

    colunas_exibir = [
        "codigoServico",
        "nomeServico",
        "codigoClasse",
        "nomeClasse",
        "codigoGrupo",
        "nomeGrupo",
        "statusServico",
        "dataHoraAtualizacao"
    ]
    colunas_exibir = [coluna for coluna in colunas_exibir if coluna in encontrados.columns]

    return (
        encontrados
        .sort_values(["pontuacao_busca", "tamanho_nome_servico", "nomeServico"], na_position="last")
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


def consultar_precos_multiplos_itens_catalogo(
    codigos_itens,
    data_inicial,
    data_final,
    tipo_catalogo="material",
    max_paginas_por_item=2,
    tamanho_pagina=50,
    retornar_diagnostico=False
):
    todos_resultados = []
    todos_erros = []
    urls = []
    diagnosticos = []

    total = len(codigos_itens)

    if total == 0:
        if retornar_diagnostico:
            return todos_resultados, todos_erros, urls, diagnosticos

        return todos_resultados, todos_erros, urls

    if tipo_catalogo == "servico":
        endpoint = "/modulo-pesquisa-preco/3_consultarServico"
        data_inicio_param = "dataCompraInicio"
        data_fim_param = "dataCompraFim"
        coluna_codigo_consultado = "catser_consultado"
    else:
        endpoint = "/modulo-pesquisa-preco/1_consultarMaterial"
        data_inicio_param = "dataCompraInicio"
        data_fim_param = "dataCompraFim"
        coluna_codigo_consultado = "catmat_consultado"

    progresso = st.progress(0)

    for i, codigo in enumerate(codigos_itens):
        params_base = {
            "codigoItemCatalogo": int(codigo)
        }
        adicionar_parametros_data(
            params_base,
            data_inicio_param,
            data_fim_param,
            data_inicial,
            data_final
        )

        resultados, erros, total_registros, total_paginas, urls_consultadas = consultar_paginas(
            endpoint=endpoint,
            params_base=params_base,
            tamanho_pagina=int(tamanho_pagina),
            max_paginas=int(max_paginas_por_item),
            timeout=120,
            tentativas_por_pagina=3,
            pular_pagina_com_erro=True
        )

        consulta_ampliada = False
        data_inicio_api = data_inicial
        data_fim_api = data_final
        url_consulta_usada = urls_consultadas[0] if urls_consultadas else None
        registros_retornados_api = len(resultados)
        resultados, removidos_por_periodo = filtrar_resultados_por_data_compra(
            resultados,
            data_inicial,
            data_final
        )

        if not resultados:
            for data_inicio_fallback, data_fim_fallback in criar_janelas_consulta_ampliada(
                data_inicial,
                data_final
            ):
                params_fallback = {
                    "codigoItemCatalogo": int(codigo)
                }
                adicionar_parametros_data(
                    params_fallback,
                    data_inicio_param,
                    data_fim_param,
                    data_inicio_fallback,
                    data_fim_fallback
                )

                (
                    resultados_fallback,
                    erros_fallback,
                    total_registros_fallback,
                    total_paginas_fallback,
                    urls_fallback
                ) = consultar_paginas(
                    endpoint=endpoint,
                    params_base=params_fallback,
                    tamanho_pagina=int(tamanho_pagina),
                    max_paginas=int(max_paginas_por_item),
                    timeout=120,
                    tentativas_por_pagina=3,
                    pular_pagina_com_erro=True
                )

                resultados_fallback_filtrados, removidos_fallback = filtrar_resultados_por_data_compra(
                    resultados_fallback,
                    data_inicial,
                    data_final
                )

                if resultados_fallback_filtrados:
                    resultados = resultados_fallback_filtrados
                    erros.extend(erros_fallback)
                    urls_consultadas.extend(urls_fallback)
                    total_registros = total_registros_fallback
                    total_paginas = total_paginas_fallback
                    registros_retornados_api = len(resultados_fallback)
                    removidos_por_periodo = removidos_fallback
                    consulta_ampliada = True
                    data_inicio_api = data_inicio_fallback
                    data_fim_api = data_fim_fallback
                    url_consulta_usada = urls_fallback[0] if urls_fallback else url_consulta_usada
                    break

        diagnosticos.append({
            "tipo": "CATSER" if tipo_catalogo == "servico" else "CATMAT",
            "codigo": int(codigo),
            "totalRegistrosAPI": total_registros,
            "totalPaginasAPI": total_paginas,
            "registrosRetornadosNasPaginas": registros_retornados_api,
            "registrosAposFiltroData": len(resultados),
            "removidosPorFiltroData": removidos_por_periodo,
            "consultaAmpliada": consulta_ampliada,
            "dataInicioConsultaAPI": formatar_data_diagnostico(data_inicio_api),
            "dataFimConsultaAPI": formatar_data_diagnostico(data_fim_api),
            "urlPrimeiraPagina": url_consulta_usada
        })

        for item in resultados:
            item[coluna_codigo_consultado] = int(codigo)

        todos_resultados.extend(resultados)
        todos_erros.extend(erros)
        urls.extend(urls_consultadas)

        progresso.progress((i + 1) / total)
        time.sleep(0.2)

    if retornar_diagnostico:
        return todos_resultados, todos_erros, urls, diagnosticos

    return todos_resultados, todos_erros, urls


def adicionar_parametros_data(params, data_inicio_param, data_fim_param, data_inicial, data_final):
    if data_inicial is not None:
        params[data_inicio_param] = data_inicial.strftime("%Y-%m-%d")

    if data_final is not None:
        params[data_fim_param] = data_final.strftime("%Y-%m-%d")


def formatar_data_diagnostico(data_valor):
    if data_valor is None:
        return "sem data"

    return data_valor.strftime("%Y-%m-%d")


def criar_janelas_consulta_ampliada(data_inicial, data_final):
    data_inicial_ref = pd.to_datetime(data_inicial).date() if data_inicial is not None else None
    data_final_ref = pd.to_datetime(data_final).date() if data_final is not None else date.today()
    data_final_ampliada = max(data_final_ref, date.today())
    janelas = []

    if data_inicial_ref is None:
        anos_inicio = [
            data_final_ref.year,
            data_final_ref.year - 1,
            2024,
            2021,
            2018
        ]
    else:
        anos_inicio = [
            data_inicial_ref.year,
            data_inicial_ref.year - 1,
            data_inicial_ref.year - 2,
            2024,
            2021
        ]

    finais = [data_final_ref]

    if data_final_ampliada != data_final_ref:
        finais.append(data_final_ampliada)

    for ano in anos_inicio:
        if ano < 2000:
            continue

        data_inicio_ampliada = date(ano, 1, 1)

        for data_fim_ampliada in finais:
            if data_inicio_ampliada == data_inicial_ref and data_fim_ampliada == data_final_ref:
                continue

            janela = (data_inicio_ampliada, data_fim_ampliada)

            if janela not in janelas:
                janelas.append(janela)

    return janelas


def filtrar_resultados_por_data_compra(resultados, data_inicial, data_final):
    if not resultados:
        return [], 0

    if data_inicial is None and data_final is None:
        return resultados, 0

    data_inicial_ref = pd.to_datetime(data_inicial).date() if data_inicial is not None else None
    data_final_ref = pd.to_datetime(data_final).date() if data_final is not None else None
    filtrados = []

    for item in resultados:
        data_compra = pd.to_datetime(item.get("dataCompra"), errors="coerce")

        if pd.isna(data_compra):
            continue

        data_compra_ref = data_compra.date()

        if data_inicial_ref is not None and data_compra_ref < data_inicial_ref:
            continue

        if data_final_ref is not None and data_compra_ref > data_final_ref:
            continue

        filtrados.append(item)

    return filtrados, len(resultados) - len(filtrados)


def consultar_precos_multiplos_catmats(
    codigos_catmat,
    data_inicial,
    data_final,
    max_paginas_por_catmat=2,
    tamanho_pagina=50
):
    return consultar_precos_multiplos_itens_catalogo(
        codigos_itens=codigos_catmat,
        data_inicial=data_inicial,
        data_final=data_final,
        tipo_catalogo="material",
        max_paginas_por_item=max_paginas_por_catmat,
        tamanho_pagina=tamanho_pagina
    )


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


def criar_tabela_caracteristicas_servicos(df_servicos):
    if df_servicos is None or df_servicos.empty:
        return pd.DataFrame()

    registros = []

    for _, row in df_servicos.iterrows():
        base = row.to_dict()

        nome_grupo = str(row.get("nomeGrupo", "")).strip()
        nome_classe = str(row.get("nomeClasse", "")).strip()
        nome_servico = str(row.get("nomeServico", "")).strip()

        if nome_grupo:
            base["CARAC_GRUPO"] = nome_grupo.upper()

        if nome_classe:
            base["CARAC_CLASSE"] = nome_classe.upper()

        partes = [
            parte.strip().upper()
            for parte in re.split(r"\s+-\s+", nome_servico)
            if parte and parte.strip()
        ]

        if partes:
            base["CARAC_SERVICO_BASE"] = partes[0]

            for indice, parte in enumerate(partes[1:6], start=1):
                base[f"CARAC_DETALHE_{indice}"] = parte

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

def normalizar_valor_exibicao(valor):
    if valor is None or pd.isna(valor):
        return ""

    texto = str(valor).strip()

    if texto.lower() in ["", "nan", "none", "null"]:
        return ""

    return texto


def normalizar_capacidade_unidade(valor):
    texto = normalizar_valor_exibicao(valor)

    if not texto:
        return ""

    try:
        numero = float(texto.replace(",", "."))
    except Exception:
        return texto

    if numero == 0:
        return ""

    if numero.is_integer():
        return str(int(numero))

    return str(numero).replace(".", ",")


def montar_rotulo_unidade_fornecimento(linha, colunas):
    sigla_fornecimento = normalizar_valor_exibicao(
        linha.get(colunas.get("sigla_fornecimento"))
    )
    nome_fornecimento = normalizar_valor_exibicao(
        linha.get(colunas.get("nome_fornecimento"))
    )
    capacidade = normalizar_capacidade_unidade(
        linha.get(colunas.get("capacidade_fornecimento"))
    )
    sigla_medida = normalizar_valor_exibicao(
        linha.get(colunas.get("sigla_medida"))
    )
    nome_medida = normalizar_valor_exibicao(
        linha.get(colunas.get("nome_medida"))
    )

    partes = []

    if sigla_fornecimento and nome_fornecimento:
        partes.append(f"{sigla_fornecimento} - {nome_fornecimento}")
    elif nome_fornecimento:
        partes.append(nome_fornecimento)
    elif sigla_fornecimento:
        partes.append(sigla_fornecimento)

    if capacidade:
        medida = sigla_medida or nome_medida
        partes.append(
            f"Capacidade: {capacidade} {medida}".strip()
        )
    elif sigla_medida or nome_medida:
        partes.append(f"Medida: {sigla_medida or nome_medida}")

    return " | ".join(partes) if partes else "Unidade não informada"


def aplicar_filtro_unidade_fornecimento(df, chave):
    colunas = {
        "sigla_fornecimento": primeira_coluna_existente(
            df,
            ["siglaUnidadeFornecimento", "sigla_unidade_fornecimento"]
        ),
        "nome_fornecimento": primeira_coluna_existente(
            df,
            ["nomeUnidadeFornecimento", "nome_unidade_fornecimento"]
        ),
        "capacidade_fornecimento": primeira_coluna_existente(
            df,
            ["capacidadeUnidadeFornecimento", "capacidade_unidade_fornecimento"]
        ),
        "sigla_medida": primeira_coluna_existente(
            df,
            ["siglaUnidadeMedida", "sigla_unidade_medida"]
        ),
        "nome_medida": primeira_coluna_existente(
            df,
            ["nomeUnidadeMedida", "nome_unidade_medida"]
        )
    }

    if not any(colunas.values()):
        return df

    coluna_unidade_resumo = "_unidade_fornecimento_resumo"
    df_unidades = df.copy()
    df_unidades[coluna_unidade_resumo] = df_unidades.apply(
        lambda linha: montar_rotulo_unidade_fornecimento(linha, colunas),
        axis=1
    )

    contagens = (
        df_unidades[coluna_unidade_resumo]
        .value_counts(dropna=False)
        .sort_index()
    )
    opcoes_unidade = contagens.index.tolist()

    st.subheader("Filtro de unidade de fornecimento")

    unidades_selecionadas = st.multiselect(
        "Unidade de fornecimento considerada no resumo estatístico",
        options=opcoes_unidade,
        default=opcoes_unidade,
        format_func=lambda unidade: f"{unidade} ({int(contagens[unidade])})",
        key=f"filtro_unidade_fornecimento_{chave}"
    )

    if not unidades_selecionadas:
        st.warning("Selecione pelo menos uma unidade de fornecimento para calcular o resumo estatístico.")
        return df_unidades.iloc[0:0].drop(columns=[coluna_unidade_resumo])

    df_filtrado = df_unidades[
        df_unidades[coluna_unidade_resumo].isin(unidades_selecionadas)
    ].copy()

    st.write("Registros considerados no resumo estatístico:", len(df_filtrado))

    return df_filtrado.drop(columns=[coluna_unidade_resumo])


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
    "Consulta assistida por catálogo",
    "Consulta direta por CATMAT"
])


# ============================================================
# ABA PRINCIPAL
# ============================================================

with aba_principal:
    tipo_catalogo = st.radio(
        "Filtrar por",
        options=[TIPO_MATERIAL, TIPO_SERVICO],
        horizontal=True,
        key="tipo_catalogo_principal"
    )

    if st.session_state.get("tipo_catalogo_principal_anterior") != tipo_catalogo:
        limpar_session_state_catmat()
        limpar_session_state_servicos()
        st.session_state.pop("df_pdms_encontrados", None)
        st.session_state.pop("diagnostico_pdms", None)
        st.session_state.pop("df_servicos_encontrados", None)
        st.session_state.pop("diagnostico_servicos", None)
        st.session_state["tipo_catalogo_principal_anterior"] = tipo_catalogo

    st.session_state.setdefault("codigo_pdm_manual", 8435)
    st.session_state.setdefault("nome_pdm_manual", "Notebook")

    if tipo_catalogo == TIPO_MATERIAL:
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
                    st.warning("Nenhum CATMAT foi retornado para o PDM informado.")

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
                    st.dataframe(df_filtrado[colunas_exibir].head(500), use_container_width=True)
                else:
                    st.dataframe(df_filtrado.head(500), use_container_width=True)

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

                    usar_filtro_data_precos = st.checkbox(
                        "Filtrar por período",
                        value=True,
                        key="usar_filtro_data_precos"
                    )

                    col_preco_1, col_preco_2, col_preco_3, col_preco_4 = st.columns(4)

                    with col_preco_1:
                        if usar_filtro_data_precos:
                            data_inicial = st.date_input(
                                "Data inicial",
                                value=date(2024, 1, 1),
                                key="data_inicial_precos"
                            )
                        else:
                            data_inicial = None
                            st.info("Sem data inicial")

                    with col_preco_2:
                        if usar_filtro_data_precos:
                            data_final = st.date_input(
                                "Data final",
                                value=date.today(),
                                key="data_final_precos"
                            )
                        else:
                            data_final = None
                            st.info("Sem data final")

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
                                (
                                    resultados_precos,
                                    erros_precos,
                                    urls_precos,
                                    diagnostico_precos
                                ) = consultar_precos_multiplos_itens_catalogo(
                                    codigos_itens=codigos_selecionados,
                                    data_inicial=data_inicial,
                                    data_final=data_final,
                                    tipo_catalogo="material",
                                    max_paginas_por_item=int(max_paginas_preco),
                                    tamanho_pagina=int(tamanho_pagina_preco),
                                    retornar_diagnostico=True
                                )

                            st.session_state["resultados_precos"] = resultados_precos
                            st.session_state["erros_precos"] = erros_precos
                            st.session_state["urls_precos"] = urls_precos
                            st.session_state["diagnostico_precos_por_codigo"] = diagnostico_precos
                            st.session_state["df_precos"] = pd.DataFrame(resultados_precos)
                            st.session_state["codigos_catmat_consultados"] = codigos_selecionados
                            st.session_state["codigos_catalogo_consultados"] = codigos_selecionados
                            st.session_state["tipo_catalogo_precos"] = "CATMAT"
                            st.session_state["coluna_codigo_consultado_precos"] = "catmat_consultado"
                            st.session_state["arquivo_csv_precos"] = "precos_consolidados_catmat.csv"
                            st.session_state["data_inicial_precos_final"] = data_inicial
                            st.session_state["data_final_precos_final"] = data_final

    else:
        st.header("1. Localizar serviço")

        col_busca_servico_1, col_busca_servico_2, col_busca_servico_3 = st.columns([3, 1, 1])

        with col_busca_servico_1:
            termo_busca_servico = st.text_input(
                "Buscar serviço por nome, classe ou grupo",
                value="limpeza",
                placeholder="Ex.: limpeza, suporte, manutenção, consultoria",
                key="termo_busca_servico"
            )

        with col_busca_servico_2:
            limite_resultados_servico = st.number_input(
                "Resultados",
                min_value=5,
                max_value=100,
                value=25,
                step=5,
                key="limite_resultados_servico"
            )

        with col_busca_servico_3:
            st.write("")
            st.write("")
            buscar_servico = st.button("Buscar serviço")

        if buscar_servico:
            if len(normalizar_texto(termo_busca_servico)) < 2:
                st.session_state.pop("df_servicos_encontrados", None)
                st.session_state.pop("diagnostico_servicos", None)
                st.warning("Informe pelo menos dois caracteres para buscar serviço.")
            else:
                limpar_session_state_precos()

                with st.spinner("Carregando catálogo de serviços e filtrando resultados..."):
                    df_catalogo_servicos, diagnostico_servicos = carregar_catalogo_servicos(
                        somente_ativos=True,
                        max_paginas=10,
                        tamanho_pagina=500
                    )

                    df_servicos_encontrados = localizar_servicos_por_termo(
                        df_catalogo_servicos,
                        termo_busca_servico,
                        limite=int(limite_resultados_servico)
                    )

                st.session_state["df_servicos_encontrados"] = df_servicos_encontrados
                st.session_state["diagnostico_servicos"] = diagnostico_servicos

        df_servicos_encontrados = st.session_state.get("df_servicos_encontrados")
        diagnostico_servicos = st.session_state.get("diagnostico_servicos", {})
        codigos_selecionados_busca = []

        st.header("2. Selecionar CATSERs similares")
        st.caption(
            "Refine os resultados por características e selecione um ou mais CATSERs "
            "que representem serviços comparáveis para a consulta de preços."
        )

        if df_servicos_encontrados is None:
            st.info("Faça uma busca para localizar e selecionar CATSERs similares.")
        else:
            if df_servicos_encontrados.empty:
                if diagnostico_servicos.get("erros"):
                    st.error("Não foi possível carregar o catálogo de serviços na API do Compras.gov.br.")
                else:
                    st.warning("Nenhum serviço encontrado para o termo informado.")
            else:
                st.success(f"{len(df_servicos_encontrados)} serviço(s) encontrado(s).")

                df_servicos_carac = criar_tabela_caracteristicas_servicos(df_servicos_encontrados)
                candidatas_servico = identificar_colunas_caracteristicas(df_servicos_carac)
                assinatura_busca_servico = re.sub(
                    r"[^A-Z0-9]+",
                    "_",
                    normalizar_texto(termo_busca_servico)
                ).strip("_") or "BUSCA"

                filtros_servico = {}

                with st.expander("Refinar lista de CATSERs por características", expanded=True):
                    st.caption(
                        "Os filtros são extraídos do grupo, da classe e das partes do nome do serviço."
                    )

                    if not candidatas_servico:
                        st.warning("Não foram identificadas características estruturáveis nos serviços localizados.")
                    else:
                        cols_servico = st.columns(3)
                        nomes_caracteristicas_servico = {
                            "GRUPO": "Grupo",
                            "CLASSE": "Classe",
                            "SERVICO_BASE": "Serviço base"
                        }

                        for i, item in enumerate(candidatas_servico[:6]):
                            coluna = item["coluna"]
                            nome_original = item["nome"]
                            nome = nomes_caracteristicas_servico.get(
                                nome_original,
                                nome_original.replace("_", " ").title()
                            )

                            valores = (
                                df_servicos_carac[coluna]
                                .dropna()
                                .astype(str)
                                .str.strip()
                                .sort_values()
                                .unique()
                                .tolist()
                            )

                            with cols_servico[i % 3]:
                                selecionados = st.multiselect(
                                    label=nome,
                                    options=valores,
                                    default=[],
                                    key=f"filtro_servico_{assinatura_busca_servico}_{coluna}"
                                )

                            filtros_servico[coluna] = selecionados

                df_servicos_filtrado = aplicar_filtros_dinamicos(
                    df_servicos_carac,
                    filtros_servico
                )

                st.subheader("CATSERs candidatos")
                st.write("Resultados após os filtros:", len(df_servicos_filtrado))

                colunas_servico_base = [
                    "codigoServico",
                    "nomeServico",
                    "codigoClasse",
                    "nomeClasse",
                    "codigoGrupo",
                    "nomeGrupo"
                ]
                colunas_servico_carac = [
                    coluna
                    for coluna in df_servicos_filtrado.columns
                    if coluna.startswith("CARAC_")
                ]
                colunas_servico = [
                    coluna
                    for coluna in colunas_servico_base + colunas_servico_carac
                    if coluna in df_servicos_filtrado.columns
                ]

                st.dataframe(
                    df_servicos_filtrado[colunas_servico].head(500),
                    use_container_width=True,
                    hide_index=True
                )

                if df_servicos_filtrado.empty:
                    st.warning("Nenhum CATSER permaneceu após os filtros.")
                else:
                    df_servicos_selecao = (
                        df_servicos_filtrado
                        .dropna(subset=["codigoServico"])
                        .drop_duplicates(subset=["codigoServico"])
                        .reset_index(drop=True)
                    )
                    mapa_servicos = {
                        int(linha["codigoServico"]): linha
                        for _, linha in df_servicos_selecao.iterrows()
                    }
                    codigos_disponiveis_servico = list(mapa_servicos.keys())

                    def formatar_opcao_servico(codigo):
                        linha = mapa_servicos[int(codigo)]
                        return (
                            f"{int(codigo)} - {linha.get('nomeServico', '')} "
                            f"| {linha.get('nomeClasse', '')}"
                        )

                    codigos_selecionados_busca = st.multiselect(
                        "Selecione os CATSERs similares para consultar preços",
                        options=codigos_disponiveis_servico,
                        default=[],
                        format_func=formatar_opcao_servico,
                        key=f"catser_selecionados_{assinatura_busca_servico}"
                    )

            with st.expander("Diagnóstico da busca de serviço", expanded=False):
                st.json(diagnostico_servicos)

        with st.expander("Adicionar CATSERs conhecidos", expanded=False):
            texto_catser_adicionais = st.text_input(
                "Códigos CATSER adicionais",
                placeholder="Ex.: 4006, 27626",
                key="catser_adicionais_servico",
                help="Separe vários códigos por vírgula, espaço ou ponto e vírgula."
            )

        codigos_adicionais_servico, codigos_invalidos_servico = parsear_codigos_catalogo(
            texto_catser_adicionais
        )

        if codigos_invalidos_servico:
            st.warning(
                "Ignorei valores que não são códigos CATSER válidos: "
                + ", ".join(codigos_invalidos_servico)
            )

        codigos_servico_consulta = list(dict.fromkeys(
            [int(codigo) for codigo in codigos_selecionados_busca]
            + codigos_adicionais_servico
        ))

        if codigos_servico_consulta:
            st.success(
                f"{len(codigos_servico_consulta)} CATSER(s) selecionado(s) para comparação: "
                + ", ".join(str(codigo) for codigo in codigos_servico_consulta)
            )
            st.header("3. Consulta de preços")

            usar_filtro_data_precos_servico = st.checkbox(
                "Filtrar por período",
                value=True,
                key="usar_filtro_data_precos_servico"
            )

            col_preco_serv_1, col_preco_serv_2, col_preco_serv_3, col_preco_serv_4 = st.columns(4)

            with col_preco_serv_1:
                if usar_filtro_data_precos_servico:
                    data_inicial_servico = st.date_input(
                        "Data inicial",
                        value=date(2024, 1, 1),
                        key="data_inicial_precos_servico"
                    )
                else:
                    data_inicial_servico = None
                    st.info("Sem data inicial")

            with col_preco_serv_2:
                if usar_filtro_data_precos_servico:
                    data_final_servico = st.date_input(
                        "Data final",
                        value=date.today(),
                        key="data_final_precos_servico"
                    )
                else:
                    data_final_servico = None
                    st.info("Sem data final")

            with col_preco_serv_3:
                max_paginas_preco_servico = st.number_input(
                    "Máximo de páginas por CATSER",
                    min_value=1,
                    max_value=10,
                    value=2,
                    step=1,
                    key="max_paginas_preco_servico"
                )

            with col_preco_serv_4:
                tamanho_pagina_preco_servico = st.number_input(
                    "Registros por página em preços",
                    min_value=10,
                    max_value=100,
                    value=50,
                    step=10,
                    key="tamanho_pagina_preco_servico"
                )

            if st.button("Consultar preços dos CATSERs selecionados"):
                limpar_session_state_precos()

                with st.spinner("Consultando preços praticados para os serviços selecionados..."):
                    (
                        resultados_precos,
                        erros_precos,
                        urls_precos,
                        diagnostico_precos
                    ) = consultar_precos_multiplos_itens_catalogo(
                        codigos_itens=codigos_servico_consulta,
                        data_inicial=data_inicial_servico,
                        data_final=data_final_servico,
                        tipo_catalogo="servico",
                        max_paginas_por_item=int(max_paginas_preco_servico),
                        tamanho_pagina=int(tamanho_pagina_preco_servico),
                        retornar_diagnostico=True
                    )

                st.session_state["resultados_precos"] = resultados_precos
                st.session_state["erros_precos"] = erros_precos
                st.session_state["urls_precos"] = urls_precos
                st.session_state["diagnostico_precos_por_codigo"] = diagnostico_precos
                st.session_state["df_precos"] = pd.DataFrame(resultados_precos)
                st.session_state["codigos_catalogo_consultados"] = codigos_servico_consulta
                st.session_state["tipo_catalogo_precos"] = "CATSER"
                st.session_state["coluna_codigo_consultado_precos"] = "catser_consultado"
                st.session_state["arquivo_csv_precos"] = "precos_consolidados_catser.csv"
                st.session_state["data_inicial_precos_final"] = data_inicial_servico
                st.session_state["data_final_precos_final"] = data_final_servico
        else:
            st.info("Selecione pelo menos um CATSER para avançar à consulta de preços.")

    if "df_precos" in st.session_state:
        df_precos = st.session_state["df_precos"]
        erros_precos = st.session_state.get("erros_precos", [])
        urls_precos = st.session_state.get("urls_precos", [])
        diagnostico_precos = st.session_state.get("diagnostico_precos_por_codigo", [])
        tipo_precos = st.session_state.get("tipo_catalogo_precos", "CATMAT")
        coluna_codigo_consultado = st.session_state.get(
            "coluna_codigo_consultado_precos",
            "catmat_consultado"
        )
        arquivo_csv_precos = st.session_state.get(
            "arquivo_csv_precos",
            "precos_consolidados_catmat.csv"
        )

        numero_resultados = 4 if tipo_precos == "CATSER" else 7
        st.header(f"{numero_resultados}. Resultados consolidados de preços ({tipo_precos})")

        st.write("Registros de preços carregados:", len(df_precos))

        with st.expander("URLs consultadas na pesquisa de preços"):
            st.write(urls_precos)

        if diagnostico_precos:
            with st.expander("Diagnóstico por código consultado", expanded=df_precos.empty):
                st.dataframe(
                    pd.DataFrame(diagnostico_precos),
                    use_container_width=True,
                    hide_index=True
                )

        if erros_precos:
            st.warning("Algumas consultas de preço retornaram erro.")
            st.json(erros_precos)

        if df_precos.empty:
            st.warning(f"Nenhum preço retornado para o(s) código(s) {tipo_precos} selecionado(s).")
        else:
            st.dataframe(df_precos, use_container_width=True)

            df_precos_analise = aplicar_filtro_unidade_fornecimento(
                df_precos,
                f"{tipo_precos.lower()}_consolidado"
            )

            exibir_estatisticas_precos(df_precos_analise)

            fornecedor_col = primeira_coluna_existente(
                df_precos_analise,
                ["nomeFornecedor", "fornecedor", "razaoSocialFornecedor"]
            )

            if fornecedor_col and not df_precos_analise.empty:
                st.subheader("Fornecedores mais recorrentes")
                fornecedores = (
                    df_precos_analise[fornecedor_col]
                    .fillna("Não informado")
                    .value_counts()
                    .reset_index()
                )
                fornecedores.columns = ["Fornecedor", "Ocorrências"]
                st.dataframe(fornecedores.head(20), use_container_width=True)

            orgao_col = primeira_coluna_existente(
                df_precos_analise,
                ["nomeOrgao", "orgao", "nomeOrgaoSuperior"]
            )

            if orgao_col and not df_precos_analise.empty:
                st.subheader("Órgãos compradores mais recorrentes")
                orgaos = (
                    df_precos_analise[orgao_col]
                    .fillna("Não informado")
                    .value_counts()
                    .reset_index()
                )
                orgaos.columns = ["Órgão", "Ocorrências"]
                st.dataframe(orgaos.head(20), use_container_width=True)

            descricao_col = primeira_coluna_existente(
                df_precos_analise,
                ["descricaoItem", "descricao", "descricao_item", "descricaoDetalhadaItem"]
            )

            if descricao_col and not df_precos_analise.empty:
                st.subheader("Descrições de itens encontradas")
                colunas_desc = [coluna_codigo_consultado, descricao_col]
                colunas_desc = [c for c in colunas_desc if c in df_precos_analise.columns]

                descricoes = df_precos_analise[colunas_desc].drop_duplicates()
                st.dataframe(descricoes.head(100), use_container_width=True)

            csv = df_precos.to_csv(index=False).encode("utf-8-sig")
            arquivo_excel_precos = arquivo_csv_precos.rsplit(".", 1)[0] + ".xlsx"
            excel = gerar_excel_precos_consolidados(
                df_precos=df_precos,
                df_precos_analise=df_precos_analise,
                diagnostico_precos=diagnostico_precos,
                urls_precos=urls_precos
            )

            col_download_csv, col_download_excel = st.columns(2)

            with col_download_csv:
                st.download_button(
                    label="Baixar preços consolidados em CSV",
                    data=csv,
                    file_name=arquivo_csv_precos,
                    mime="text/csv"
                )

            with col_download_excel:
                st.download_button(
                    label="Baixar preços consolidados em Excel",
                    data=excel,
                    file_name=arquivo_excel_precos,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
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

    usar_filtro_data_direta = st.checkbox(
        "Filtrar por período",
        value=True,
        key="direto_usar_filtro_data"
    )

    col4, col5 = st.columns(2)

    with col4:
        if usar_filtro_data_direta:
            data_inicial_direta = st.date_input(
                "Data inicial",
                value=date(2024, 1, 1),
                key="direto_data_inicial"
            )
        else:
            data_inicial_direta = None
            st.info("Sem data inicial")

    with col5:
        if usar_filtro_data_direta:
            data_final_direta = st.date_input(
                "Data final",
                value=date.today(),
                key="direto_data_final"
            )
        else:
            data_final_direta = None
            st.info("Sem data final")

    if st.button("Consultar preços praticados", key="direto_botao"):
        try:
            codigo_item_int = int(codigo_item)

            with st.spinner("Consultando Pesquisa de Preços..."):
                (
                    resultados,
                    erros,
                    urls,
                    diagnostico_precos
                ) = consultar_precos_multiplos_itens_catalogo(
                    codigos_itens=[codigo_item_int],
                    data_inicial=data_inicial_direta,
                    data_final=data_final_direta,
                    tipo_catalogo="material",
                    max_paginas_por_item=int(max_paginas),
                    tamanho_pagina=int(tamanho_pagina),
                    retornar_diagnostico=True
                )

            diagnostico_preco = diagnostico_precos[0] if diagnostico_precos else {}
            total_registros = diagnostico_preco.get("totalRegistrosAPI")
            total_paginas = diagnostico_preco.get("totalPaginasAPI")

            st.write("Total de registros informado pela API:", total_registros)
            st.write("Total de páginas informado pela API:", total_paginas)
            st.write("Registros carregados no app:", len(resultados))

            removidos_por_periodo = diagnostico_preco.get("removidosPorFiltroData", 0)

            if removidos_por_periodo:
                st.info(
                    f"{removidos_por_periodo} registro(s) fora do período informado foram ignorados localmente."
                )

            with st.expander("URLs consultadas"):
                st.write(urls)

            if diagnostico_precos:
                with st.expander("Diagnóstico por código consultado", expanded=not resultados):
                    st.dataframe(
                        pd.DataFrame(diagnostico_precos),
                        use_container_width=True,
                        hide_index=True
                    )

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
