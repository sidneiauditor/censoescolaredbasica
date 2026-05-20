"""
Controller do modo Ensino Superior — DMS × Censo da Educação Superior.

Merge primário: textual (NO_MANTENEDORA × NMRAZAOSOCIAL) via RapidFuzz.
Exportação: Relatório Operacional de Divergências (mesmo formato do Básico).
"""
from __future__ import annotations

import io
import logging
from typing import Any

import pandas as pd
import streamlit as st

from config import APP_DIR
from controllers.common import load_bundle, versioned_output_path, write_xlsx_async
from domain.dataset_kind import DatasetKind
from services.cnpj_aggregation import resolver_vigencia_dms
from services.divergencia_export_service import exportar_divergencias_operacionais
from services.indicators import add_basic_fiscal_indicators
from services.superior_consolidator import MUNICIPIO_SALVADOR, consolidate_censo_superior
from services.text_fuzzy_merge import run_textual_fuzzy_merge
from state.pipeline_state import PipelineState

LOG = logging.getLogger(__name__)

# ── Chaves de sessão exclusivas do modo Superior
_KEY_RESULTADO   = "cif_superior_resultado"
_KEY_SUMMARY     = "cif_superior_summary"
_KEY_SIG         = "cif_superior_ultimo_sig"
_KEY_IES_TOTAL   = "cif_superior_ies_total"

# ── Configuração padrão
_DEFAULT_CUTOFF    = 80
_COL_DMS_RAZAO     = "NMRAZAOSOCIAL"
_COL_CENSO_NOME    = "NO_MANTENEDORA"


# ─────────────────────────────────────────────────────── loaders

def _load_inep_csv(uploaded: Any, label: str) -> pd.DataFrame | None:
    """Carrega CSV INEP (sep=';', latin-1) a partir de upload Streamlit."""
    if uploaded is None:
        return None
    raw: bytes = uploaded.getvalue()
    try:
        with st.spinner(f"Carregando {label}…"):
            df = pd.read_csv(io.BytesIO(raw), sep=";", encoding="latin-1", low_memory=False)
        st.success(f"{label}: **{len(df):,} registros** · {len(df.columns)} colunas")
        LOG.info("INEP %s carregado: %s linhas.", label, len(df))
        return df
    except Exception as exc:  # pylint: disable=broad-except
        st.error(f"Erro ao carregar **{label}**: {exc}")
        LOG.exception("Falha ao carregar INEP %s", label)
        return None


# ─────────────────────────────────────────────────────── pipeline

def _executar_pipeline_superior(
    *,
    dms_df: pd.DataFrame,
    ies_df: pd.DataFrame,
    cur_df: pd.DataFrame,
    exercicio: int,
    include_ead: bool,
    apenas_privadas: bool,
    score_cutoff: float,
) -> bool:
    """Executa o pipeline completo e persiste resultado na sessão."""

    # ── 0. Resolução de vigência: remove CANCELADAS, RETIFICADORA substitui ORIGINAL
    n_antes = len(dms_df)
    dms_df = resolver_vigencia_dms(dms_df)
    n_excluidos = n_antes - len(dms_df)
    if n_excluidos:
        st.info(
            f"Resolução de vigência DMS: **{n_excluidos} linha(s) removida(s)** "
            f"(CANCELADAS excluídas; ORIGINAIs substituídas por RETIFICADORA onde aplicável)."
        )
    if dms_df.empty:
        st.error("Nenhum registro DMS válido após resolução de vigência.")
        return False

    # ── 1. Consolidar Censo Superior
    with st.spinner("Consolidando IES + Cursos…"):
        try:
            censo_sup = consolidate_censo_superior(
                ies_df, cur_df,
                municipio_codigo=MUNICIPIO_SALVADOR,
                include_ead=include_ead,
                apenas_privadas=apenas_privadas,
            )
        except Exception as exc:  # pylint: disable=broad-except
            st.error(f"Falha ao consolidar Censo Superior: {exc}")
            LOG.exception("Consolidação Censo Superior")
            return False

    if censo_sup.empty:
        st.error(
            "Nenhuma IES encontrada para Salvador (IBGE 2927408) nos arquivos enviados. "
            "Confirme que os arquivos correspondem ao Censo da Educação Superior."
        )
        return False

    n_ies = len(censo_sup)
    st.session_state[_KEY_IES_TOTAL] = n_ies
    st.info(
        f"**{n_ies} IES** em Salvador identificadas · "
        f"**{censo_sup['QT_MAT_BAS'].sum():,} matrículas** "
        f"({'com' if include_ead else 'sem'} EaD)"
    )

    # ── 2. Validar colunas DMS
    if _COL_DMS_RAZAO not in dms_df.columns:
        st.error(
            f"Coluna `{_COL_DMS_RAZAO}` não encontrada na DMS. "
            "Verifique se o arquivo é a DMS-Educação."
        )
        return False

    # ── 3. Merge fuzzy: DMS × IES por nome
    prog_bar = st.progress(0.0)

    def _cb(p: float) -> None:
        prog_bar.progress(min(max(p, 0.0), 1.0))

    with st.spinner("Executando merge textual (RapidFuzz)…"):
        try:
            resultado, summary = run_textual_fuzzy_merge(
                dms_df, censo_sup,
                col_dms_razao=_COL_DMS_RAZAO,
                col_censo_nome=_COL_CENSO_NOME,
                score_cutoff=float(score_cutoff),
                progress_callback=_cb,
            )
        except Exception as exc:  # pylint: disable=broad-except
            prog_bar.empty()
            st.error(f"Falha no merge textual: {exc}")
            LOG.exception("Merge fuzzy Superior")
            return False

    prog_bar.empty()

    # ── 4. Indicadores fiscais
    col_map = {
        "matriculas_denominador":    "censo__QT_MAT_BAS",
        "VLIMPOSTO_numerador":       "dms__VLIMPOSTO",
        "VLMENSALIDADE_numerador":   "dms__VLMENSALIDADE",
        "VLBASECALCULO_numerador":   "dms__VLBASECALCULO",
    }
    resultado, _ = add_basic_fiscal_indicators(resultado, col_map)

    # ── 5. Persistir
    st.session_state[_KEY_RESULTADO] = resultado
    st.session_state[_KEY_SUMMARY]   = summary

    # ── 6. Salvar base integrada (async)
    out_path   = versioned_output_path("consolidado_superior")
    fixed_path = APP_DIR / "outputs" / "consolidado_superior.xlsx"
    write_xlsx_async(resultado, out_path)
    write_xlsx_async(resultado, fixed_path)

    st.success(
        f"**Merge concluído** — "
        f"**{summary.encontrados:,}** de **{summary.linhas_dms:,}** linhas DMS vinculadas "
        f"· {summary.sem_correspondencia:,} sem correspondência "
        f"· cutoff {score_cutoff:.0f}"
    )
    return True


