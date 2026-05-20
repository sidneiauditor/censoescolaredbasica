"""
Controller do modo técnico — UX completo com mapeamento manual e diagnósticos.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import streamlit as st

from config import APP_DIR, SELECT_SENTINEL, UX_AVANCADO, UX_SIMPLES
from controllers.common import load_bundle, coerce_frames_from_uploads
from controllers.pipeline import (
    collect_logical_mapping,
    column_options,
    ensure_normalized_cnpj_workframes,
    render_logical_mapper,
    resolve_escola_mapping,
    resolve_matricula_mapping,
    run_etapa2_mapping,
    run_etapa3_merge_pipeline,
)
from domain.census_logical import CENSO_ESCOLA_FIELDS, CENSO_MATRICULA_FIELDS
from domain.dataset_kind import DatasetKind
from services.census_consolidator import (
    CensusMergeError,
    consolidate_census_escolar,
    normalize_co_entidade,
)
from services.cnpj_aggregation import filter_censo_for_fiscal_panel
from services.cnpj_root_aggregation import dataframe_with_cnpj_raiz
from services.municipality_filter import (
    filter_escola_by_municipality,
    restrict_matricula_to_entidades,
)
from state.pipeline_state import PipelineState, SessionKeys
from ui import upload as ui_upload

LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────── helpers geográficos

def _sanitize_municipio_codigo_cell(value: object) -> str:
    texto = "" if pd.isna(value) else str(value).strip()
    if texto.endswith(".0") and texto.replace(".0", "").isdigit():
        texto = texto[:-2]
    return texto


def append_censo_context_columns(df: pd.DataFrame, ctx: dict[str, Any]) -> pd.DataFrame:
    out = df.copy()
    if ctx.get("uf"):
        out["censo_ctx_UF"] = str(ctx["uf"])
    if ctx.get("mun_code") is not None:
        out["censo_ctx_municipio_codigo"] = str(ctx["mun_code"])
    if ctx.get("mun_label"):
        out["censo_ctx_municipio_rotulo_ui"] = str(ctx["mun_label"])
    out["censo_ctx_filtro_municipal_aplicado"] = "sim" if ctx.get("filtro_ativo") else "nao"
    return out


# ─────────────────────────────────────────────────────── Etapa 0 — contexto municipal

def render_etapa0_contexto_municipal(
    df_escola: pd.DataFrame,
    map_geo: dict[str, str],
    *,
    ui_simples: bool,
) -> dict[str, Any]:
    """Etapa 0 — exercício + UF + município."""

    st.divider()
    st.header("Etapa 0 — Contexto municipal")
    if ui_simples:
        st.caption(
            "Indique o ano de referência e o **município**. Só ficam escolas dessa cidade no Censo "
            "**antes** da junção — processamento mais leve."
        )
    else:
        st.caption(
            "Igual ao modo simples, com opções extra: pode **desligar** o recorte territorial se o ficheiro "
            "já estiver pré-filtrado ou não usar colunas típicas INEP."
        )

    exercise_default = PipelineState.exercise()
    ex = st.number_input(
        "Exercício do Censo (ano de referência)",
        min_value=1996, max_value=2050, value=exercise_default, step=1,
        key="ctx_exercise_year",
        help='Aparece em ``censo_exercicio`` na base consolidada.',
    )
    st.session_state[SessionKeys.EXERCISE_DEFAULT] = int(ex)

    uf_phys = map_geo.get("SG_UF")
    co_phys = map_geo.get("CO_MUNICIPIO")
    no_phys = map_geo.get("NO_MUNICIPIO")

    skip_geo = False
    if not ui_simples:
        skip_geo = st.checkbox(
            "Não aplicar filtro municipal (usar todas as linhas da Escola carregada)",
            value=bool(st.session_state.get("ctx_skip_municipality", False)),
            key="ctx_skip_municipality",
        )

    out: dict[str, Any] = {
        "exercise": int(ex), "uf": None, "mun_code": None, "mun_label": "",
        "skip_geo": bool(skip_geo), "filtro_ativo": False, "filtro_impossivel_geo": False,
    }

    if skip_geo:
        st.info(
            "**Filtro municipal desativado.** Será usado o conjunto inteiro da tabela Escola — "
            "único cenário válido quando o arquivo já está recortado ou não há UF/município."
        )
        return out

    if uf_phys is None or co_phys is None:
        out["filtro_impossivel_geo"] = True
        st.warning(
            "As colunas físicas típicas de **UF / município** não foram encontradas nem mapeadas.\n\n"
            "- Modo simples: confirme se o ficheiro é o microdados **Escola** INEP (com ``SG_UF`` / ``CO_MUNICIPIO``).\n"
            "- Modo avançado: associe explicitamente esses dois campos lógicos no mapeamento **ou** desative o filtro acima."
        )
        return out

    if uf_phys not in df_escola.columns or co_phys not in df_escola.columns:
        out["filtro_impossivel_geo"] = True
        st.error("As colunas de localização definidas pelo mapeamento **não existem** nesta tabela Escola.")
        return out

    ufs = df_escola[uf_phys].dropna().astype(str).str.strip().str.upper().unique()
    ufs_sorted = sorted(u for u in ufs if u)
    if not ufs_sorted:
        st.warning("Sem valores de UF na coluna física configurada.")
        out["filtro_impossivel_geo"] = True
        return out

    uf_sel = st.selectbox("UF", ufs_sorted, key="ctx_uf_select")
    out["uf"] = uf_sel

    sub_mask = df_escola[uf_phys].astype(str).str.strip().str.upper() == str(uf_sel).strip().upper()
    sub = df_escola.loc[sub_mask]
    use_name = bool(no_phys and no_phys in df_escola.columns)

    labels: list[str] = []
    label_to_code: dict[str, str] = {}
    for _, row in sub[[co_phys] + ([no_phys] if use_name else [])].drop_duplicates().iterrows():
        code = _sanitize_municipio_codigo_cell(row[co_phys])
        if use_name:
            nome = str(row[no_phys]).strip() if not pd.isna(row[no_phys]) else ""
            label = f"{nome} — código IBGE {code}" if nome else f"Município código IBGE {code}"
        else:
            label = f"Município código IBGE {code}"
        if label not in label_to_code:
            labels.append(label)
            label_to_code[label] = code

    labels = sorted(labels, key=lambda s: (label_to_code.get(s, ""), s))
    if not labels:
        st.warning("Nenhum código de município encontrado para a UF seleccionada.")
        out["filtro_impossivel_geo"] = True
        return out

    chosen = st.selectbox("Município", labels, key="ctx_municipio_select")
    out["mun_code"] = label_to_code.get(chosen)
    out["mun_label"] = chosen
    out["filtro_ativo"] = True
    st.success(
        f"Vamos **filtrar o Censo** para apenas escolas em **{chosen.split(' — ')[0].strip()}** "
        f"({uf_sel}) antes de juntar com Matrícula."
    )
    return out


# ─────────────────────────────────────────────────────── continuação DMS/Etapas 2 e 3

def _maybe_continue_dms_etapas(
    dms_df: pd.DataFrame | None,
    censo_consolidado: object,
    ui_simples: bool,
    up_dms: Any,
    up_escola: Any,
) -> None:
    if not isinstance(censo_consolidado, pd.DataFrame):
        st.warning(
            "Após configurar UF/município e garantir reconhecimento das colunas, clique em **Consolidar** "
            "para gerar a base única usada pela DMS."
        )
        return

    if dms_df is None:
        st.info(
            "**Carregue a DMS-Educação** para iniciar Etapa 2 (CNPJ normalizado em memória) e "
            "Etapa 3 (matching)."
        )
        return

    dms_nm   = getattr(up_dms,    "name", "?")
    escola_nm = getattr(up_escola, "name", "?") if up_escola else "?"
    run_etapa2_mapping(
        dms_df, censo_consolidado,
        ui_simples=ui_simples,
        up_dms_name=str(dms_nm),
        up_escola_name=str(escola_nm),
    )

    cm_live = PipelineState.column_map()
    rebuilt = ensure_normalized_cnpj_workframes(
        column_map=cm_live,
        df_dms=dms_df,
        df_censo_consolidado=censo_consolidado,
    )
    if rebuilt is None:
        if ui_simples:
            st.info(
                "Precisamos de **colunas físicas válidas em ambas as bases** antes do merge pela chave CNPJ. "
                "Use a Etapa 2 quando as mensagens de erro acima forem sanadas."
            )
        else:
            st.warning("Corrija o mapeamento de **CNPJ** na Etapa 2 para continuar ao merge determinístico.")
        return

    dms_work_ready, censo_work_ready, cm_new = rebuilt
    dms_work_ready   = dataframe_with_cnpj_raiz(dms_work_ready,  "__cnpj_norm_dms")
    censo_work_ready = dataframe_with_cnpj_raiz(censo_work_ready, "__cnpj_norm_censo")
    PipelineState.set_work_frames(dms_work_ready, censo_work_ready, cm_new)

    run_etapa3_merge_pipeline(dms_work_ready, censo_work_ready, ux_simples=ui_simples)


# ─────────────────────────────────────────────────────── modo técnico — UI principal

def render_main_technical() -> None:
    """UI completa do modo técnico (ex-main(), ramo else)."""

    st.title("DMS-Educação × Censo Escolar (contexto municipal)")
    st.caption(
        "**Modo simples** orienta pelo município e pré-mapeia colunas típicas de INEP. "
        "**Modo avançado** expõe todos os diagnósticos e mapeamentos explícitos."
    )

    up_dms, up_escola, up_mat = ui_upload.render_secao_carregar_arquivos(compacto_operacional=False)

    ux_mode_choice = st.radio(
        "**Modo de trabalho**",
        [UX_SIMPLES, UX_AVANCADO],
        horizontal=False, key="ux_flow_mode_radio",
        help="O modo simples esconde mapeamentos até ser preciso corrigi-los manualmente.",
    )
    ui_simples = ux_mode_choice == UX_SIMPLES

    dms_bundle    = load_bundle(DatasetKind.DMS_EDUCACAO,    up_dms,    "DMS-Educação")
    escola_bundle = load_bundle(DatasetKind.CENSO_ESCOLA,    up_escola, "Censo Escola")
    mat_bundle    = load_bundle(DatasetKind.CENSO_MATRICULA, up_mat,    "Censo Matrícula")

    dms_df: pd.DataFrame | None = None
    dms_meta: dict[str, Any]   = {}
    df_escola: pd.DataFrame | None = None
    df_mat: pd.DataFrame | None    = None

    if dms_bundle:
        dms_df   = dms_bundle["dataframe"]
        dms_meta = dms_bundle.get("meta") or {}
    if escola_bundle:
        df_escola = escola_bundle["dataframe"]
    if mat_bundle:
        df_mat = mat_bundle["dataframe"]

    # ── Previews
    pv1, pv2, pv3 = st.columns(3)
    preview_h = 260 if ui_simples else 320
    with pv1:
        st.subheader("Pré-visualização — DMS")
        if dms_df is None:
            st.info("Sem ficheiro.")
        else:
            st.success(f"`{up_dms.name}` · {len(dms_df):,} × {len(dms_df.columns)}")
            if dms_meta and not ui_simples:
                with st.expander("Meta cabeçalho export DMS"):
                    st.json({k: v for k, v in dms_meta.items() if k != "columns"})
            st.markdown("**Amostra** — primeiras linhas")
            st.dataframe(dms_df.head(14), use_container_width=True, height=preview_h)
    with pv2:
        st.subheader("Pré-visualização — Escola")
        if df_escola is None:
            st.info("Sem ficheiro.")
        else:
            st.success(f"`{up_escola.name}` · {len(df_escola):,} × {len(df_escola.columns)}")
            st.markdown("**Amostra** — antes do recorte municipal")
            st.dataframe(df_escola.head(14), use_container_width=True, height=preview_h)
    with pv3:
        st.subheader("Pré-visualização — Matrícula")
        if df_mat is None:
            st.info("Sem ficheiro (opcional).")
        else:
            st.success(f"`{up_mat.name}` · {len(df_mat):,} × {len(df_mat.columns)}")
            st.markdown("**Amostra**")
            st.dataframe(df_mat.head(14), use_container_width=True, height=preview_h)

    # ── Mapeamentos
    st.divider()
    st.header("2. Orientar Escola ▸ Matrícula ao município")
    cols_esc = list(map(str, df_escola.columns)) if df_escola is not None else []

    if not ui_simples and df_escola is not None:
        st.markdown("#### Mapeamento manual (somente modo avançado)")
        render_logical_mapper("Tabela Escola",    df_escola, CENSO_ESCOLA_FIELDS,    "logical_escola")
    if not ui_simples and df_mat is not None:
        render_logical_mapper("Tabela Matrícula", df_mat,    CENSO_MATRICULA_FIELDS, "logical_matricula")

    if df_escola is None:
        st.warning("Precisamos do ficheiro **Escola** para continuar até à consolidação.")
        return

    resolved_escola_map = resolve_escola_mapping(cols_esc, ui_simples=ui_simples)
    co_auto = resolved_escola_map.get("CO_ENTIDADE")
    if co_auto:
        st.success(
            f"**Identificação automática.** A coluna **`{co_auto}`** ligada ao papel lógico `CO_ENTIDADE`."
        )

    geo_ctx_snapshot   = render_etapa0_contexto_municipal(df_escola, resolved_escola_map, ui_simples=ui_simples)
    exercise_year_ctx  = int(geo_ctx_snapshot["exercise"])
    st.session_state["_last_geo_ctx"] = geo_ctx_snapshot

    btn_consolidation = (
        "Gerar base integrada municipal (Escola ⊕ Matrícula)"
        if ui_simples else "Consolidar Censo municipal (Escola ⊕ Matrícula)"
    )
    if st.button(btn_consolidation, type="primary", key="btn_consolidar_censo"):
        map_e_click = resolve_escola_mapping(cols_esc, ui_simples=ui_simples)
        map_m_click = resolve_matricula_mapping(
            list(map(str, df_mat.columns)) if df_mat is not None else None,
            ui_simples=ui_simples,
        )

        df_esc_eff  = df_escola.copy()
        df_mat_eff  = df_mat.copy() if df_mat is not None else None

        if (
            geo_ctx_snapshot.get("filtro_ativo")
            and geo_ctx_snapshot.get("uf")
            and geo_ctx_snapshot.get("mun_code")
        ):
            df_esc_eff, filt_stats = filter_escola_by_municipality(
                df_esc_eff, map_e_click,
                uf_escolha=str(geo_ctx_snapshot["uf"]),
                municipio_codigo=str(geo_ctx_snapshot["mun_code"]),
            )
            motivo = filt_stats.get("motivo", "")
            if motivo:
                st.warning(f"Filtro municipal não aplicável: **{motivo}**")
            else:
                st.info(
                    f"Linhas Escola **antes ▸ depois** do filtro: "
                    f"`{filt_stats.get('antes'):,}` ▸ `{filt_stats.get('depois'):,}`."
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

        phys_co_esc = map_e_click.get("CO_ENTIDADE")
        if isinstance(df_mat_eff, pd.DataFrame) and phys_co_esc and phys_co_esc in df_esc_eff.columns:
            entidades = set(normalize_co_entidade(df_esc_eff[phys_co_esc]).tolist())
            entidades.discard("")
            phys_co_mat = map_m_click.get("CO_ENTIDADE")
            if phys_co_mat and isinstance(phys_co_mat, str) and phys_co_mat in df_mat_eff.columns and entidades:
                df_mat_eff_, mat_trim = restrict_matricula_to_entidades(df_mat_eff, phys_co_mat, entidades)
                df_mat_eff = df_mat_eff_
                st.success(
                    f"Matrículas também **recortadas** ao mesmo conjunto de escolas municipais "
                    f"(`{phys_co_mat}`): {mat_trim['antes_mat']:,} → {mat_trim['depois_mat']:,} linhas."
                )

        fn_esc = up_escola.name if up_escola else ""
        fn_mat = up_mat.name if up_mat else ""
        try:
            merged_out = consolidate_census_escolar(
                df_esc_eff, df_mat_eff, map_e_click, map_m_click, int(exercise_year_ctx),
                source_escola_label=fn_esc, source_matricula_label=fn_mat or "",
            )
        except CensusMergeError as exc:
            st.error(str(exc))
            LOG.warning("Consolidação Censo recusada: %s", exc)
        except Exception as exc:  # pylint: disable=broad-except
            st.error("Erro inesperado na consolidação.")
            LOG.exception("merge censo")
            st.code(str(exc))
        else:
            merged_final = append_censo_context_columns(merged_out, geo_ctx_snapshot)
            stale_sig = tuple(sorted({
                ("uf_pick",     geo_ctx_snapshot.get("uf")),
                ("mun_pick",    geo_ctx_snapshot.get("mun_code")),
                ("filtro_geo",  geo_ctx_snapshot.get("filtro_ativo")),
                ("skip_geo_flag", geo_ctx_snapshot.get("skip_geo")),
            }))
            PipelineState.set_censo_consolidado(
                merged_final,
                (fn_esc, fn_mat or "", int(exercise_year_ctx), bool(ui_simples),
                 tuple(sorted(map_e_click.items())), tuple(sorted(map_m_click.items())), stale_sig),
            )
            LOG.debug("Mapeamento escola: %s | matricula: %s", map_e_click, map_m_click)
            st.success(
                f"**Base Escola⊕Matrícula** municipal (**{len(merged_final.index):,}** linhas) com metadados de contexto."
            )

    # ── Mostrar consolidado se disponível
    censo_consolidado = PipelineState.censo_consolidado()
    sig_store         = st.session_state.get(SessionKeys.CENSO_CONS_SIG)

    resolved_now = resolve_escola_mapping(list(map(str, df_escola.columns)), ui_simples=ui_simples)
    map_m_live   = resolve_matricula_mapping(
        list(map(str, df_mat.columns)) if df_mat is not None else None,
        ui_simples=ui_simples,
    )
    stale_now = tuple(sorted({
        ("uf_pick",      geo_ctx_snapshot.get("uf")),
        ("mun_pick",     geo_ctx_snapshot.get("mun_code")),
        ("filtro_geo",   geo_ctx_snapshot.get("filtro_ativo")),
        ("skip_geo_flag", geo_ctx_snapshot.get("skip_geo")),
    }))
    current_sig = (
        up_escola.name if up_escola else "",
        up_mat.name if up_mat else "",
        int(geo_ctx_snapshot["exercise"]),
        bool(ui_simples),
        tuple(sorted(resolved_now.items())),
        tuple(sorted(map_m_live.items())),
        stale_now,
    )

    if isinstance(censo_consolidado, pd.DataFrame):
        st.subheader("Base Escola⊕Matrícula — disponível nesta sessão")
        meta_cols = [
            c for c in censo_consolidado.columns
            if str(c).startswith(("censo_", "censo_ctx_")) or str(c) == "censo_exercicio"
        ]
        if meta_cols:
            st.caption("Metadados: " + ", ".join(f"`{c}`" for c in meta_cols[:11]))
        st.dataframe(censo_consolidado.head(30), use_container_width=True, height=360)
        if sig_store is not None and sig_store != current_sig:
            st.warning(
                "Os ficheiros, modo UX, mapeamentos **ou** o contexto municipal/exercício mudaram "
                "**desde a última consolidação bem-sucedida**. Clique novamente para manter a base alinhada."
            )

    # ── DMS / Etapas 2 e 3
    st.divider()
    st.header("3. Cruzamento com DMS")
    _maybe_continue_dms_etapas(dms_df, censo_consolidado, ui_simples, up_dms, up_escola)
