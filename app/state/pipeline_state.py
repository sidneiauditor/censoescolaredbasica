"""
Gestão centralizada do session_state do pipeline fiscal.

- SessionKeys: constantes para todas as chaves do st.session_state.
  Nunca escreva a string diretamente no código; use sempre SessionKeys.<NOME>.
- PipelineState: leitura tipada + escritas com invalidação em cascata.
  Toda escrita de dados de pipeline deve passar por aqui para garantir
  consistência entre as etapas.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st


class SessionKeys:
    """Chaves únicas do st.session_state — fonte única da verdade."""

    # DataFrames de trabalho
    DMS_WORK             = "dms_work"
    CENSO_WORK           = "censo_work"
    CENSO_CONSOLIDADO_DF = "censo_consolidado_df"
    CONSOLIDADO_DF       = "consolidado_df"

    # Mapeamento de colunas
    COLUMN_MAP           = "column_map"

    # Assinaturas de cache (evitam reprocessamento sem mudança de dados)
    CENSO_CONS_SIG       = "censo_consolidado_signature"
    ETAPA3_SIG           = "etapa3_det_sig"
    DMS_FILE_HASH        = "dms_file_hash"
    ESCOLA_FILE_HASH     = "escola_file_hash"
    MATRICULA_FILE_HASH  = "matricula_file_hash"

    # Summaries de merge
    ETAPA3_SUMMARY       = "etapa3_cnpj_summary"
    ETAPA3_FUZZY_SUMMARY = "etapa3_comp_text_summary"
    ETAPA3_FUZZY_SIG     = "etapa3_fuzzy_sig"

    # Contexto de execução
    EXERCISE_DEFAULT     = "ctx_exercise_default"
    SKIP_MUNICIPALITY    = "ctx_skip_municipality"
    UF_SELECT            = "ctx_uf_select"
    MUNICIPIO_SELECT     = "ctx_municipio_select"

    # Payload da Etapa 2
    ETAPA2_SIG           = "_etapa2_payload_sig"


class PipelineState:
    """Acesso tipado e escrita com invalidação em cascata do pipeline."""

    # ------------------------------------------------------------------ leitura

    @staticmethod
    def consolidado() -> pd.DataFrame | None:
        v = st.session_state.get(SessionKeys.CONSOLIDADO_DF)
        return v if isinstance(v, pd.DataFrame) else None

    @staticmethod
    def dms_work() -> pd.DataFrame | None:
        v = st.session_state.get(SessionKeys.DMS_WORK)
        return v if isinstance(v, pd.DataFrame) else None

    @staticmethod
    def censo_work() -> pd.DataFrame | None:
        v = st.session_state.get(SessionKeys.CENSO_WORK)
        return v if isinstance(v, pd.DataFrame) else None

    @staticmethod
    def censo_consolidado() -> pd.DataFrame | None:
        v = st.session_state.get(SessionKeys.CENSO_CONSOLIDADO_DF)
        return v if isinstance(v, pd.DataFrame) else None

    @staticmethod
    def column_map() -> dict[str, Any]:
        return dict(st.session_state.get(SessionKeys.COLUMN_MAP) or {})

    @staticmethod
    def exercise() -> int:
        return int(st.session_state.get(SessionKeys.EXERCISE_DEFAULT) or 2025)

    # ------------------------------------------------------------------ escrita com cascata

    @staticmethod
    def set_upload_changed(which: str) -> None:
        """
        Invalida tudo downstream quando um upload muda.

        which: "dms" | "escola" | "matricula"
        """
        if which in ("escola", "matricula"):
            st.session_state.pop(SessionKeys.CENSO_CONSOLIDADO_DF, None)
            st.session_state.pop(SessionKeys.CENSO_CONS_SIG, None)

        if which in ("dms", "escola"):
            st.session_state.pop(SessionKeys.DMS_WORK, None)
            st.session_state.pop(SessionKeys.CENSO_WORK, None)
            st.session_state.pop(SessionKeys.COLUMN_MAP, None)
            st.session_state.pop(SessionKeys.ETAPA2_SIG, None)

        # Qualquer mudança de upload invalida o merge final
        st.session_state.pop(SessionKeys.CONSOLIDADO_DF, None)
        st.session_state.pop(SessionKeys.ETAPA3_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_SIG, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SIG, None)

    @staticmethod
    def set_censo_consolidado(df: pd.DataFrame, sig: tuple) -> None:
        """Persiste o consolidado Escola⊕Matrícula e invalida o merge downstream."""
        st.session_state[SessionKeys.CENSO_CONSOLIDADO_DF] = df
        st.session_state[SessionKeys.CENSO_CONS_SIG] = sig
        # Invalida merge — novos dados de censo exigem novo cruzamento
        st.session_state.pop(SessionKeys.CONSOLIDADO_DF, None)
        st.session_state.pop(SessionKeys.ETAPA3_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_SIG, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SIG, None)

    @staticmethod
    def set_work_frames(
        dms_work: pd.DataFrame,
        censo_work: pd.DataFrame,
        column_map: dict[str, Any],
    ) -> None:
        """Persiste os DataFrames normalizados de trabalho e o mapeamento de colunas."""
        st.session_state[SessionKeys.DMS_WORK] = dms_work
        st.session_state[SessionKeys.CENSO_WORK] = censo_work
        st.session_state[SessionKeys.COLUMN_MAP] = column_map
        # Invalida merge anterior — bases podem ter mudado
        st.session_state.pop(SessionKeys.CONSOLIDADO_DF, None)
        st.session_state.pop(SessionKeys.ETAPA3_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_SIG, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SIG, None)

    @staticmethod
    def set_merge_result(
        consolidado: pd.DataFrame,
        summary: Any,
        sig: tuple,
    ) -> None:
        """Persiste o resultado do merge determinístico por CNPJ."""
        st.session_state[SessionKeys.CONSOLIDADO_DF] = consolidado
        st.session_state[SessionKeys.ETAPA3_SUMMARY] = summary
        st.session_state[SessionKeys.ETAPA3_SIG] = sig
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SUMMARY, None)
        st.session_state.pop(SessionKeys.ETAPA3_FUZZY_SIG, None)

    @staticmethod
    def set_file_hash(which: str, file_hash: str) -> None:
        key_map = {
            "dms": SessionKeys.DMS_FILE_HASH,
            "escola": SessionKeys.ESCOLA_FILE_HASH,
            "matricula": SessionKeys.MATRICULA_FILE_HASH,
        }
        k = key_map.get(which)
        if k:
            st.session_state[k] = file_hash

    @staticmethod
    def get_file_hash(which: str) -> str | None:
        key_map = {
            "dms": SessionKeys.DMS_FILE_HASH,
            "escola": SessionKeys.ESCOLA_FILE_HASH,
            "matricula": SessionKeys.MATRICULA_FILE_HASH,
        }
        k = key_map.get(which)
        return st.session_state.get(k) if k else None
