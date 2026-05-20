"""
Testes unitários — services/indicators.py

Cobre:
  - add_basic_fiscal_indicators: colunas presentes, cálculo correto, denominador zero,
    colunas ausentes (aviso no report), df original não modificado
  - summarize_series_stats: média, mediana, n_validos, serie vazia
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from services.indicators import (
    COL_BASE_PM,
    COL_ISS_PM,
    COL_MSG_PM,
    FiscalBasicIndicatorReport,
    add_basic_fiscal_indicators,
    summarize_series_stats,
)


# ──────────────────────────────────────────────── helpers

def _make_consolidated(
    *,
    iss: float | None = 1500.0,
    mensalidade: float | None = 500.0,
    base_calc: float | None = 15000.0,
    matriculas: float | None = 150.0,
) -> pd.DataFrame:
    """DataFrame com prefixos dms__ e censo__ como sai da Etapa 3."""
    row: dict[str, object] = {}
    if iss is not None:
        row["dms__VLIMPOSTO"] = iss
    if mensalidade is not None:
        row["dms__VLMENSALIDADE"] = mensalidade
    if base_calc is not None:
        row["dms__VLBASECALCULO"] = base_calc
    if matriculas is not None:
        row["censo__QT_MAT_BAS"] = matriculas
    return pd.DataFrame([row])


def _default_column_map() -> dict[str, str]:
    return {"censo_mat": "QT_MAT_BAS"}


# ──────────────────────────────────────────────── add_basic_fiscal_indicators: colunas criadas

def test_indicadores_cria_col_iss_por_matricula():
    df = _make_consolidated()
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert COL_ISS_PM in out.columns


def test_indicadores_cria_col_mensalidade_por_aluno():
    df = _make_consolidated()
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert COL_MSG_PM in out.columns


def test_indicadores_cria_col_base_calculo_por_aluno():
    df = _make_consolidated()
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert COL_BASE_PM in out.columns


# ──────────────────────────────────────────────── cálculos corretos

def test_iss_por_matricula_valor_correto():
    df = _make_consolidated(iss=1500.0, matriculas=150.0)
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert math.isclose(float(out[COL_ISS_PM].iloc[0]), 10.0, rel_tol=1e-9)


def test_mensalidade_por_aluno_valor_correto():
    df = _make_consolidated(mensalidade=500.0, matriculas=100.0)
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert math.isclose(float(out[COL_MSG_PM].iloc[0]), 5.0, rel_tol=1e-9)


def test_base_calculo_por_aluno_valor_correto():
    df = _make_consolidated(base_calc=6000.0, matriculas=200.0)
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert math.isclose(float(out[COL_BASE_PM].iloc[0]), 30.0, rel_tol=1e-9)


# ──────────────────────────────────────────────── denominador zero / ausente

def test_matriculas_zero_vira_nan():
    """Denominador zero não deve gerar inf — deve ser NaN."""
    df = _make_consolidated(iss=1500.0, matriculas=0.0)
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert pd.isna(out[COL_ISS_PM].iloc[0])


def test_matriculas_negativas_vira_nan():
    df = _make_consolidated(iss=1000.0, matriculas=-10.0)
    out, _ = add_basic_fiscal_indicators(df, _default_column_map())
    assert pd.isna(out[COL_ISS_PM].iloc[0])


def test_matriculas_ausentes_todos_nan():
    """Sem coluna censo__QT_MAT_BAS os três indicadores ficam NaN."""
    df = _make_consolidated(matriculas=None)
    out, report = add_basic_fiscal_indicators(df, {})

    assert pd.isna(out[COL_ISS_PM].iloc[0])
    assert pd.isna(out[COL_MSG_PM].iloc[0])
    assert pd.isna(out[COL_BASE_PM].iloc[0])
    assert report.avisos  # deve ter pelo menos um aviso


# ──────────────────────────────────────────────── numerador ausente

def test_sem_iss_indicador_nan_com_aviso():
    df = _make_consolidated(iss=None)
    out, report = add_basic_fiscal_indicators(df, _default_column_map())

    assert pd.isna(out[COL_ISS_PM].iloc[0])
    assert any("VLIMPOSTO" in av for av in report.avisos)


def test_sem_mensalidade_indicador_nan():
    df = _make_consolidated(mensalidade=None)
    out, report = add_basic_fiscal_indicators(df, _default_column_map())

    assert pd.isna(out[COL_MSG_PM].iloc[0])


# ──────────────────────────────────────────────── imutabilidade do df original

def test_nao_modifica_df_original():
    df = _make_consolidated()
    original_cols = set(df.columns)
    _, _ = add_basic_fiscal_indicators(df, _default_column_map())

    assert set(df.columns) == original_cols


# ──────────────────────────────────────────────── report

def test_report_resolve_coluna_matriculas():
    df = _make_consolidated()
    _, report = add_basic_fiscal_indicators(df, _default_column_map())

    assert report.colunas_resolvidas.get("matriculas_denominador") is not None


def test_report_resolve_coluna_iss():
    df = _make_consolidated()
    _, report = add_basic_fiscal_indicators(df, _default_column_map())

    assert report.colunas_resolvidas.get("VLIMPOSTO_numerador") is not None


def test_report_resumos_tem_tres_indicadores():
    df = _make_consolidated()
    _, report = add_basic_fiscal_indicators(df, _default_column_map())

    assert len(report.resumos) == 3


def test_report_resumo_iss_media_correta():
    df = _make_consolidated(iss=1500.0, matriculas=150.0)
    _, report = add_basic_fiscal_indicators(df, _default_column_map())

    iss_stat = next((r for r in report.resumos if r.nome == COL_ISS_PM), None)
    assert iss_stat is not None
    assert math.isclose(float(iss_stat.media), 10.0, rel_tol=1e-9)


# ──────────────────────────────────────────────── summarize_series_stats

def test_summarize_serie_normal():
    s = pd.Series([10.0, 20.0, 30.0])
    stat = summarize_series_stats("teste", s)

    assert stat.n_validos == 3
    assert math.isclose(stat.media, 20.0, rel_tol=1e-9)
    assert math.isclose(stat.mediana, 20.0, rel_tol=1e-9)
    assert math.isclose(stat.maximo, 30.0, rel_tol=1e-9)
    assert math.isclose(stat.minimo, 10.0, rel_tol=1e-9)


def test_summarize_serie_vazia():
    s = pd.Series(dtype=float)
    stat = summarize_series_stats("vazia", s)

    assert stat.n_validos == 0
    assert stat.media is None
    assert stat.mediana is None


def test_summarize_ignora_nan_e_inf():
    s = pd.Series([np.nan, np.inf, 50.0, -np.inf, 100.0])
    stat = summarize_series_stats("com_invalidos", s)

    assert stat.n_validos == 2
    assert math.isclose(stat.media, 75.0, rel_tol=1e-9)


def test_summarize_serie_so_nan():
    s = pd.Series([np.nan, np.nan])
    stat = summarize_series_stats("so_nan", s)

    assert stat.n_validos == 0
    assert stat.media is None


# ──────────────────────────────────────────────── múltiplas linhas

def test_indicadores_multiplas_linhas():
    df = pd.DataFrame({
        "dms__VLIMPOSTO": [1000.0, 2000.0],
        "censo__QT_MAT_BAS": [100.0, 200.0],
    })
    out, report = add_basic_fiscal_indicators(df, {"censo_mat": "QT_MAT_BAS"})

    assert len(out.index) == 2
    assert math.isclose(float(out[COL_ISS_PM].iloc[0]), 10.0, rel_tol=1e-9)
    assert math.isclose(float(out[COL_ISS_PM].iloc[1]), 10.0, rel_tol=1e-9)
    iss_stat = next(r for r in report.resumos if r.nome == COL_ISS_PM)
    assert iss_stat.n_validos == 2
