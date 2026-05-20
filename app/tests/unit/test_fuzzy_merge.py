"""
Testes unitários — services/text_fuzzy_merge.py

Cobre:
  - run_textual_fuzzy_merge: match acima do cutoff, ausência de match abaixo,
    DMS com texto vazio, múltiplos candidatos, progress callback,
    estrutura do consolidado (prefixos dms__/censo__), contadores do summary
  - _blocking_key: strings vazias e normais
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.text_fuzzy_merge import (
    TextMatchSummary,
    _blocking_key,
    run_textual_fuzzy_merge,
)


# ──────────────────────────────────────────────── helpers

def _dms(razoes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "NUCNPJ": [""] * len(razoes),
        "NMRAZAOSOCIAL": razoes,
    })


def _censo(nomes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "CO_ENTIDADE": [f"E{i}" for i in range(len(nomes))],
        "NO_ENTIDADE": nomes,
    })


def _run(
    dms_razoes: list[str],
    censo_nomes: list[str],
    cutoff: float = 70.0,
) -> tuple[pd.DataFrame, TextMatchSummary]:
    dms = _dms(dms_razoes)
    censo = _censo(censo_nomes)
    return run_textual_fuzzy_merge(
        dms, censo,
        col_dms_razao="NMRAZAOSOCIAL",
        col_censo_nome="NO_ENTIDADE",
        score_cutoff=cutoff,
    )


# ──────────────────────────────────────────────── _blocking_key

def test_blocking_key_string_normal():
    key = _blocking_key("ESCOLA MUNICIPAL ATIVA")
    assert len(key) == 4
    assert key == key.upper()


def test_blocking_key_string_vazia():
    key = _blocking_key("")
    assert key == "__EMPTY__"


def test_blocking_key_so_espacos():
    key = _blocking_key("   ")
    assert key == "__EMPTY__"


# ──────────────────────────────────────────────── match encontrado

def test_match_textual_encontrado():
    """Nome quase idêntico deve produzir match_status == 'match_textual'."""
    result, summary = _run(
        ["COLEGIO ESTADUAL BAHIA"],
        ["COLEGIO ESTADUAL BAHIA"],
        cutoff=80.0,
    )

    assert result["match_status"].iloc[0] == "match_textual"
    assert summary.encontrados == 1
    assert summary.sem_correspondencia == 0


def test_match_textual_score_preenchido():
    result, _ = _run(
        ["ESCOLA MUNICIPAL ATIVA"],
        ["ESCOLA MUNICIPAL ATIVA"],
        cutoff=80.0,
    )
    score = result["similaridade_score"].iloc[0]
    assert score is not None
    assert float(score) >= 80.0


def test_match_textual_preserva_dado_censo():
    result, _ = _run(
        ["ESCOLA MUNICIPAL ATIVA"],
        ["ESCOLA MUNICIPAL ATIVA"],
        cutoff=70.0,
    )
    assert "censo__CO_ENTIDADE" in result.columns
    assert result["censo__CO_ENTIDADE"].iloc[0] == "E0"


# ──────────────────────────────────────────────── sem match

def test_sem_match_abaixo_cutoff():
    """Texto completamente diferente não deve gerar match com cutoff alto."""
    result, summary = _run(
        ["EMPRESA XYZ INEXISTENTE"],
        ["ESCOLA MUNICIPAL ATIVA"],
        cutoff=95.0,
    )

    assert result["match_status"].iloc[0] == "sem_correspondencia"
    assert summary.encontrados == 0
    assert summary.sem_correspondencia == 1


def test_sem_match_score_nulo():
    result, _ = _run(
        ["EMPRESA TOTALMENTE DIFERENTE XYZ"],
        ["ESCOLA MUNICIPAL ATIVA"],
        cutoff=95.0,
    )
    assert result["similaridade_score"].iloc[0] is None


def test_sem_match_censo_cols_sao_na():
    result, _ = _run(
        ["EMPRESA TOTALMENTE DIFERENTE XYZ"],
        ["ESCOLA MUNICIPAL ATIVA"],
        cutoff=95.0,
    )
    co = result["censo__CO_ENTIDADE"].iloc[0]
    assert co is None or (isinstance(co, float) and pd.isna(co)) or pd.isna(co)


# ──────────────────────────────────────────────── texto vazio na DMS

def test_texto_vazio_dms_sem_match():
    result, summary = _run([""], ["ESCOLA ATIVA"], cutoff=70.0)

    assert result["match_status"].iloc[0] == "sem_correspondencia"
    assert summary.sem_correspondencia == 1


# ──────────────────────────────────────────────── múltiplas linhas

def test_multiplas_linhas_counts():
    result, summary = _run(
        ["ESCOLA BAHIA", "COLEGIO MUNICIPAL"],
        ["ESCOLA BAHIA", "COLEGIO MUNICIPAL"],
        cutoff=80.0,
    )

    assert summary.linhas_dms == 2
    assert summary.encontrados == 2
    assert summary.sem_correspondencia == 0


def test_multiplas_linhas_misto():
    result, summary = _run(
        ["ESCOLA BAHIA", "EMPRESA XYZ INEXISTENTE"],
        ["ESCOLA BAHIA", "ESCOLA MUNICIPAL"],
        cutoff=90.0,
    )

    assert summary.linhas_dms == 2
    assert summary.encontrados >= 1  # pelo menos ESCOLA BAHIA casa


# ──────────────────────────────────────────────── progress callback

def test_progress_callback_chamado():
    progresses: list[float] = []

    def _cb(p: float) -> None:
        progresses.append(p)

    dms = _dms(["ESCOLA A", "ESCOLA B"])
    censo = _censo(["ESCOLA A", "ESCOLA B"])
    run_textual_fuzzy_merge(
        dms, censo,
        col_dms_razao="NMRAZAOSOCIAL",
        col_censo_nome="NO_ENTIDADE",
        score_cutoff=70.0,
        progress_callback=_cb,
    )

    assert len(progresses) == 2
    assert abs(progresses[-1] - 1.0) < 1e-6


# ──────────────────────────────────────────────── estrutura do consolidado

def test_consolidado_tem_prefixo_dms():
    result, _ = _run(["ESCOLA"], ["ESCOLA"])
    dms_cols = [c for c in result.columns if str(c).startswith("dms__")]
    assert len(dms_cols) > 0


def test_consolidado_tem_prefixo_censo():
    result, _ = _run(["ESCOLA"], ["ESCOLA"])
    censo_cols = [c for c in result.columns if str(c).startswith("censo__")]
    assert len(censo_cols) > 0


def test_consolidado_tem_colunas_de_diagnostico():
    result, _ = _run(["ESCOLA"], ["ESCOLA"])
    for col in ("dms_texto_normalizado", "censo_texto_normalizado_match", "similaridade_score", "match_status"):
        assert col in result.columns


def test_summary_tempo_positivo():
    _, summary = _run(["ESCOLA"], ["ESCOLA"])
    assert summary.tempo_segundos > 0


def test_summary_linhas_censo():
    _, summary = _run(["ESCOLA A", "ESCOLA B"], ["ESCOLA A"])
    assert summary.linhas_censo == 1
    assert summary.linhas_dms == 2


# ──────────────────────────────────────────────── censo vazio

def test_censo_vazio_todos_sem_correspondencia():
    result, summary = _run(["ESCOLA ATIVA"], [], cutoff=70.0)

    assert summary.sem_correspondencia == 1
    assert summary.encontrados == 0


def test_dms_vazio_retorna_vazio():
    result, summary = _run([], ["ESCOLA A"], cutoff=70.0)

    assert len(result.index) == 0
    assert summary.linhas_dms == 0
    assert summary.encontrados == 0
