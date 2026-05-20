"""Modo de uso: Operacional (CIF Salvador) · Ensino Superior · Técnico."""

from __future__ import annotations

import streamlit as st

SESSION_MODO = "cif_modo_selecionado"
AUTO_PROCESS_KEY = "cif_pref_auto_process_when_three_files"

MODO_OPERACIONAL = "operacional"
MODO_SUPERIOR    = "superior"
MODO_TECNICO     = "tecnico"

_LABEL_OPERACIONAL = "Operacional — Ed. Básica (Salvador)"
_LABEL_SUPERIOR    = "Operacional — Ed. Superior (Salvador)"
_LABEL_TECNICO     = "Técnico / diagnóstico completo"

_LABELS = [_LABEL_OPERACIONAL, _LABEL_SUPERIOR, _LABEL_TECNICO]
_LABEL_TO_MODO = {
    _LABEL_OPERACIONAL: MODO_OPERACIONAL,
    _LABEL_SUPERIOR:    MODO_SUPERIOR,
    _LABEL_TECNICO:     MODO_TECNICO,
}
_MODO_TO_LABEL = {v: k for k, v in _LABEL_TO_MODO.items()}


def _modo_atual() -> str:
    return st.session_state.get(SESSION_MODO, MODO_OPERACIONAL)


def modo_sidebar_radio() -> str:
    """Seleção na sidebar; devolve 'operacional' | 'superior' | 'tecnico'."""
    label_atual = _MODO_TO_LABEL.get(_modo_atual(), _LABEL_OPERACIONAL)
    idx = _LABELS.index(label_atual) if label_atual in _LABELS else 0

    choice = st.sidebar.radio(
        "Modo",
        options=_LABELS,
        index=idx,
        key="cif_modo_sidebar_widget",
        help=(
            "**Ed. Básica** — Salvador/BA, pipeline DMS × Censo Escolar.\n\n"
            "**Ed. Superior** — Salvador/BA, pipeline DMS × Censo Superior (merge por nome).\n\n"
            "**Técnico** — fluxo completo com mapeamento manual e diagnósticos."
        ),
    )
    modo = _LABEL_TO_MODO[choice]
    st.session_state[SESSION_MODO] = modo
    return modo


def salvador_sidebar_contexto_ui(exercise_year: int) -> None:
    st.sidebar.caption(
        f"Território: **Salvador (BA)** · IBGE **2927408** · ano **{exercise_year}**."
    )


def marcar_preset_salvador_sessao() -> None:
    st.session_state["cif_contexto_preset"] = "salvador_ba_2927408"


# ── compatibilidade retroativa (usado em operacional.py)
def is_modo_operacional() -> bool:
    return _modo_atual() == MODO_OPERACIONAL
