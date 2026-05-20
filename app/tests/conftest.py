"""
Fixtures compartilhadas para toda a suite de testes.

Nomenclatura:
  df_escola_*  — tabelas Escola do Censo (TP_DEPENDENCIA, QT_MAT_BAS, CNPJ, etc.)
  df_dms_*     — extratos DMS-Educação (CNPJ, QUANTIDADE, VLIMPOSTO, etc.)
  df_mat_*     — tabelas Matrícula do Censo (CO_ENTIDADE, QT_MAT_BAS)
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.cnpj_aggregation import CNPJ_NORM_COL_CENSO, CNPJ_NORM_COL_DMS


# ──────────────────────────────────────────────────────────────────── helpers

def _cnpj_valido_28580065001810() -> str:
    """CNPJ com DV correto."""
    return "28580065001810"


def _cnpj_valido_33000167000101() -> str:
    return "33000167000101"


def _cnpj_invalido_dv() -> str:
    """14 dígitos mas DV errado."""
    return "28580065001811"


# ──────────────────────────────────────────────────────────────────── escola

@pytest.fixture
def df_escola_privada_simples():
    """Escola privada (TP_DEPENDENCIA=4), Salvador, com CNPJ válido e QT_MAT_BAS > 0."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["12345678"],
        "NO_ENTIDADE": ["Escola Teste Ltda"],
        "TP_DEPENDENCIA": [4],
        "NU_CNPJ_ESCOLA_PRIVADA": ["28.580.065/0018-10"],
        "QT_MAT_BAS": [150],
        "SG_UF": ["BA"],
        "CO_MUNICIPIO": ["2927408"],
        "NO_MUNICIPIO": ["Salvador"],
    })


@pytest.fixture
def df_escola_publica():
    """Escola municipal (TP_DEPENDENCIA=3) — deve ser excluída pelo filtro fiscal."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["99999999"],
        "NO_ENTIDADE": ["EMEF Pública"],
        "TP_DEPENDENCIA": [3],
        "QT_MAT_BAS": [300],
        "SG_UF": ["BA"],
        "CO_MUNICIPIO": ["2927408"],
    })


@pytest.fixture
def df_escola_superior_puro():
    """Escola privada de ensino superior (QT_MAT_BAS=0) — deve ser excluída."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["88888888"],
        "NO_ENTIDADE": ["Faculdade Pura Superior"],
        "TP_DEPENDENCIA": [4],
        "NU_CNPJ_ESCOLA_PRIVADA": ["33.000.167/0001-01"],
        "QT_MAT_BAS": [0],
        "SG_UF": ["BA"],
        "CO_MUNICIPIO": ["2927408"],
    })


@pytest.fixture
def df_escola_mista():
    """Escola privada com superior E básica (QT_MAT_BAS > 0) — deve ser mantida."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["77777777"],
        "NO_ENTIDADE": ["Colégio e Faculdade Mista"],
        "TP_DEPENDENCIA": [4],
        "NU_CNPJ_ESCOLA_PRIVADA": ["33.000.167/0001-01"],
        "QT_MAT_BAS": [80],
        "SG_UF": ["BA"],
        "CO_MUNICIPIO": ["2927408"],
    })


@pytest.fixture
def df_escola_multiplas_privadas():
    """Duas escolas privadas com CNPJs distintos para teste de merge 1:1."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["12345678", "87654321"],
        "NO_ENTIDADE": ["Escola A", "Escola B"],
        "TP_DEPENDENCIA": [4, 4],
        "NU_CNPJ_ESCOLA_PRIVADA": ["28.580.065/0018-10", "33.000.167/0001-01"],
        "QT_MAT_BAS": [150, 200],
        "SG_UF": ["BA", "BA"],
        "CO_MUNICIPIO": ["2927408", "2927408"],
    })


