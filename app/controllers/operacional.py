"""
Controller do modo operacional Salvador — pipeline automático e painel fiscal.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import streamlit as st

from cif_geo_salvador import ROTULO_SALVADOR_CURTO, construir_geo_context_salvador
from config import APP_DIR, SELECT_SENTINEL
from controllers.common import (
    coerce_frames_from_uploads,
    versioned_output_path,
    write_xlsx_async,
)
from controllers.pipeline import (
    ensure_normalized_cnpj_workframes,
    resolve_escola_mapping,
    resolve_matricula_mapping,
)
from domain.audit import MergeAuditRecord
from services.census_consolidator import (
    CensusMergeError,
    consolidate_census_escolar,
    normalize_co_entidade,
)
from services.cnpj_aggregation import filter_censo_for_fiscal_panel
from services.cnpj_merge import deterministic_merge_by_cnpj
from services.cnpj_root_aggregation import CNPJ_RAIZ_COL, dataframe_with_cnpj_raiz
from services.divergencia_export_service import exportar_divergencias_operacionais
from services.indicators import add_basic_fiscal_indicators
from services.municipality_filter import (
    filter_escola_by_municipality,
    restrict_matricula_to_entidades,
)
from state.pipeline_state import PipelineState, SessionKeys
from ui import mode as ui_mode
from ui import upload as ui_upload
from ui.operacional_dashboard import render_operacional_enrollment_dashboard
from utils.cnpj import normalize_cnpj_digits

LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────── helpers UI

def _render_previews_operacional(
    dms_df: pd.DataFrame | None,
    df_escola: pd.DataFrame | None,
    df_mat: pd.DataFrame | None,
    up_dms: Any,
    up_escola: Any,
    up_mat: Any,
) -> None:
    preview_h = 220
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**DMS-Educação**")
        if dms_df is None or up_dms is None:
            st.caption("Aguardando ficheiro válido.")
        else:
            st.caption(f"`{up_dms.name}` · {len(dms_df):,} linhas")
            st.dataframe(dms_df.head(8), use_container_width=True, height=preview_h)
    with c2:
        st.markdown("**Censo Escola**")
        if df_escola is None or up_escola is None:
            st.caption("Aguardando ficheiro válido.")
        else:
            st.caption(f"`{up_escola.name}` · {len(df_escola):,} linhas")
            st.dataframe(df_escola.head(8), use_container_width=True, height=preview_h)
    with c3:
        st.markdown("**Censo Matrícula**")
        if df_mat is None or up_mat is None:
            st.caption("Aguardando ficheiro válido.")
        else:
            st.caption(f"`{up_mat.name}` · {len(df_mat):,} linhas")
            st.dataframe(df_mat.head(8), use_container_width=True, height=preview_h)


def _render_operacional_dashboard_download() -> None:
    df_op = PipelineState.consolidado()
    if df_op is None:
        st.info("Ainda não há **base integrada** nesta sessão — use **Arquivos e processamento**.")
        return
    cm_op = PipelineState.column_map()
    render_operacional_enrollment_dashboard(
        df_op,
        cm_op,
        dms_work=PipelineState.dms_work(),
        censo_work=PipelineState.censo_work(),
    )
    exercicio = PipelineState.exercise()
    st.download_button(
        label="Exportar Divergências Operacionais (.xlsx)",
        data=exportar_divergencias_operacionais(df_op, exercicio=exercicio),
        file_name=f"divergencias_dms_censo_{exercicio}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_divergencias_operacional",
        type="primary",
    )


# ─────────────────────────────────────────────────────── append context

def _append_censo_context_columns(df: pd.DataFrame, ctx: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    if ctx.get("uf"):
        out["censo_ctx_UF"] = str(ctx["uf"])
    if ctx.get("mun_code") is not None:
        out["censo_ctx_municipio_codigo"] = str(ctx["mun_code"])
    if ctx.get("mun_label"):
        out["censo_ctx_municipio_rotulo_ui"] = str(ctx["mun_label"])
    out["censo_ctx_filtro_municipal_aplicado"] = "sim" if ctx.get("filtro_ativo") else "nao"
    return out


# ─────────────────────────────────────────────────────── pipeline principal

def _executar_pipeline_operacional(
    *,
    dms_df: pd.DataFrame,
    df_escola: pd.DataFrame,
    df_mat: pd.DataFrame,
    exercise_year_ctx: int,
    up_dms: Any,
    up_escola: Any,
    up_mat: Any,
) -> bool:
    cols_esc = list(map(str, df_escola.columns))
    map_e = resolve_escola_mapping(cols_esc, ui_simples=True)
    map_m = resolve_matricula_mapping(list(map(str, df_mat.columns)), ui_simples=True)

    geo_ctx = construir_geo_context_salvador(df_escola, map_e, int(exercise_year_ctx))
    if geo_ctx.get("filtro_impossivel_geo"):
        st.error(
            "Não foi possível aplicar o recorte automático para **Salvador (BA, IBGE 2927408)**. "
            "Confirme que o extracto contempla esse município ou utilize o modo **Técnico**."
        )
        return False
    geo_ctx["mun_label"] = ROTULO_SALVADOR_CURTO

    df_esc_eff = df_escola.copy()
    df_mat_eff = df_mat.copy()

    if geo_ctx.get("filtro_ativo") and geo_ctx.get("uf") and geo_ctx.get("mun_code"):
        df_esc_eff, filt_stats = filter_escola_by_municipality(
            df_esc_eff, map_e,
            uf_escolha=str(geo_ctx["uf"]),
            municipio_codigo=str(geo_ctx["mun_code"]),
        )
        motivo = filt_stats.get("motivo", "")
        if motivo:
            st.warning(f"Recorte municipal: {motivo}")
        else:
            st.info(
                f"Escolas antes ▸ depois do recorte Salvador: "
                f"**`{filt_stats.get('antes'):,}` ▸ `{filt_stats.get('depois'):,}`**."
            )

    df_esc_eff, _ff = filter_censo_for_fiscal_panel(
        df_esc_eff, only_private=True, exclude_superior_puro=True,
        keep_missing_dependencia=False, keep_missing_matriculas_bas=False,
    )
    if _ff.get("dependencia_col_missing"):
        st.warning(
            "Coluna de dependência administrativa **não encontrada** — escolas públicas não foram excluídas. "
            "Confirme que `TP_DEPENDENCIA` está no arquivo Escola."
        )
    else:
        _n_pub = int(_ff.get("n_publicas_excluidas", 0))
        _n_sup = int(_ff.get("n_superior_puro_excluidas", 0))
        if _n_pub or _n_sup:
            st.info(
                f"Filtro fiscal: **{_n_pub:,}** escola(s) pública(s) e **{_n_sup:,}** sem Educação Básica "
                f"excluídas — restam **{len(df_esc_eff.index):,}** escolas privadas no cruzamento."
            )

    phys_co_esc = map_e.get("CO_ENTIDADE")
    if phys_co_esc and phys_co_esc in df_esc_eff.columns:
        entidades = set(normalize_co_entidade(df_esc_eff[phys_co_esc]).tolist())
        entidades.discard("")
        phys_co_mat = map_m.get("CO_ENTIDADE")
        if phys_co_mat and isinstance(phys_co_mat, str) and phys_co_mat in df_mat_eff.columns and entidades:
            df_mat_eff_, mat_trim = restrict_matricula_to_entidades(df_mat_eff, phys_co_mat, entidades)
            df_mat_eff = df_mat_eff_
            st.success(
                f"Matrículas recortadas ao mesmo conjunto de escolas (**{phys_co_mat}**): "
                f"{mat_trim['antes_mat']:,} → {mat_trim['depois_mat']:,} linhas."
            )

    fn_esc = up_escola.name if up_escola else ""
    fn_mat = up_mat.name if up_mat else ""
    try:
        merged_out = consolidate_census_escolar(
            df_esc_eff, df_mat_eff, map_e, map_m, int(exercise_year_ctx),
            source_escola_label=fn_esc, source_matricula_label=fn_mat or "",
        )
    except CensusMergeError as exc:
        st.error(str(exc))
        LOG.warning("Consolidação operacional recusada: %s", exc)
        return False
    except Exception as exc:  # pylint: disable=broad-except
        st.error("Erro inesperado ao integrar Escola ⊕ Matrícula.")
        LOG.exception("merge censo operacional")
        with st.expander("Detalhe técnico", expanded=False):
            st.code(str(exc))
        return False

    merged_final = _append_censo_context_columns(merged_out, geo_ctx)
    stale_sig_anchor = tuple(sorted({
        ("uf_pick",     geo_ctx.get("uf")),
        ("mun_pick",    geo_ctx.get("mun_code")),
        ("filtro_geo",  geo_ctx.get("filtro_ativo")),
        ("skip_geo_flag", geo_ctx.get("skip_geo")),
    }))
    PipelineState.set_censo_consolidado(
        merged_final,
        (fn_esc, fn_mat or "", int(exercise_year_ctx), True,
         tuple(sorted(map_e.items())), tuple(sorted(map_m.items())), stale_sig_anchor),
    )

    cm_live = PipelineState.column_map()
    rebuilt = ensure_normalized_cnpj_workframes(
        column_map=cm_live, df_dms=dms_df,
        df_censo_consolidado=merged_final, inferencia_operacional_dms=True,
    )
    if rebuilt is None:
        return False

    dms_work_ready, censo_work_ready, cm_new = rebuilt
    dms_work_ready  = dataframe_with_cnpj_raiz(dms_work_ready,  "__cnpj_norm_dms")
    censo_work_ready = dataframe_with_cnpj_raiz(censo_work_ready, "__cnpj_norm_censo")
    PipelineState.set_work_frames(dms_work_ready, censo_work_ready, cm_new)

    col_dms_raw = cm_new.get("dms_cnpj")
    if (
        not col_dms_raw or col_dms_raw == SELECT_SENTINEL
        or "__cnpj_norm_dms" not in dms_work_ready.columns
        or "__cnpj_norm_censo" not in censo_work_ready.columns
    ):
        st.error("Não foi possível preparar o cruzamento por **CNPJ**. Utilize o modo técnico.")
        return False

    sig_det_now = (
        str(col_dms_raw), str(cm_new.get("censo_cnpj")),
        len(dms_work_ready.index), len(censo_work_ready.index), True, True,
    )

    try:
        with st.spinner("A integrar bases e cruzar dados fiscais (CNPJ)…"):
            consolidado_cnpj, summary_cnpj = deterministic_merge_by_cnpj(
                dms_work_ready, censo_work_ready,
                col_dms_raw_cnpj=str(col_dms_raw),
                col_dms_norm="__cnpj_norm_dms",
                col_censo_norm="__cnpj_norm_censo",
                progress_callback=None,
            )
    except Exception as exc:  # pylint: disable=broad-except
        st.error(
            "Falha ao cruzar dados fiscais. Abra o modo **Técnico** para ver o relatório pré-merge."
        )
        LOG.exception("operacional deterministic merge")
        with st.expander("Detalhe técnico", expanded=False):
            st.code(str(exc))
        return False

    consolidado_cnpj, _report61 = add_basic_fiscal_indicators(consolidado_cnpj, dict(cm_new))

    pref_dms_cnpj = f"dms__{col_dms_raw}"
    if pref_dms_cnpj in consolidado_cnpj.columns:
        cc = consolidado_cnpj.copy()
        norms = cc[pref_dms_cnpj].map(normalize_cnpj_digits)
        cc[CNPJ_RAIZ_COL] = norms.map(lambda x: x[:8] if isinstance(x, str) and len(x) == 14 else "")
        consolidado_cnpj = cc

    PipelineState.set_merge_result(consolidado_cnpj, summary_cnpj, sig_det_now)

    out_path   = versioned_output_path("consolidado")
    fixed_path = APP_DIR / "outputs" / "consolidado.xlsx"
    write_xlsx_async(consolidado_cnpj, out_path)
    write_xlsx_async(consolidado_cnpj, fixed_path)

    _audit = MergeAuditRecord.from_summary(
        summary_cnpj,
        modo_ui="operacional",
        exercise=PipelineState.exercise(),
        municipio_codigo="2927408",
        municipio_label="Salvador",
        uf="BA",
        n_dms_linhas=len(dms_work_ready.index),
        n_censo_consolidado_linhas=len(censo_work_ready.index),
        cnpj_col_dms=str(col_dms_raw),
        cnpj_col_censo=str(cm_new.get("censo_cnpj", "")),
        dms_hash=PipelineState.get_file_hash("dms") or "",
        escola_hash=PipelineState.get_file_hash("escola") or "",
        matricula_hash=PipelineState.get_file_hash("matricula") or "",
    )
    _audit.append_to_log(APP_DIR / "outputs" / "audit_log.jsonl")

    st.success("**Base integrada** pronta — painel fiscal actualizado.")
    return True


# ─────────────────────────────────────────────────────── UI principal operacional

def main_operacional_ui() -> None:
    """Painel Salvador: upload único em lote e pipeline já existente."""
    ui_mode.marcar_preset_salvador_sessao()
    st.session_state.setdefault(SessionKeys.EXERCISE_DEFAULT, 2025)
    consolidado_ready = PipelineState.consolidado() is not None

    st.title("CIF — DMS × Censo municipal (Salvador)")
    st.caption(
        "**Salvador · BA · IBGE 2927408.** Use **uma única janela** para CSV/XLSX da DMS, do Censo Escola "
        "e do Censo Matrícula — o programa identifica o papel de cada um pelos cabeçalhos."
    )

    if consolidado_ready:
        tab_dashboard, zona_arquivos = st.tabs(["Painel fiscal", "Arquivos e processamento"])
        with tab_dashboard:
            _render_operacional_dashboard_download()
        container_carregamentos = zona_arquivos
    else:
        container_carregamentos = st.container()

    clicked_run = False
    up_slot_dms_final = up_slot_esc_final = up_slot_mat_final = None
    df_dmss = df_esc_final = df_matricula_final = None
    resultado_do_lote = None

    with container_carregamentos:
        up_slot_dms_final, up_slot_esc_final, up_slot_mat_final, resultado_do_lote = (
            ui_upload.render_operacional_batch_upload()
        )
        df_dmss, df_esc_final, df_matricula_final = coerce_frames_from_uploads(
            up_slot_dms_final, up_slot_esc_final, up_slot_mat_final,
        )

        with st.expander("Pré-visualização (amostra)", expanded=False):
            _render_previews_operacional(
                df_dmss, df_esc_final, df_matricula_final,
                up_slot_dms_final, up_slot_esc_final, up_slot_mat_final,
            )

        tripla_sem_ambiguo  = resultado_do_lote.triple_ready()
        leituras_ok_local   = df_dmss is not None and df_esc_final is not None and df_matricula_final is not None
        pode_gravar_na_pipeline = tripla_sem_ambiguo and leituras_ok_local

        if not tripla_sem_ambiguo:
            st.button(
                "Processar dados", disabled=True,
                key="cif_ops_proc_disabled_waiting_triple",
                help="O lote ainda não tem uma **única DMS-Educação**, uma **Escola** e uma **Matrícula** bem identificadas.",
            )
            clicked_run = False
        else:
            clicked_run = st.button(
                "Processar dados", type="primary", disabled=not leituras_ok_local,
                key="cif_btn_processar_operacional",
                help=None if leituras_ok_local else "Classificação do lote concluída mas ao menos uma base falhou ao abrir.",
            )

    nm_dms = up_slot_dms_final.name if up_slot_dms_final else ""
    nm_esc = up_slot_esc_final.name if up_slot_esc_final else ""
    nm_mat = up_slot_mat_final.name if up_slot_mat_final else ""
    sig_exercicio = PipelineState.exercise()

    # Assinatura de pipeline baseada em hashes de conteúdo (Task 10)
    arquivo_sig = (
        PipelineState.get_file_hash("dms")      or nm_dms,
        PipelineState.get_file_hash("escola")   or nm_esc,
        PipelineState.get_file_hash("matricula") or nm_mat,
        sig_exercicio,
    )

    marcador_ok     = st.session_state.get("cif_operacional_ultimo_sig_ok")
    marcador_falhou = st.session_state.get("cif_operacional_pipeline_falhou_sig")
    auto_sidebar    = bool(st.session_state.get(ui_mode.AUTO_PROCESS_KEY, True))

    rerun_auto = (
        auto_sidebar and pode_gravar_na_pipeline
        and arquivo_sig != marcador_ok
        and arquivo_sig != marcador_falhou
    )
    deve_rodar = (clicked_run and pode_gravar_na_pipeline) or rerun_auto

    if clicked_run and not resultado_do_lote.triple_ready():
        st.warning(
            "Resolva os avisos do **lote** (Etiquetas DMS / Escola / Matrícula ausentes ou em conflito) "
            "antes de continuar."
        )
    elif clicked_run and resultado_do_lote.triple_ready() and not leituras_ok_local:
        st.warning("Etiquetas corretas no lote, mas **CSV/XLSX** com erro estrutural — confira mensagens.")

    if rerun_auto and not (clicked_run and pode_gravar_na_pipeline):
        st.info("**Triagem válida.** O modo automático vai arrancar o encadeamento completo.")

    if deve_rodar:
        resultado_ok = _executar_pipeline_operacional(
            dms_df=df_dmss, df_escola=df_esc_final, df_mat=df_matricula_final,
            exercise_year_ctx=sig_exercicio,
            up_dms=up_slot_dms_final, up_escola=up_slot_esc_final, up_mat=up_slot_mat_final,
        )
        if resultado_ok:
            st.session_state["cif_operacional_ultimo_sig_ok"] = arquivo_sig
            st.session_state.pop("cif_operacional_pipeline_falhou_sig", None)
            st.rerun()
        else:
            st.session_state["cif_operacional_pipeline_falhou_sig"] = arquivo_sig
