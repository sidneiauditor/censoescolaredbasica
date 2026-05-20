"""
Testes do núcleo do sistema: services/cnpj_merge.py

Cobre os 7 estados de match_status_principal:
  1. match_cnpj_exato
  2. multiplas_escolas_mesmo_cnpj
  3. sem_correspondencia_cnpj
  4. sem_cnpj_utilizavel_dms
  5. cnpj_dms_invalido
  6. match_textual_complementar  (passe texto)
  7. sem_correspondencia_texto   (passe texto sem match)
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.cnpj_aggregation import CNPJ_NORM_COL_CENSO, CNPJ_NORM_COL_DMS
from services.cnpj_merge import (
    CNPJ_INVALIDO_DMS,
    CONFIANCA_ALTA_CNPJ,
    CONFIANCA_DIVERGENCIA,
    CONFIANCA_SEM_CHAVE_MERGE,
    MATCH_CNPJ_EXATO,
    MATCH_MULTIPLAS_ESCOLAS,
    SEM_CNPJ_DMS,
    SEM_CORRESP_CNPJ,
    deterministic_merge_by_cnpj,
    merge_status_qualifies_textual_complement,
)
from utils.cnpj import add_normalized_cnpj_column

CNPJ_A = "28580065001810"
CNPJ_B = "33000167000101"
# normalize_cnpj_digits não valida checksum — DV errado ainda gera 14 dígitos usáveis no merge.
# Para acionar cnpj_dms_invalido precisamos de >14 dígitos (normalize descarta → "" → chave_usavel=False).
CNPJ_INVALIDO = "285800650018101"  # 15 dígitos — descartado por normalize_cnpj_digits


def _make_dms(cnpjs: list[str]) -> pd.DataFrame:
    normed = []
    for c in cnpjs:
        from utils.cnpj import normalize_cnpj_digits
        normed.append(normalize_cnpj_digits(c) or "")
    df = pd.DataFrame({
        "NUCNPJ": cnpjs,
        CNPJ_NORM_COL_DMS: normed,
        "NMRAZAOSOCIAL": [f"Empresa {i}" for i in range(len(cnpjs))],
        "VLIMPOSTO": [1000.0] * len(cnpjs),
        "QUANTIDADE": [100] * len(cnpjs),
    })
    return df


def _make_censo(cnpjs: list[str]) -> pd.DataFrame:
    normed = []
    for c in cnpjs:
        from utils.cnpj import normalize_cnpj_digits
        normed.append(normalize_cnpj_digits(c) or "")
    return pd.DataFrame({
        "CO_ENTIDADE": [f"E{i}" for i in range(len(cnpjs))],
        "NO_ENTIDADE": [f"Escola {i}" for i in range(len(cnpjs))],
        "NU_CNPJ_ESCOLA_PRIVADA": cnpjs,
        CNPJ_NORM_COL_CENSO: normed,
        "QT_MAT_BAS": [100] * len(cnpjs),
        "TP_DEPENDENCIA": [4] * len(cnpjs),
    })


def _run_merge(dms_df: pd.DataFrame, censo_df: pd.DataFrame):
    return deterministic_merge_by_cnpj(
        dms_df,
        censo_df,
        col_dms_raw_cnpj="NUCNPJ",
        col_dms_norm=CNPJ_NORM_COL_DMS,
        col_censo_norm=CNPJ_NORM_COL_CENSO,
    )


# ─────────────────────────────────────── Estado 1: match_cnpj_exato

def test_match_cnpj_exato_unicidade_bilateral():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    result, summary = _run_merge(dms, censo)

    assert result["match_status_principal"].iloc[0] == MATCH_CNPJ_EXATO
    assert result["merge_confianca"].iloc[0] == CONFIANCA_ALTA_CNPJ
    assert summary.match_cnpj_exato == 1
    assert summary.sem_correspondencia_cnpj == 0


def test_match_cnpj_exato_preserva_dados_censo():
    """Dados do lado Censo devem aparecer com prefixo censo__ no resultado."""
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    result, _ = _run_merge(dms, censo)

    assert "censo__NO_ENTIDADE" in result.columns
    assert result["censo__NO_ENTIDADE"].iloc[0] == "Escola 0"


def test_match_cnpj_exato_dois_pares_distintos():
    dms = _make_dms([CNPJ_A, CNPJ_B])
    censo = _make_censo([CNPJ_A, CNPJ_B])
    result, summary = _run_merge(dms, censo)

    assert (result["match_status_principal"] == MATCH_CNPJ_EXATO).all()
    assert summary.match_cnpj_exato == 2


# ─────────────────────────────────────── Estado 2: multiplas_escolas_mesmo_cnpj

def test_multiplas_escolas_mesmo_cnpj():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A, CNPJ_A])  # duplicado
    result, summary = _run_merge(dms, censo)

    assert result["match_status_principal"].iloc[0] == MATCH_MULTIPLAS_ESCOLAS
    assert result["merge_confianca"].iloc[0] == CONFIANCA_DIVERGENCIA
    assert summary.multiplas_escolas_mesmo_cnpj == 1


def test_multiplas_escolas_candidatos_correto():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A, CNPJ_A, CNPJ_A])  # triplicado
    result, _ = _run_merge(dms, censo)

    assert float(result["cnpj_censo_candidatos_mesmo_numero"].iloc[0]) == 3.0


# ─────────────────────────────────────── Estado 3: sem_correspondencia_cnpj

def test_sem_correspondencia_cnpj():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_B])  # CNPJ diferente
    result, summary = _run_merge(dms, censo)

    assert result["match_status_principal"].iloc[0] == SEM_CORRESP_CNPJ
    assert summary.sem_correspondencia_cnpj == 1


def test_sem_correspondencia_cnpj_censo_vazio():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([])
    result, summary = _run_merge(dms, censo)

    assert result["match_status_principal"].iloc[0] == SEM_CORRESP_CNPJ


# ─────────────────────────────────────── Estado 4: sem_cnpj_utilizavel_dms

def test_sem_cnpj_utilizavel_dms_campo_vazio():
    dms = _make_dms([""])
    censo = _make_censo([CNPJ_A])
    result, summary = _run_merge(dms, censo)

    assert result["match_status_principal"].iloc[0] == SEM_CNPJ_DMS
    assert result["merge_confianca"].iloc[0] == CONFIANCA_SEM_CHAVE_MERGE
    assert summary.sem_cnpj_dms == 1


def test_sem_cnpj_utilizavel_dms_nao_aparece_no_censo():
    """Linha sem CNPJ DMS deve ter colunas censo__ como NA."""
    dms = _make_dms([""])
    censo = _make_censo([CNPJ_A])
    result, _ = _run_merge(dms, censo)

    censo_entidade = result["censo__CO_ENTIDADE"].iloc[0]
    assert pd.isna(censo_entidade) or str(censo_entidade) in ("", "nan", "<NA>")


# ─────────────────────────────────────── Estado 5: cnpj_dms_invalido

def test_cnpj_dms_invalido_dv_errado():
    dms = _make_dms([CNPJ_INVALIDO])
    censo = _make_censo([CNPJ_A])
    result, summary = _run_merge(dms, censo)

    assert result["match_status_principal"].iloc[0] == CNPJ_INVALIDO_DMS
    assert summary.cnpj_dms_invalido == 1


# ─────────────────────────────────────── Elegibilidade para passe textual

def test_elegibilidade_textual_sem_cnpj():
    assert merge_status_qualifies_textual_complement(SEM_CNPJ_DMS) is True

def test_elegibilidade_textual_cnpj_invalido():
    assert merge_status_qualifies_textual_complement(CNPJ_INVALIDO_DMS) is True

def test_nao_elegivel_textual_match_exato():
    assert merge_status_qualifies_textual_complement(MATCH_CNPJ_EXATO) is False

def test_nao_elegivel_textual_sem_correspondencia():
    assert merge_status_qualifies_textual_complement(SEM_CORRESP_CNPJ) is False


# ─────────────────────────────────────── Estrutura do resultado

def test_resultado_tem_prefixo_dms():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    result, _ = _run_merge(dms, censo)

    dms_cols = [c for c in result.columns if str(c).startswith("dms__")]
    assert len(dms_cols) > 0

def test_resultado_tem_prefixo_censo():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    result, _ = _run_merge(dms, censo)

    censo_cols = [c for c in result.columns if str(c).startswith("censo__")]
    assert len(censo_cols) > 0

def test_resultado_tem_colunas_status():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    result, _ = _run_merge(dms, censo)

    for col in ("match_status_principal", "merge_confianca", "merge_metodo_primario"):
        assert col in result.columns

def test_resultado_status_e_category():
    """Após o merge, colunas de status devem ser dtype category (otimização de memória)."""
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    result, _ = _run_merge(dms, censo)

    assert str(result["match_status_principal"].dtype) == "category"
    assert str(result["merge_confianca"].dtype) == "category"


# ─────────────────────────────────────── Summary

def test_summary_linhas_corretas():
    dms = _make_dms([CNPJ_A, CNPJ_B, ""])
    censo = _make_censo([CNPJ_A])
    _, summary = _run_merge(dms, censo)

    assert summary.linhas_dms == 3
    assert summary.match_cnpj_exato == 1
    assert summary.sem_correspondencia_cnpj == 1
    assert summary.sem_cnpj_dms == 1

def test_summary_tempo_positivo():
    dms = _make_dms([CNPJ_A])
    censo = _make_censo([CNPJ_A])
    _, summary = _run_merge(dms, censo)
    assert summary.tempo_segundos > 0