@pytest.fixture
def df_escola_cnpj_duplicado():
    """Duas escolas com o MESMO CNPJ — provoca multiplas_escolas_mesmo_cnpj."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["11111111", "22222222"],
        "NO_ENTIDADE": ["Escola Filial 1", "Escola Filial 2"],
        "TP_DEPENDENCIA": [4, 4],
        "NU_CNPJ_ESCOLA_PRIVADA": ["28.580.065/0018-10", "28.580.065/0018-10"],
        "QT_MAT_BAS": [100, 50],
        "SG_UF": ["BA", "BA"],
        "CO_MUNICIPIO": ["2927408", "2927408"],
    })


# ──────────────────────────────────────────────────────────────────── DMS

@pytest.fixture
def df_dms_simples():
    """DMS com um lançamento válido, CNPJ correspondente à escola privada simples."""
    return pd.DataFrame({
        "NUCNPJ": ["28.580.065/0018-10"],
        "NMRAZAOSOCIAL": ["ESCOLA TESTE LTDA"],
        "VLIMPOSTO": [1500.00],
        "VLBASECALCULO": [15000.00],
        "VLMENSALIDADE": [500.00],
        "QUANTIDADE": [150],
        "DTCOMPETENCIA": ["2025-05-01"],
        "TIPO": ["NORMAL"],
        "SITUACAO": ["ATIVA"],
    })


@pytest.fixture
def df_dms_sem_cnpj():
    """DMS com CNPJ vazio — deve resultar em sem_cnpj_utilizavel_dms."""
    return pd.DataFrame({
        "NUCNPJ": [""],
        "NMRAZAOSOCIAL": ["ESCOLA SEM CNPJ"],
        "VLIMPOSTO": [500.00],
        "QUANTIDADE": [50],
        "DTCOMPETENCIA": ["2025-05-01"],
    })


@pytest.fixture
def df_dms_cnpj_invalido():
    """DMS com CNPJ de 14 dígitos mas DV errado."""
    return pd.DataFrame({
        "NUCNPJ": [_cnpj_invalido_dv()],
        "NMRAZAOSOCIAL": ["ESCOLA CNPJ INVALIDO"],
        "VLIMPOSTO": [200.00],
        "QUANTIDADE": [20],
        "DTCOMPETENCIA": ["2025-05-01"],
    })


@pytest.fixture
def df_dms_cnpj_sem_correspondencia():
    """DMS com CNPJ válido que não existe no Censo municipal."""
    return pd.DataFrame({
        "NUCNPJ": ["11222333000181"],  # CNPJ válido mas não presente no Censo
        "NMRAZAOSOCIAL": ["ESCOLA INEXISTENTE NO CENSO"],
        "VLIMPOSTO": [300.00],
        "QUANTIDADE": [30],
        "DTCOMPETENCIA": ["2025-05-01"],
    })


@pytest.fixture
def df_dms_multiplos():
    """DMS com dois lançamentos distintos: um com match, outro sem."""
    return pd.DataFrame({
        "NUCNPJ": ["28.580.065/0018-10", "33.000.167/0001-01"],
        "NMRAZAOSOCIAL": ["ESCOLA A LTDA", "ESCOLA B LTDA"],
        "VLIMPOSTO": [1000.00, 2000.00],
        "QUANTIDADE": [100, 200],
        "DTCOMPETENCIA": ["2025-05-01", "2025-05-01"],
        "TIPO": ["NORMAL", "NORMAL"],
        "SITUACAO": ["ATIVA", "ATIVA"],
    })


# ──────────────────────────────────────────────────────────────────── Matrícula

@pytest.fixture
def df_matricula_simples():
    """Matrícula básica: uma linha por escola (CO_ENTIDADE + QT_MAT_BAS)."""
    return pd.DataFrame({
        "CO_ENTIDADE": ["12345678"],
        "QT_MAT_BAS": [150],
    })


# ──────────────────────────────────────────────────────────────────── Censo com CNPJ norm

@pytest.fixture
def df_censo_normalizado(df_escola_privada_simples):
    """Escola com __cnpj_norm_censo já preenchido (pós-normalização)."""
    df = df_escola_privada_simples.copy()
    df[CNPJ_NORM_COL_CENSO] = _cnpj_valido_28580065001810()
    return df


@pytest.fixture
def df_dms_normalizado(df_dms_simples):
    """DMS com __cnpj_norm_dms já preenchido (pós-normalização)."""
    df = df_dms_simples.copy()
    df[CNPJ_NORM_COL_DMS] = _cnpj_valido_28580065001810()
    return df
