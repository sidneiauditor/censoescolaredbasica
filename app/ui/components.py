"""
Componentes UI reutilizáveis — sem lógica de negócio.

Blocos de apresentação usados por app.py e futuros controllers.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from utils.cnpj import summarize_cnpj_column


SELECT_SENTINEL = "-- Selecionar coluna --"


def render_cnpj_stats_block(label: str, df: pd.DataFrame, col_name: str) -> None:
    """Mostra contagem vazio / inválido / válido (com dígito verificador) para uma coluna CNPJ."""
    import logging
    LOG = logging.getLogger(__name__)

    st.markdown(label)
    if col_name == SELECT_SENTINEL or col_name not in df.columns:
        st.info("Selecione primeiro a coluna de CNPJ.")
        return
    stats = summarize_cnpj_column(df[col_name])
    c_empty, c_inv, c_ok = st.columns(3)
    c_empty.metric("Vazio", f"{stats.empty:,}")
    c_inv.metric("Inválido formato/DV", f"{stats.invalid_format_or_checksum:,}")
    c_ok.metric("Válidos (DV ok)", f"{stats.valid_checksum:,}")
    pct = round(100.0 * stats.valid_checksum / stats.total, 1) if stats.total else 0.0
    st.caption(
        f"Total de registos avaliados: **{stats.total:,}**. "
        f"Percentagem com CNPJ aceite: **{pct} %**."
    )
    LOG.info(
        "CNPJ %s [%s]: vazio=%s inválido=%s válido=%s",
        label, col_name, stats.empty, stats.invalid_format_or_checksum, stats.valid_checksum,
    )


def render_data_quality_banner(df: pd.DataFrame, dataset_label: str) -> None:
    """Métricas de qualidade básicas após o upload de uma base."""
    if df is None or df.empty:
        return
    n_rows = len(df)
    n_cols = len(df.columns)
    pct_null = df.isnull().mean().mean() * 100

    col1, col2, col3 = st.columns(3)
    col1.metric(f"{dataset_label} — linhas", f"{n_rows:,}")
    col2.metric("Colunas", n_cols)
    col3.metric("% campos nulos", f"{pct_null:.1f}%")

    if pct_null > 30:
        st.warning(
            f"Alta proporção de campos nulos em **{dataset_label}** ({pct_null:.0f}%) — verifique o arquivo."
        )


def preview_dataframe(title: str, df: pd.DataFrame, caption: str) -> None:
    """Pré-visualização padronizada de DataFrame com título e legenda."""
    st.markdown(f"**{title}** — {caption}")
    st.dataframe(df, use_container_width=True, height=320)


def render_pipeline_step_progress(steps: list[str], current: int) -> None:
    """Barra de progresso com nome do passo atual visível."""
    total = len(steps)
    if total == 0:
        return
    pct = current / total
    label = steps[current - 1] if 0 < current <= total else ""
    st.progress(pct, text=f"⏳ {label}" if label else None)


def render_merge_summary_metrics(summary: object) -> None:
    """KPIs de resumo do merge determinístico (CNPJDeterministicSummary)."""
    if summary is None:
        return
    cols = st.columns(5)
    cols[0].metric("Match CNPJ exato", getattr(summary, "match_cnpj_exato", "—"))
    cols[1].metric("Múltiplas escolas", getattr(summary, "multiplas_escolas_mesmo_cnpj", "—"))
    cols[2].metric("Sem correspondência", getattr(summary, "sem_correspondencia_cnpj", "—"))
    cols[3].metric("Sem CNPJ DMS", getattr(summary, "sem_cnpj_dms", "—"))
    cols[4].metric("CNPJ inválido DMS", getattr(summary, "cnpj_dms_invalido", "—"))
