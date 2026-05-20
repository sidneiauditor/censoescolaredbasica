"""
Utilitários partilhados pelos controllers:
  - logging, erros amigáveis, hash SHA-256, gravação async de Excel,
    carregamento de uploads e caminhos de saída com timestamp.
"""

from __future__ import annotations

import hashlib
import logging
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from config import APP_DIR
from domain.dataset_kind import DatasetKind, label as dataset_kind_label
from services.table_loader import load_dataset_bundle, spinner_message
from state.pipeline_state import PipelineState
from utils.file_io import FileValidationError

LOG = logging.getLogger(__name__)

_DATASET_HASH_KEY: dict[DatasetKind, str] = {
    DatasetKind.DMS_EDUCACAO:    "dms",
    DatasetKind.CENSO_ESCOLA:    "escola",
    DatasetKind.CENSO_MATRICULA: "matricula",
}


# ─────────────────────────────────────────────────────── logging

def configure_logging() -> None:
    """Logs para ficheiro (DEBUG) e consola (INFO). Evita handlers duplicados em reruns Streamlit."""

    root = logging.getLogger()
    if root.handlers:
        return

    log_dir = APP_DIR / "outputs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root.setLevel(logging.DEBUG)

    fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(sh)


# ─────────────────────────────────────────────────────── erros

def friendly_file_error(where: str, err: BaseException) -> None:
    st.error(f"**{where}**: não foi possível utilizar este ficheiro.")
    if isinstance(err, FileValidationError):
        st.warning(str(err))
        LOG.warning("Validação falhou [%s]: %s", where, err)
        return
    st.warning(
        "Ocorreu um erro inesperado. Sugestões:\n\n"
        "- Confirme se o formato é CSV ou Excel (.xlsx) íntegro.\n\n"
        "- Para CSV grave em UTF‑8 ou Windows‑1252.\n\n"
        "- Experimente remover palavras‑passe de folha Excel."
    )
    LOG.exception("Erro ao processar [%s]", where)
    with st.expander("Detalhe técnico (equipa técnica)"):
        st.code(str(err))


# ─────────────────────────────────────────────────────── hash SHA-256

def compute_file_hash(data: bytes) -> str:
    """SHA-256 hex do conteúdo bruto de um ficheiro."""
    return hashlib.sha256(data).hexdigest()


# ─────────────────────────────────────────────────────── output paths

def versioned_output_path(base_name: str, ext: str = ".xlsx") -> Path:
    """Caminho em outputs/ com carimbo de data: consolidado_20250520.xlsx."""
    stamp = date.today().strftime("%Y%m%d")
    return APP_DIR / "outputs" / f"{base_name}_{stamp}{ext}"


# ─────────────────────────────────────────────────────── Excel assíncrono

def write_xlsx_async(df: pd.DataFrame, path: Path) -> None:
    """Grava DataFrame para Excel em thread daemon — não bloqueia o rerun Streamlit."""

    def _write() -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_excel(path, index=False, engine="openpyxl")
            LOG.info("Excel gravado: %s (linhas=%s)", path.name, len(df.index))
        except Exception as _exc:
            LOG.warning("Falha ao gravar %s: %s", path.name, _exc)

    threading.Thread(target=_write, daemon=True).start()


# ─────────────────────────────────────────────────────── carregamento de uploads

def load_bundle(kind: DatasetKind, uploaded: Any, label_erro: str) -> dict[str, Any] | None:
    """
    Carrega upload para estrutura ``bundle`` ou devolve ``None`` com erro na UI.

    Efeito colateral: computa SHA-256 do ficheiro e persiste em PipelineState
    para auditoria e deteção de ficheiro repetido.
    """
    if uploaded is None:
        return None

    raw: bytes = uploaded.getvalue()
    fname: str = uploaded.name

    hash_key = _DATASET_HASH_KEY.get(kind)
    if hash_key:
        PipelineState.set_file_hash(hash_key, compute_file_hash(raw))

    try:
        with st.spinner(spinner_message(kind.value)):
            bundle = load_dataset_bundle(kind.value, raw, fname)
        LOG.info(
            "Carregado %s (%s): linhas=%s colunas=%s",
            dataset_kind_label(kind),
            fname,
            len(bundle["dataframe"].index),
            len(bundle["dataframe"].columns),
        )
        return bundle
    except (FileValidationError, ValueError) as exc:
        friendly_file_error(label_erro, exc)
    except Exception as exc:  # pylint: disable=broad-except
        friendly_file_error(label_erro, exc)
    return None


def coerce_frames_from_uploads(
    up_dms: Any,
    up_escola: Any,
    up_mat: Any,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, pd.DataFrame | None]:
    """Carrega os três uploads para DataFrames. Retorna None onde o upload falhou."""
    dms_bundle    = load_bundle(DatasetKind.DMS_EDUCACAO,    up_dms,    "DMS-Educação")
    escola_bundle = load_bundle(DatasetKind.CENSO_ESCOLA,    up_escola, "Censo Escola")
    mat_bundle    = load_bundle(DatasetKind.CENSO_MATRICULA, up_mat,    "Censo Matrícula")
    dms_df    = dms_bundle["dataframe"]    if dms_bundle    else None
    df_escola = escola_bundle["dataframe"] if escola_bundle else None
    df_mat    = mat_bundle["dataframe"]    if mat_bundle    else None
    return dms_df, df_escola, df_mat
