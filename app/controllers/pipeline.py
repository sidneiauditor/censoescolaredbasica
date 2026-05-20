"""
Funções de pipeline reutilizáveis — Etapa 2 (CNPJ mapping) e Etapa 3 (merge CNPJ + texto).

Usado tanto pelo controller técnico como pelo operacional.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

import pandas as pd
import streamlit as st

from config import APP_DIR, SELECT_SENTINEL
from controllers.common import write_xlsx_async, versioned_output_path
from domain.audit import MergeAuditRecord
from domain.census_logical import CENSO_ESCOLA_FIELDS, CENSO_MATRICULA_FIELDS, LogicalFieldSpec
from services.cnpj_aggregation import filter_censo_for_fiscal_panel
from services.cnpj_merge import (
    CNPJ_INVALIDO_DMS,
    MATCH_CNPJ_EXATO,
    MATCH_MULTIPLAS_ESCOLAS,
    MATCH_TEXTO_COMPLEMENTAR,
    ORDEM_COLUMN,
    SEM_CNPJ_DMS,
    SEM_CORRESP_CNPJ,
    SEM_CORRESP_TEXTO,
    compute_merge_debug_snapshot,
    deterministic_merge_by_cnpj,
    merge_status_qualifies_textual_complement,
    stitch_complementary_textual_into_base,
)
from services.cnpj_root_aggregation import dataframe_with_cnpj_raiz
from services.divergencia_export_service import exportar_divergencias_operacionais
from services.indicators import (
    COL_BASE_PM,
    COL_ISS_PM,
    COL_MSG_PM,
    add_basic_fiscal_indicators,
)
from services.inferred_mapping import (
    infer_dms_cnpj_column_operacional,
    propose_dms_mapping,
    propose_escola_mapping,
    propose_matricula_mapping,
    resolve_census_cnpj_physical_column,
)
from services.text_fuzzy_merge import run_textual_fuzzy_merge
from state.pipeline_state import PipelineState, SessionKeys
from ui.components import render_cnpj_stats_block
from ui.dashboard import render_dashboard_ranking_fiscal
from utils.cnpj import add_normalized_cnpj_column

LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────── helpers de UI

def column_options(df: pd.DataFrame | None) -> list[str]:
    if df is None or df.empty:
        return [SELECT_SENTINEL]
    return [SELECT_SENTINEL] + [str(c) for c in df.columns]


def collect_logical_mapping(prefix_key: str, specs: tuple[LogicalFieldSpec, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for spec in specs:
        val = st.session_state.get(f"{prefix_key}_{spec.key}", SELECT_SENTINEL)
        if val != SELECT_SENTINEL:
            result[spec.key] = val
    return result


def resolve_escola_mapping(cols_escola: list[str], *, ui_simples: bool) -> dict[str, str]:
    proposals = propose_escola_mapping(list(cols_escola))
    if ui_simples:
        return proposals
    merged: dict[str, str] = dict(proposals)
    widgets = collect_logical_mapping("logical_escola", CENSO_ESCOLA_FIELDS)
    for logical_key, physical in widgets.items():
        if physical and physical != SELECT_SENTINEL and physical in cols_escola:
            merged[logical_key] = physical
    return merged


def resolve_matricula_mapping(cols_mat: list[str] | None, *, ui_simples: bool) -> dict[str, str]:
    if not cols_mat:
        return {}
    proposals = propose_matricula_mapping(list(cols_mat))
    if ui_simples:
        return proposals
    merged: dict[str, str] = dict(proposals)
    widgets = collect_logical_mapping("logical_matricula", CENSO_MATRICULA_FIELDS)
    for logical_key, physical in widgets.items():
        if physical and physical != SELECT_SENTINEL and physical in cols_mat:
            merged[logical_key] = physical
    return merged


def render_logical_mapper(
    titulo: str,
    df: pd.DataFrame,
    specs: tuple[LogicalFieldSpec, ...],
    prefix_key: str,
) -> None:
    st.markdown(f"#### {titulo}")
    opts = column_options(df)
    for spec in specs:
        obrig = "**obrigatório**" if getattr(spec, "obrigatorio_escola", False) or getattr(
            spec, "obrigatorio_matricula", False
        ) else "opcional"
        st.selectbox(
            f"`{spec.key}` ({obrig}) — {spec.description_pt}",
            opts,
            key=f"{prefix_key}_{spec.key}",
        )


def default_select_index(options: list[str], preferred: str | None) -> int:
    if preferred and preferred in options:
        return options.index(preferred)
    return 0


def _series_nonempty_cell_count(series: pd.Series) -> int:
    mask = series.notna()
    strv = series.astype(str).str.strip()
    mask &= strv.ne("") & ~strv.str.lower().isin(["nan", "none"])
    return int(mask.sum())


# ─────────────────────────────────────────────────────── ensure_normalized_cnpj_workframes

def ensure_normalized_cnpj_workframes(
    *,
    column_map: dict[str, Any],
    df_dms: pd.DataFrame,
    df_censo_consolidado: pd.DataFrame,
    inferencia_operacional_dms: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]] | None:
    """
    Etapa obrigatória antes do merge: cria ``__cnpj_norm_dms`` e ``__cnpj_norm_censo``.

    ``inferencia_operacional_dms=True`` tenta primeiro a heurística operacional Salvador;
    caso falhe, cai para ``propose_dms_mapping`` como no modo técnico.
    """

    cm_out: dict[str, Any] = dict(column_map or {})
    d_cols = {str(c) for c in df_dms.columns}

    def _dms_pick_invalid(pick_val: object) -> bool:
        return (
            not isinstance(pick_val, str)
            or pick_val == SELECT_SENTINEL
            or not str(pick_val).strip()
            or str(pick_val) not in d_cols
        )

    dms_pick = cm_out.get("dms_cnpj")

    if _dms_pick_invalid(dms_pick) and inferencia_operacional_dms:
        op_hit, metodo_op = infer_dms_cnpj_column_operacional([str(c) for c in df_dms.columns])
        if op_hit and op_hit in d_cols:
            dms_pick = op_hit
            cm_out["dms_cnpj"] = op_hit
            LOG.info("DMS CNPJ inferido (operacional) — coluna=%r método=%s", op_hit, metodo_op)

    if _dms_pick_invalid(dms_pick):
        inferred_d = propose_dms_mapping([str(c) for c in df_dms.columns]).get("CNPJ")
        if inferred_d and inferred_d in df_dms.columns:
            dms_pick = inferred_d
            cm_out["dms_cnpj"] = inferred_d
            LOG.info("DMS CNPJ inferido — coluna=%r (propose_dms_mapping)", inferred_d)
        else:
            if inferencia_operacional_dms:
                st.error(
                    "**DMS**: não foi possível identificar uma coluna de **CNPJ** reconhecível neste extrato. "
                    "Abra **Técnico / diagnóstico completo** e associe manualmente na **Etapa 2**."
                )
            else:
                st.error(
                    "**DMS**: não conseguimos localizar uma coluna de CNPJ física válida. Na Etapa 2 "
                    "escolha manualmente a coluna do contribuinte."
                )
            return None

    censo_pick = cm_out.get("censo_cnpj")
    if (
        not isinstance(censo_pick, str)
        or censo_pick == SELECT_SENTINEL
        or not censo_pick.strip()
        or censo_pick not in df_censo_consolidado.columns
    ):
        resolved_c = resolve_census_cnpj_physical_column(df_censo_consolidado.columns)
        if resolved_c:
            censo_pick = resolved_c
            cm_out["censo_cnpj"] = resolved_c
            LOG.info("Coluna CNPJ censo inferida antes do merge: `%s`.", resolved_c)
        else:
            st.error(
                "**Censo municipal sem coluna CNPJ reconhecida.** Inclua o campo lógico **`CNPJ`** no mapeamento "
                "da Escola (INEP) ou verifique se o consolidado expõe ``CNPJ_base_escola``."
            )
            return None

    dms_work  = add_normalized_cnpj_column(df_dms,             dms_pick,   "__cnpj_norm_dms")
    censo_work = add_normalized_cnpj_column(df_censo_consolidado, censo_pick, "__cnpj_norm_censo")

    if "__cnpj_norm_dms" not in dms_work.columns or "__cnpj_norm_censo" not in censo_work.columns:
        st.error("Falha ao materializar colunas internas ``__cnpj_norm_*`` — contacte a equipa técnica.")
        return None

    norm_censo = censo_work["__cnpj_norm_censo"].astype(str).str.strip()
    if not (norm_censo.str.len() > 0).any():
        st.warning(
            f"A coluna **`{censo_pick}`** existe, mas **não há CNPJ normalizável** (14 dígitos) nas linhas — "
            "confirme o mapeamento ou se a base municipal contém PJ com número de inscrição."
        )

    return dms_work, censo_work, cm_out


# ─────────────────────────────────────────────────────── Etapa 2

def run_etapa2_mapping(
    dms_df: pd.DataFrame,
    censo_df: pd.DataFrame,
    *,
    ui_simples: bool,
    up_dms_name: str,
    up_escola_name: str,
) -> None:
    """Seleção compacta das colunas de ligação DMS ↔ Censo + normalização de CNPJ."""

    st.divider()
    st.header("Etapa 2 — Ligação fiscal com o Censo municipal")
    if ui_simples:
        st.markdown(
            "O passo anterior já **nomeou logicamente** o CNPJ da escola, o nome público (`NO_ENTIDADE`) e as "
            "matrículas sempre que foram reconhecidos no ficheiro INEP ou no mapeamento avançado. "
            "Aqui concentramo-nos apenas em **alinhar os campos ao export da DMS**."
        )
    else:
        st.caption(
            "As colunas do Censo consolidado mantêm nomes estáveis (`CNPJ`, `NO_ENTIDADE`, `matriculas`, …). "
            "Os CNPJs são normalizados e validados (DV)."
        )

    sig_uid = "|".join((
        up_dms_name,
        up_escola_name,
        str(len(dms_df.index)),
        str(len(censo_df.index)),
        ",".join(map(str, list(censo_df.columns[:16]))),
    ))
    if st.session_state.get(SessionKeys.ETAPA2_SIG) != sig_uid:
        st.session_state[SessionKeys.ETAPA2_SIG] = sig_uid
        for k in ("map_dms_cnpj", "map_dms_razao", "map_dms_qtd", "map_censo_cnpj", "map_censo_nome", "map_censo_mat"):
            st.session_state.pop(k, None)
        dm = propose_dms_mapping([str(c) for c in dms_df.columns])
        cols_d = set(map(str, dms_df.columns))
        cols_c = set(map(str, censo_df.columns))
        for alias_key, state_key in (("CNPJ", "map_dms_cnpj"), ("razao_social", "map_dms_razao"), ("quantidade", "map_dms_qtd")):
            hit = dm.get(alias_key)
            if isinstance(hit, str) and hit in cols_d:
                st.session_state[state_key] = hit
        hit_cnpj_cons = resolve_census_cnpj_physical_column(list(cols_c))
        if hit_cnpj_cons:
            st.session_state["map_censo_cnpj"] = hit_cnpj_cons
        if "NO_ENTIDADE" in cols_c:
            st.session_state["map_censo_nome"] = "NO_ENTIDADE"
        if "matriculas" in cols_c:
            st.session_state["map_censo_mat"] = "matriculas"

    opts_d = column_options(dms_df)
    opts_c = column_options(censo_df)
    c1, c2 = st.columns(2)

    with c1:
        st.subheader("DMS-Educação")
        dms_cnpj = st.selectbox("Coluna que contém o CNPJ jurídico", opts_d, key="map_dms_cnpj",
                                help='Alias comuns: ``CNPJ``, ``NU_CNPJ``, …')
        dms_razao = st.selectbox("Texto institucional (razão ou nome próximo)", opts_d, key="map_dms_razao",
                                 help='Usado mais tarde no matching textual.')
        dms_qtd = st.selectbox("Quantidade relacionada ao serviço/alunos (opcional)", opts_d, key="map_dms_qtd")

    with c2:
        st.subheader("Censo (já com colunas lógicas quando possível)")
        censo_cnpj = st.selectbox("CNPJ institucional do Censo", opts_c, key="map_censo_cnpj")
        censo_nome = st.selectbox("Denominação pública reconhecível", opts_c, key="map_censo_nome")
        censo_mat  = st.selectbox("Campo agregado de matrículas (opcional)", opts_c, key="map_censo_mat")

    diag_exp = st.expander("Diagnóstico de CNPJ (modo avançado)", expanded=not ui_simples)
    with diag_exp:
        left, right = st.columns(2)
        with left:
            render_cnpj_stats_block("DMS", dms_df, dms_cnpj)
        with right:
            render_cnpj_stats_block("Censo municipal", censo_df, censo_cnpj)

    if ui_simples:
        with st.expander("Ajustar campos manualmente (se o ficheiro DMS tiver nomes atípicos)"):
            st.caption("Só precisa de alterar se a deteção automática não bater com o layout real.")

    if (
        dms_cnpj != SELECT_SENTINEL and censo_cnpj != SELECT_SENTINEL
        and dms_cnpj in dms_df.columns and censo_cnpj in censo_df.columns
    ):
        dms_work  = add_normalized_cnpj_column(dms_df,  dms_cnpj,  "__cnpj_norm_dms")
        censo_work = add_normalized_cnpj_column(censo_df, censo_cnpj, "__cnpj_norm_censo")
        st.session_state[SessionKeys.DMS_WORK]   = dms_work
        st.session_state[SessionKeys.CENSO_WORK] = censo_work
        st.session_state[SessionKeys.COLUMN_MAP] = {
            "dms_cnpj": dms_cnpj, "dms_razao": dms_razao, "dms_qtd": dms_qtd,
            "censo_cnpj": censo_cnpj, "censo_nome": censo_nome, "censo_mat": censo_mat,
        }
        if ui_simples:
            st.success(
                "**Etapa 2 OK.** CNPJ guardado em ``__cnpj_norm_*`` — na Etapa 3 o merge vai primeiro por esse "
                "número (14 dígitos) e o texto (RapidFuzz) aparece apenas se a linha não tiver CNPJ DMS válido."
            )
        else:
            sample = dms_work[[dms_cnpj, "__cnpj_norm_dms"]].head(8)
            with st.expander("Pré-visualização de CNPJ normalizado (DMS — primeiras linhas)"):
                st.dataframe(sample, use_container_width=True, hide_index=True)
        LOG.info("Mapa da Etapa 2 persistido em session_state.")
    else:
        for key in (SessionKeys.DMS_WORK, SessionKeys.CENSO_WORK, SessionKeys.COLUMN_MAP):
            st.session_state.pop(key, None)
        if ui_simples:
            st.info("Escolha o **CNPJ** na base DMS e no Censo — é o único par obrigatório para continuar.")
        else:
            st.info(
                "Seleccione o par **CNPJ** nas duas bases para normalizar dígitos (``__cnpj_norm_*``) — "
                "é o primeiro passo obrigatório antes do merge determinístico da Etapa 3."
            )


# ─────────────────────────────────────────────────────── Etapa 3 — diagnóstico

def render_etapa3_premerge_diagnostics(
    dms_work: pd.DataFrame,
    censo_work: pd.DataFrame,
    cm: dict[str, Any],
) -> None:
    with st.expander("**Diagnóstico pré-merge** — dados entregues à Etapa 3", expanded=True):
        st.markdown("##### Colunas em `dms_work`")
        st.code("\n".join(map(str, dms_work.columns)), language="text")
        st.caption(
            f"**{len(dms_work.columns)}** colunas · **{len(dms_work.index):,}** linhas · "
            f"`__cnpj_norm_dms` presente: **{'sim' if '__cnpj_norm_dms' in dms_work.columns else 'não'}**"
        )
        st.markdown("##### Colunas em `censo_work`")
        st.code("\n".join(map(str, censo_work.columns)), language="text")
        st.caption(
            f"**{len(censo_work.columns)}** colunas · **{len(censo_work.index):,}** linhas · "
            f"`__cnpj_norm_censo` presente: **{'sim' if '__cnpj_norm_censo' in censo_work.columns else 'não'}**"
        )

        col_censo_fis = cm.get("censo_cnpj")
        st.markdown("##### CNPJ do Censo — coluna física e preenchimento")
        if isinstance(col_censo_fis, str) and col_censo_fis.strip() and col_censo_fis != SELECT_SENTINEL:
            st.write(f"**`column_map.censo_cnpj`:** `{col_censo_fis}`")
            if col_censo_fis in censo_work.columns:
                n_nonempty = _series_nonempty_cell_count(censo_work[col_censo_fis])
                st.metric("Valores não vazios", f"{n_nonempty:,} / {len(censo_work.index):,}")
            else:
                st.warning(
                    f"A coluna **`{col_censo_fis}`** está no mapeamento mas **não existe** em `censo_work`. "
                    "**Reconsolide** o Censo ou confira os nomes listados acima."
                )
        else:
            st.info("O CNPJ físico do Censo **não está definido** em `column_map` (Etapa 2).")

        st.markdown("##### Coluna interna `__cnpj_norm_censo`")
        if "__cnpj_norm_censo" in censo_work.columns:
            s_norm = censo_work["__cnpj_norm_censo"]
            n_filled = int((s_norm.fillna("").astype(str).str.strip().ne("")).sum())
            st.success("A coluna **`__cnpj_norm_censo`** existe.")
            st.write(f"- **dtype:** `{s_norm.dtype}`")
            st.metric("Linhas com normalização não vazia", f"{n_filled:,} / {len(censo_work.index):,}")
            if (
                isinstance(col_censo_fis, str) and col_censo_fis.strip()
                and col_censo_fis != SELECT_SENTINEL and col_censo_fis in censo_work.columns
            ):
                preview = censo_work[[col_censo_fis, "__cnpj_norm_censo"]].head(25)
                st.markdown("**Pré-visualização:** CNPJ original × `__cnpj_norm_censo` (até 25 linhas)")
                st.dataframe(preview, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "**`__cnpj_norm_censo` não existe** neste `DataFrame`. Causas prováveis:\n\n"
                "- A Etapa 2 não ficou com um **par CNPJ válido** ou os `selectbox` ainda estão no sentinel.\n"
                "- O passo `ensure_normalized_cnpj_workframes` não correu depois da Etapa 2 ou falhou.\n"
                "- O consolidado foi **alterado** sem reexecutar o encadeamento.\n\n"
                "Corrija na **Etapa 2**, **consolide** de novo o Censo se mudou o contexto."
            )


# ─────────────────────────────────────────────────────── Etapa 3 — pipeline completo

def run_etapa3_merge_pipeline(
    dms_work: pd.DataFrame,
    censo_work: pd.DataFrame,
    *,
    ux_simples: bool = False,
) -> None:
    """Pipeline Etapa 3: merge determinístico por CNPJ + texto opcional."""

    st.divider()
    if ux_simples:
        st.header("Cruzamento de dados — DMS × Censo (chave fiscal CNPJ)")
    else:
        st.header("Etapa 3 — Cruzamento DMS × Censo (**CNPJ primeiro, confiança alta**)")
    st.markdown(
        "1. **Chave CNPJ determinística** — apenas dígitos, **14 posições** (zfill onde necessário).\n"
        "2. **Classificação** por linha DMS: correspondência única, várias escolas no Censo com o mesmo número, "
        "falta na base municipal, campo vazio/inutilizável ou CNPJ DMS inválido por regras DV.\n"
        "3. **RapidFuzz** aparece apenas como passe **complementar** nas linhas sem CNPJ utilizável na DMS."
    )
    if not ux_simples:
        with st.expander("Arquitetura determinística (para equipa técnica)"):
            st.markdown(
                "- **Join lógico** — esquerda sempre a fatia inteira da DMS; direito `lookup_first.groupby(...).head(1)`, "
                "com `cnpj_censo_candidatos_mesmo_numero` sempre visível.\n"
                "- **`merge_confianca = alta_conf_cnpj_exato`** — exclusivo onde existe unicidade bilateral.\n"
                "- **`merge_metodo_primario`** — `cnpj_14_digitos` onde faz sentido usar a chave fiscal."
            )

    cm = st.session_state.get(SessionKeys.COLUMN_MAP) or {}
    render_etapa3_premerge_diagnostics(dms_work, censo_work, cm)

    col_dms_raw = cm.get("dms_cnpj")
    opts_dms   = column_options(dms_work)
    opts_censo = column_options(censo_work)

    sig_det_now = (
        str(col_dms_raw), str(cm.get("censo_cnpj")),
        len(dms_work.index), len(censo_work.index),
        "__cnpj_norm_dms" in dms_work.columns,
        "__cnpj_norm_censo" in censo_work.columns,
    )

    merge_bloqueado = False
    if not col_dms_raw or col_dms_raw == SELECT_SENTINEL:
        st.error("Finalize a Etapa 2 definindo explicitamente as colunas de CNPJ DMS.")
        merge_bloqueado = True
    elif "__cnpj_norm_dms" not in dms_work.columns:
        st.error(
            "Falta ``__cnpj_norm_dms`` na DMS transformada — recarregue os ficheiros ou limpe cache e "
            "gere novamente a Etapa 2."
        )
        merge_bloqueado = True
    elif "__cnpj_norm_censo" not in censo_work.columns:
        st.error(
            "**Merge indisponível:** falta ``__cnpj_norm_censo`` na base municipal de trabalho. "
            "Consulte o **diagnóstico pré-merge** acima para causas prováveis."
        )
        merge_bloqueado = True

    det_clicked = False
    if not merge_bloqueado:
        merge_btn_label = (
            "Cruzar dados fiscais (CNPJ 14 dígitos)" if ux_simples
            else "Executar merge determinístico (CNPJ 14 dígitos)"
        )
        det_clicked = st.button(merge_btn_label, type="primary", key="btn_etapa3_merge_cnpj")

    if det_clicked:
        prog = st.progress(0)

        def _cb_det(p: float) -> None:
            prog.progress(min(max(p, 0.0), 1.0))

        merge_snap = compute_merge_debug_snapshot(
            dms_work, censo_work,
            col_dms_norm="__cnpj_norm_dms", col_censo_norm="__cnpj_norm_censo",
        )
        LOG.info("Etapa 3 — snapshot antes do merge: %s", merge_snap)

        try:
            consolidado_cnpj, summary_cnpj = deterministic_merge_by_cnpj(
                dms_work, censo_work,
                col_dms_raw_cnpj=str(col_dms_raw),
                col_dms_norm="__cnpj_norm_dms",
                col_censo_norm="__cnpj_norm_censo",
                progress_callback=_cb_det,
            )
        except Exception as exc:  # pylint: disable=broad-except
            prog.empty()
            tb_full = traceback.format_exc()
            st.error("**Erro durante o merge determinístico por CNPJ.**")
            LOG.exception("Etapa 3 — deterministic merge")
            with st.expander("**Debug — exceção e traceback**", expanded=True):
                st.markdown(f"**Tipo:** `{type(exc).__name__}` — **Mensagem:** `{exc}`")
                cause = getattr(exc, "__cause__", None)
                if cause:
                    st.markdown(f"**`__cause__`:** `{type(cause).__name__}` — `{cause}`")
                st.code(tb_full, language="text")
            with st.expander("**Diagnóstico numérico**", expanded=True):
                c1, c2 = st.columns(2)
                c1.metric("`dms_work` shape", str(merge_snap.get("dms_shape")))
                c2.metric("`censo_work` shape", str(merge_snap.get("censo_shape")))
                st.json(merge_snap)
            return

        prog.empty()
        cm_for_ind = dict(st.session_state.get(SessionKeys.COLUMN_MAP) or {})
        consolidado_cnpj, _report61 = add_basic_fiscal_indicators(consolidado_cnpj, cm_for_ind)
        PipelineState.set_merge_result(consolidado_cnpj, summary_cnpj, sig_det_now)

        out_path    = versioned_output_path("consolidado")
        fixed_path  = APP_DIR / "outputs" / "consolidado.xlsx"
        write_xlsx_async(consolidado_cnpj, out_path)
        write_xlsx_async(consolidado_cnpj, fixed_path)
        st.success(f"**Merge por CNPJ concluído.** Excel sendo gravado em `outputs/`.")

        _geo = st.session_state.get("_last_geo_ctx") or {}
        _audit = MergeAuditRecord.from_summary(
            summary_cnpj,
            modo_ui="tecnico",
            exercise=PipelineState.exercise(),
            municipio_codigo=str(_geo.get("mun_code", "")),
            municipio_label=str(_geo.get("mun_label", "")),
            uf=str(_geo.get("uf", "")),
            n_dms_linhas=len(dms_work.index),
            n_censo_consolidado_linhas=len(censo_work.index),
            cnpj_col_dms=str(cm.get("dms_cnpj", "")),
            cnpj_col_censo=str(cm.get("censo_cnpj", "")),
            dms_hash=PipelineState.get_file_hash("dms") or "",
            escola_hash=PipelineState.get_file_hash("escola") or "",
            matricula_hash=PipelineState.get_file_hash("matricula") or "",
        )
        _audit.append_to_log(APP_DIR / "outputs" / "audit_log.jsonl")

    if merge_bloqueado:
        st.info(
            "O merge determinístico **não está disponível** até existirem as colunas internas "
            "``__cnpj_norm_dms`` e ``__cnpj_norm_censo``. Corrija conforme o diagnóstico pré-merge."
        )
        return

    consolidado_raw = PipelineState.consolidado()
    summary_det     = st.session_state.get(SessionKeys.ETAPA3_SUMMARY)
    sdet            = st.session_state.get(SessionKeys.ETAPA3_SIG)

    if consolidado_raw is None or summary_det is None:
        lbl = "Cruzar dados fiscais (CNPJ 14 dígitos)" if ux_simples else "Executar merge determinístico (CNPJ 14 dígitos)"
        st.info(f"Clique **{lbl}** para gerar a **base integrada**.")
        return

    if isinstance(sdet, tuple) and sdet != sig_det_now:
        st.warning(
            "**Colunas CNPJ ou tamanhos das bases mudaram** face ao último merge determinístico. "
            "Volte a executar **merge por CNPJ (14 dígitos)** para manter dados consistentes."
        )

    consolidado_raw, etapa61_report = add_basic_fiscal_indicators(
        consolidado_raw, dict(cm or {}),
    )
    st.session_state[SessionKeys.CONSOLIDADO_DF] = consolidado_raw

    texto_elegivel_n = 0
    if "match_status_principal" in consolidado_raw.columns:
        mascara_texto_opt = (
            consolidado_raw["match_status_principal"].astype(str)
            .apply(merge_status_qualifies_textual_complement)
            .fillna(False)
        )
        texto_elegivel_n = int(mascara_texto_opt.sum())

    with st.expander(
        "**Texto opcional (RapidFuzz)** — apenas linhas sem CNPJ utilizável na DMS",
        expanded=not ux_simples,
    ):
        st.caption(
            f"**Complementar.** `{texto_elegivel_n}` linha(s) atualmente elegíveis — "
            "todas as outras categorias ficam definidas apenas pela **chave fiscal CNPJ**."
        )
        col_razao = st.selectbox(
            "Texto institucional na DMS", opts_dms,
            index=default_select_index(opts_dms, cm.get("dms_razao")),
            key="etapa3_col_dms_razao",
        )
        col_nome = st.selectbox(
            "Denominação pública escola (Censo)", opts_censo,
            index=default_select_index(opts_censo, cm.get("censo_nome")),
            key="etapa3_col_censo_nome",
        )
        cutoff = st.radio(
            "Pontuação mínima WRatio RapidFuzz (0–100)",
            options=[70, 80, 90], index=1, horizontal=True, key="etapa3_cutoff",
        )

        texto_pronto = col_razao != SELECT_SENTINEL and col_nome != SELECT_SENTINEL and texto_elegivel_n > 0
        if not texto_pronto and texto_elegivel_n == 0:
            st.info("Nesta execução **não há** linhas só com fallback textual.")
        elif not texto_pronto:
            st.info("Seleccione as duas colunas de texto válidas antes de executar RapidFuzz.")

        if st.button(
            "Correr passe textual só nas linhas sem CNPJ válido",
            disabled=not texto_pronto,
            key="btn_etapa3_optional_text_run",
            type="secondary",
        ):
            mascara = (
                consolidado_raw["match_status_principal"].astype(str)
                .apply(merge_status_qualifies_textual_complement)
                .fillna(False)
            )
            if not bool(mascara.any()):
                st.warning("Nenhuma linha textual elegível após filtros deterministicos mais recentes.")
            else:
                subset_dms = dms_work.loc[mascara.to_numpy()].copy().reset_index(drop=True)
                orden_col  = pd.to_numeric(
                    consolidado_raw.loc[mascara, f"dms__{ORDEM_COLUMN}"], errors="coerce",
                ).reset_index(drop=True)
                subset_dms.loc[:, ORDEM_COLUMN] = orden_col.to_numpy(dtype=int)
                fz_prog = st.progress(0)

                def _cb_fuzz(p: float) -> None:
                    fz_prog.progress(min(max(p, 0.0), 1.0))

                try:
                    fz_resultado, _fz_summary = run_textual_fuzzy_merge(
                        subset_dms, censo_work,
                        col_dms_razao=col_razao, col_censo_nome=col_nome,
                        score_cutoff=float(cutoff), progress_callback=_cb_fuzz,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    fz_prog.empty()
                    st.error("Falha durante RapidFuzz complementar.")
                    LOG.exception("Etapa 3 — fuzzy opcional")
                    with st.expander("Detalhe técnico"):
                        st.code(str(exc))
                else:
                    fz_prog.empty()
                    consolidado_atualizado, texto_sumario = stitch_complementary_textual_into_base(
                        consolidado_raw, fz_resultado, score_cutoff_used=float(cutoff),
                    )
                    consolidado_atualizado, _ = add_basic_fiscal_indicators(
                        consolidado_atualizado,
                        dict(st.session_state.get(SessionKeys.COLUMN_MAP) or {}),
                    )
                    st.session_state[SessionKeys.CONSOLIDADO_DF]      = consolidado_atualizado
                    st.session_state[SessionKeys.ETAPA3_FUZZY_SUMMARY] = texto_sumario
                    st.session_state[SessionKeys.ETAPA3_FUZZY_SIG]    = (col_razao, col_nome, int(cutoff))

                    out_path_fz   = versioned_output_path("consolidado")
                    fixed_path_fz = APP_DIR / "outputs" / "consolidado.xlsx"
                    write_xlsx_async(consolidado_atualizado, out_path_fz)
                    write_xlsx_async(consolidado_atualizado, fixed_path_fz)
                    st.success(
                        f"**Passe texto aplicado** · {texto_sumario.matches_texto} match(es) texto / "
                        f"{texto_sumario.linhas_elegiveis} elegíveis."
                    )

    refinado = st.session_state.get(SessionKeys.CONSOLIDADO_DF)
    if not isinstance(refinado, pd.DataFrame):
        refinado = consolidado_raw
    summary_actual = summary_det

    diver_agregado = int(
        getattr(summary_actual, "multiplas_escolas_mesmo_cnpj", 0)
        + getattr(summary_actual, "cnpj_dms_invalido", 0)
        + getattr(summary_actual, "sem_correspondencia_cnpj", 0)
    )

    st.subheader("Métricas — resultado determinístico + divergências")
    r1, r2, r3, r4, r5 = st.columns(5)
    r1.metric("Merge exato CNPJ",                     f"{summary_actual.match_cnpj_exato:,}")
    r2.metric("Linhas sem CNPJ DMS válido",            f"{texto_elegivel_n:,}")
    r3.metric("Divergências agregadas",                f"{diver_agregado:,}")
    r4.metric("Várias escolas / mesmo número",         f"{summary_actual.multiplas_escolas_mesmo_cnpj:,}")
    r5.metric("Chaves Censo duplicadas (global)",      f"{summary_actual.chaves_com_multiplos_cnpj_no_censo:,}")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Sem correspondência censo",             f"{summary_actual.sem_correspondencia_cnpj:,}")
    s2.metric("DMS sem dígitos normalizados",          f"{summary_actual.sem_cnpj_dms:,}")
    s3.metric("CNPJ DMS inválido (DV/formato)",        f"{summary_actual.cnpj_dms_invalido:,}")
    s4.metric("Tempo determinístico (s)",              f"{summary_actual.tempo_segundos:.3f}")

    # ── Etapa 6.1 — Indicadores
    st.divider()
    st.header("Etapa 6 — Indicadores fiscais básicos (6.1)")
    st.markdown(
        f"Ratios por linha do consolidado: `{COL_ISS_PM}`, `{COL_MSG_PM}`, `{COL_BASE_PM}`."
    )
    cols_resolv = etapa61_report.colunas_resolvidas
    src_txt = ", ".join(f"`{l}` ← `{p or '—'}`" for l, p in cols_resolv.items())
    st.caption("Colunas físicas efectivas: " + src_txt)
    for av in etapa61_report.avisos:
        st.warning(av)

    def _fmt(v: float | None) -> str:
        return "—" if v is None else f"{v:,.6g}"

    cols_stats = sorted(etapa61_report.resumos, key=lambda x: x.nome)
    for row_i in range(0, len(cols_stats), 3):
        chunk = cols_stats[row_i : row_i + 3]
        grid  = st.columns(len(chunk))
        for j, stt in enumerate(chunk):
            with grid[j]:
                st.markdown(f"**{stt.nome}** — *n válidos*: {stt.n_validos}")
                g1, g2, g3, g4 = st.columns(4)
                g1.metric("média",   _fmt(stt.media))
                g2.metric("mediana", _fmt(stt.mediana))
                g3.metric("máximo",  _fmt(stt.maximo))
                g4.metric("mínimo",  _fmt(stt.minimo))

    ind_cols = [c for c in (COL_ISS_PM, COL_MSG_PM, COL_BASE_PM) if c in refinado.columns]
    extra_src = [cols_resolv.get(k) for k in ("matriculas_denominador", "VLIMPOSTO_numerador", "VLMENSALIDADE_numerador", "VLBASECALCULO_numerador")]
    show_ind61 = list(dict.fromkeys(ind_cols + [c for c in extra_src if isinstance(c, str) and c in refinado.columns]))
    if show_ind61:
        st.dataframe(refinado.loc[:, show_ind61].head(200), use_container_width=True, height=320)

    render_dashboard_ranking_fiscal(refinado, cm, lingua_cif_operacional=False)

    texto_extra = st.session_state.get(SessionKeys.ETAPA3_FUZZY_SUMMARY)
    if texto_extra:
        z1, z2, z3 = st.columns(3)
        z1.metric("Textual elegível",       f"{texto_extra.linhas_elegiveis:,}")
        z2.metric("Sucesso texto complement", f"{texto_extra.matches_texto:,}")
        z3.metric("Sem match textual",       f"{texto_extra.sem_correspondencia:,}")
        fz_sig = st.session_state.get(SessionKeys.ETAPA3_FUZZY_SIG)
        if fz_sig:
            fra, fro, fc = fz_sig
            st.caption(f"Último passe texto: **`{fra}` × `{fro}`** · cutoff WRatio **≥ {fc}**.")

    # ── Export
    st.divider()
    st.header("Pré-visualização + export (consolidado)")
    filt = st.radio(
        "Segmentar resultado",
        ["Todos", "Só alta confiança (match_cnpj_exato)",
         "Divergência — várias escolas / mesmo número",
         "Sem correspondência censo mesmo com CNPJ DMS válido",
         "Sem CNPJ normalizável na DMS (+ inválidos — elegível ao texto opcional)",
         "Só passe textual complementar",
         "Linhas mesmo sem resultado textual opcional aplicado"],
        horizontal=True, key="etapa3_preview_filter",
    )
    view_df = refinado
    estado = refinado["match_status_principal"].astype(str) if "match_status_principal" in refinado.columns else None
    if estado is not None:
        if filt == "Só alta confiança (match_cnpj_exato)":
            view_df = refinado.loc[estado.eq(MATCH_CNPJ_EXATO)].copy()
        elif filt == "Divergência — várias escolas / mesmo número":
            view_df = refinado.loc[estado.eq(MATCH_MULTIPLAS_ESCOLAS)].copy()
        elif filt == "Sem correspondência censo mesmo com CNPJ DMS válido":
            view_df = refinado.loc[estado.eq(SEM_CORRESP_CNPJ)].copy()
        elif filt.startswith("Sem CNPJ"):
            view_df = refinado.loc[estado.isin({SEM_CNPJ_DMS, CNPJ_INVALIDO_DMS})].copy()
        elif filt == "Só passe textual complementar":
            view_df = refinado.loc[estado.eq(MATCH_TEXTO_COMPLEMENTAR)].copy()
        elif filt.startswith("Linhas mesmo sem resultado textual"):
            view_df = refinado.loc[estado.eq(SEM_CORRESP_TEXTO)].copy()

    st.dataframe(view_df.head(200), use_container_width=True, height=420)
    exercicio_dl = PipelineState.exercise()
    st.download_button(
        label="Exportar Divergências Operacionais (.xlsx)",
        data=exportar_divergencias_operacionais(refinado, exercicio=exercicio_dl),
        file_name=f"divergencias_dms_censo_{exercicio_dl}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_divergencias_tecnico",
        type="primary",
    )
