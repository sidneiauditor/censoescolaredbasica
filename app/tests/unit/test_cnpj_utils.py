"""Testes unitários — utils/cnpj.py: normalização, checksum, classify, summarize."""

from __future__ import annotations

import pandas as pd
import pytest

from utils.cnpj import (
    add_normalized_cnpj_column,
    classify_cnpj_cell,
    cnpj_checksum_ok,
    normalize_cnpj_digits,
    only_digits,
    summarize_cnpj_column,
)

# ──────────────────────────────────────────────── only_digits

def test_only_digits_com_mascara():
    assert only_digits("28.580.065/0018-10") == "28580065001810"

def test_only_digits_vazio():
    assert only_digits("") == ""

def test_only_digits_nan():
    assert only_digits(float("nan")) == ""

def test_only_digits_none():
    assert only_digits(None) == ""

# ──────────────────────────────────────────────── normalize_cnpj_digits

def test_normalize_retorna_14_digitos_com_mascara():
    assert normalize_cnpj_digits("28.580.065/0018-10") == "28580065001810"

def test_normalize_zfill_quando_menos_de_14():
    assert normalize_cnpj_digits("1234567000100") == "01234567000100"

def test_normalize_retorna_none_quando_vazio():
    assert normalize_cnpj_digits("") is None

def test_normalize_retorna_none_quando_mais_de_14():
    assert normalize_cnpj_digits("123456789012345") is None

def test_normalize_retorna_none_para_nan():
    assert normalize_cnpj_digits(float("nan")) is None

# ──────────────────────────────────────────────── cnpj_checksum_ok

def test_checksum_valido():
    assert cnpj_checksum_ok("28580065001810") is True

def test_checksum_invalido_dv():
    assert cnpj_checksum_ok("28580065001811") is False

def test_checksum_rejeita_todos_zeros():
    assert cnpj_checksum_ok("00000000000000") is False

def test_checksum_rejeita_tamanho_errado():
    assert cnpj_checksum_ok("2858006500181") is False

def test_checksum_outro_cnpj_valido():
    # CNPJ verificado manualmente — dígitos verificadores conferem
    assert cnpj_checksum_ok("33000167000101") is True

# ──────────────────────────────────────────────── classify_cnpj_cell

def test_classify_vazio():
    assert classify_cnpj_cell("") == "empty"

def test_classify_invalido_dv():
    assert classify_cnpj_cell("28580065001811") == "invalid"

def test_classify_valido():
    assert classify_cnpj_cell("28.580.065/0018-10") == "valid"

def test_classify_nan():
    assert classify_cnpj_cell(float("nan")) == "empty"

def test_classify_texto_sem_digitos():
    # "N/A" não tem dígitos → trata como vazio; usar all-zeros para cobrir "invalid"
    assert classify_cnpj_cell("N/A") == "empty"

def test_classify_zeros_invalido():
    # 14 zeros passa o tamanho mas é rejeitado pelo guard all-zeros no checksum
    assert classify_cnpj_cell("00000000000000") == "invalid"

# ──────────────────────────────────────────────── summarize_cnpj_column

def test_summarize_counts_corretos():
    s = pd.Series(["28.580.065/0018-10", "", "28580065001811", "33.000.167/0001-01"])
    r = summarize_cnpj_column(s)
    assert r.valid_checksum == 2
    assert r.empty == 1
    assert r.invalid_format_or_checksum == 1
    assert r.total == 4

# ──────────────────────────────────────────────── add_normalized_cnpj_column

def test_add_normalized_nao_modifica_original():
    df = pd.DataFrame({"CNPJ": ["28.580.065/0018-10", ""]})
    original_cols = list(df.columns)
    result = add_normalized_cnpj_column(df, "CNPJ", "__cnpj_norm")
    assert list(df.columns) == original_cols  # original intacto
    assert "__cnpj_norm" in result.columns

def test_add_normalized_valores_corretos():
    df = pd.DataFrame({"CNPJ": ["28.580.065/0018-10", "", "28580065001811"]})
    result = add_normalized_cnpj_column(df, "CNPJ", "__cnpj_norm")
    assert result["__cnpj_norm"].iloc[0] == "28580065001810"
    assert result["__cnpj_norm"].iloc[1] == ""
    assert result["__cnpj_norm"].iloc[2] == "28580065001811"

def test_add_normalized_usa_assign_sem_copy():
    """df.assign() deve retornar novo objeto; df original não deve ter a coluna nova."""
    df = pd.DataFrame({"C": ["28.580.065/0018-10"]})
    result = add_normalized_cnpj_column(df, "C", "_norm")
    assert "_norm" not in df.columns
    assert "_norm" in result.columns