# ─────────────────────────────────────────────────────── dashboard

def _render_superior_dashboard() -> None:
    resultado: pd.DataFrame = st.session_state.get(_KEY_RESULTADO)
    summary = st.session_state.get(_KEY_SUMMARY)

    if resultado is None:
        return

    st.divider()
    st.header("Resultado — DMS × Censo Superior")

    # Métricas
    n_total   = len(resultado)
    n_match   = int((resultado["match_status"] == "match_textual").sum()) if "match_status" in resultado.columns else 0
    n_sem     = n_total - n_match
    n_ies_tot = st.session_state.get(_KEY_IES_TOTAL, "—")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Linhas DMS",          f"{n_total:,}")
    c2.metric("Vinculadas (fuzzy)",   f"{n_match:,}")
    c3.metric("Sem correspondência",  f"{n_sem:,}")
    c4.metric("IES em Salvador",      f"{n_ies_tot}")

    if summary:
        st.caption(
            f"Score mín. utilizado: **{summary.score_min_usado:.0f}** · "
            f"Tempo: **{summary.tempo_segundos:.2f}s**"
        )

    # Top matches por score
    if "similaridade_score" in resultado.columns and "dms__NMRAZAOSOCIAL" in resultado.columns:
        top_matches = (
            resultado[resultado["match_status"] == "match_textual"]
            [[
                "dms__NMRAZAOSOCIAL",
                "censo__NO_MANTENEDORA",
                "censo__NO_IES",
                "censo__QT_MAT_BAS",
                "similaridade_score",
            ]]
            .drop_duplicates("dms__NMRAZAOSOCIAL")
            .sort_values("similaridade_score", ascending=False)
            .head(20)
            .rename(columns={
                "dms__NMRAZAOSOCIAL":    "Razão Social (DMS)",
                "censo__NO_MANTENEDORA": "Mantenedora (Censo)",
                "censo__NO_IES":         "Nome da IES",
                "censo__QT_MAT_BAS":     "Matrículas",
                "similaridade_score":    "Score",
            })
        )
        with st.expander("Vínculos estabelecidos (top 20 por score)", expanded=True):
            st.dataframe(top_matches, use_container_width=True, height=320)

    # Sem correspondência
    if "dms__NMRAZAOSOCIAL" in resultado.columns:
        sem = (
            resultado[resultado["match_status"] != "match_textual"]
            [["dms__NUCNPJ", "dms__NMRAZAOSOCIAL"]]
            .drop_duplicates("dms__NUCNPJ")
            .rename(columns={
                "dms__NUCNPJ":        "CNPJ",
                "dms__NMRAZAOSOCIAL": "Razão Social",
            })
        )
        if not sem.empty:
            with st.expander(f"Sem correspondência — {len(sem)} contribuintes"):
                st.dataframe(sem, use_container_width=True, height=220)

    # Export — apenas registros com vínculo IES estabelecido
    st.divider()
    exercicio = PipelineState.exercise()
    resultado_matched = (
        resultado[resultado["match_status"] == "match_textual"]
        if "match_status" in resultado.columns
        else resultado
    )

    # CNPJs que também aparecem no Censo Escolar (instituições mistas)
    cnpjs_mistos: set[str] = set()
    consolidado_basica = PipelineState.consolidado()
    if consolidado_basica is not None and "dms__NUCNPJ" in consolidado_basica.columns:
        cnpjs_mistos = set(
            consolidado_basica["dms__NUCNPJ"].dropna()
            .astype(str).str.replace(r"\.0$", "", regex=True)
            .str.strip().str.zfill(14)
        )

    st.download_button(
        label="Exportar Divergências Operacionais — Ensino Superior (.xlsx)",
        data=exportar_divergencias_operacionais(
            resultado_matched,
            exercicio=exercicio,
            col_censo_entidade="censo__CO_IES",
            label_censo="Qtd Matrículas Censo Superior",
            cnpjs_mistos=cnpjs_mistos or None,
        ),
        file_name=f"divergencias_superior_{exercicio}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_divergencias_superior",
        type="primary",
    )


# ─────────────────────────────────────────────────────── UI principal

def main_superior_ui() -> None:
    """Painel Ensino Superior — Salvador."""

    st.title("CIF — DMS × Censo Educação Superior (Salvador)")
    st.caption(
        "**Salvador · BA · IBGE 2927408.** "
        "Merge por similaridade textual: Razão Social (DMS) × Mantenedora (Censo Superior)."
    )

    resultado_pronto = st.session_state.get(_KEY_RESULTADO) is not None

    if resultado_pronto:
        tab_painel, tab_arquivos = st.tabs(["Painel", "Arquivos e processamento"])
        with tab_painel:
            _render_superior_dashboard()
        container = tab_arquivos
    else:
        container = st.container()

    with container:
        st.subheader("Upload de arquivos")
        st.caption(
            "São necessários **três arquivos**: DMS-Educação (CSV ou Excel), "
            "microdados IES do Censo Superior (CSV, separador ponto e vírgula) e "
            "microdados de Cursos (CSV, separador ponto e vírgula)."
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            up_dms = st.file_uploader(
                "DMS-Educação", type=["csv", "xlsx"],
                key="sup_up_dms",
                help="Relatório de DMS do ISS-Educação exportado da prefeitura.",
            )
        with col2:
            up_ies = st.file_uploader(
                "IES — Censo Superior (CSV)", type=["csv"],
                key="sup_up_ies",
                help="Arquivo MICRODADOS_ED_SUP_IES_XXXX.CSV do INEP.",
            )
        with col3:
            up_cur = st.file_uploader(
                "Cursos — Censo Superior (CSV)", type=["csv"],
                key="sup_up_cursos",
                help="Arquivo MICRODADOS_CADASTRO_CURSOS_XXXX.CSV do INEP.",
            )

        st.divider()
        st.subheader("Opções")
        op1, op2, op3 = st.columns(3)
        with op1:
            include_ead = st.checkbox(
                "Incluir matrículas EaD",
                value=True,
                key="sup_include_ead",
                help="Inclui cursos a distância no total de matrículas do Censo Superior.",
            )
        with op2:
            apenas_privadas = st.checkbox(
                "Apenas IES privadas",
                value=False,
                key="sup_apenas_privadas",
                help="Filtra o Censo Superior para categorias 4 e 5 (privadas).",
            )
        with op3:
            score_cutoff = st.slider(
                "Score mínimo (fuzzy)",
                min_value=50, max_value=95, value=_DEFAULT_CUTOFF, step=5,
                key="sup_score_cutoff",
                help="Pontuação mínima WRatio do RapidFuzz para aceitar um vínculo.",
            )

        arquivos_prontos = up_dms is not None and up_ies is not None and up_cur is not None

        if not arquivos_prontos:
            st.button("Processar", disabled=True, key="sup_btn_disabled",
                      help="Envie os três arquivos para continuar.")
        else:
            if st.button("Processar", type="primary", key="sup_btn_processar"):
                exercicio = PipelineState.exercise()

                dms_bundle = load_bundle(DatasetKind.DMS_EDUCACAO, up_dms, "DMS-Educação")
                if dms_bundle is None:
                    st.stop()
                dms_df = dms_bundle["dataframe"]

                ies_df = _load_inep_csv(up_ies, "IES")
                cur_df = _load_inep_csv(up_cur, "Cursos")

                if ies_df is None or cur_df is None:
                    st.stop()

                ok = _executar_pipeline_superior(
                    dms_df=dms_df,
                    ies_df=ies_df,
                    cur_df=cur_df,
                    exercicio=exercicio,
                    include_ead=include_ead,
                    apenas_privadas=apenas_privadas,
                    score_cutoff=float(score_cutoff),
                )
                if ok:
                    st.rerun()
