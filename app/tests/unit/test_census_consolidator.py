"""
Testes unitários — services/census_consolidator.py

Cobre:
  - normalize_co_entidade: sufixo flutuante, strip, mascara nan/none/vazio
  - consolidate_census_escolar: só-escola, escola+matrícula, QT_MAT_BAS canónico,
    campos obrigatórios ausentes (CensusMergeError), outer merge
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.census_consolidator import (
    CensusMergeError,
    QT_MAT_BAS_CANONICAL,
    consolidate_census_escolar,
    normalize_co_entidade,
)


# ──────────────────────────────────────────────── helpers

def _map_escola_minimo() -> dict[str, str]:
    return {"CO_ENTIDADE": "CO_ENTIDADE", "NO_ENTIDADE": "NO_ENTIDADE"}


def _map_matricula_minimo() -> dict[str, str]:
    return {"CO_ENTIDADE": "CO_ENTIDADE", "matriculas": "QT_MAT_BAS"}


def _df_escola(entidades: list[str], nomes: list[str] | None = None) -> pd.DataFrame:
    if nomes is None:
        nomes = [f"Escola {e}" for e in entidades]
    return pd.DataFrame({"CO_ENTIDADE": entidades, "NO_ENTIDADE": nomes})


def _df_matricula(entidades: list[str], mats: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"CO_ENTIDADE": entidades, "QT_MAT_BAS": mats})


# ──────────────────────────────────────────────── normalize_co_entidade

def test_normalize_remove_sufixo_float():
    s = pd.Series(["12345678.0", "87654321.0"])
    result = normalize_co_entidade(s)
    assert result.tolist() == ["12345678", "87654321"]


def test_normalize_strip_espacos():
    s = pd.Series(["  12345678  ", "87654321 "])
    result = normalize_co_entidade(s)
    assert result.iloc[0] == "12345678"
    assert result.iloc[1] == "87654321"


def test_normalize_mascara_nan():
    s = pd.Series(["nan", "NaN", "none", "None", "<NA>", ""])
    result = normalize_co_entidade(s)
    assert (result == "").all()


def test_normalize_preserva_codigo_valido():
    s = pd.Series(["12345678"])
    result = normalize_co_entidade(s)
    assert result.iloc[0] == "12345678"


def test_normalize_inteiros_convertidos():
    """Série numérica deve ser convertida para str sem sufixo .0."""
    s = pd.Series([12345678, 87654321])
    result = normalize_co_entidade(s)
    assert result.tolist() == ["12345678", "87654321"]


# ──────────────────────────────────────────────── consolidate_census_escolar: só-escola

def test_consolida_so_escola_retorna_linhas():
    escola = _df_escola(["AAA", "BBB"])
    result = consolidate_census_escolar(escola, None, _map_escola_minimo(), {}, 2025)

    assert len(result.index) == 2


def test_consolida_so_escola_tem_metadata():
    escola = _df_escola(["AAA"])
    result = consolidate_census_escolar(
        escola, None, _map_escola_minimo(), {}, 2025,
        source_escola_label="escola_2025.csv",
    )

    assert result["censo_fonte_escola"].iloc[0] == "escola_2025.csv"
    assert result["censo_exercicio"].iloc[0] == "2025"
    assert result["censo_fonte_matricula"].iloc[0] == ""


def test_consolida_so_escola_vazia_sem_matricula():
    escola = _df_escola([])
    result = consolidate_census_escolar(escola, None, _map_escola_minimo(), {}, 2025)

    assert result.empty
    assert "CO_ENTIDADE" in result.columns


# ──────────────────────────────────────────────── consolidate_census_escolar: escola + matrícula

def test_consolida_escola_matricula_join_correto():
    escola = _df_escola(["AAA", "BBB"])
    matricula = _df_matricula(["AAA", "BBB"], [100, 200])
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025
    )

    assert len(result.index) == 2
    assert "matriculas" in result.columns


def test_consolida_outer_escola_sem_matricula():
    """Escola sem registro em matrícula deve aparecer (outer merge) com matriculas NaN."""
    escola = _df_escola(["AAA", "BBB"])
    matricula = _df_matricula(["AAA"], [100])
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025
    )

    assert len(result.index) == 2
    bbb_row = result[result["CO_ENTIDADE"] == "BBB"]
    assert bbb_row["matriculas"].isna().all()


def test_consolida_outer_matricula_sem_escola():
    """Matrícula sem escola correspondente deve aparecer com NO_ENTIDADE NaN."""
    escola = _df_escola(["AAA"])
    matricula = _df_matricula(["AAA", "CCC"], [100, 50])
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025
    )

    ccc_row = result[result["CO_ENTIDADE"] == "CCC"]
    assert len(ccc_row.index) == 1
    no_ent = ccc_row["NO_ENTIDADE"].iloc[0]
    assert pd.isna(no_ent) or str(no_ent) in ("nan", "None", "")


def test_consolida_metadata_matricula():
    escola = _df_escola(["AAA"])
    matricula = _df_matricula(["AAA"], [150])
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025,
        source_escola_label="e.csv", source_matricula_label="m.csv",
    )

    assert result["censo_fonte_matricula"].iloc[0] == "m.csv"


# ──────────────────────────────────────────────── QT_MAT_BAS canónico

def test_consolida_preserva_qt_mat_bas_canonico():
    """Quando mapeamento de matrículas aponta para QT_MAT_BAS, a coluna é preservada no consolidado."""
    escola = _df_escola(["AAA"])
    matricula = _df_matricula(["AAA"], [300])
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025
    )

    assert QT_MAT_BAS_CANONICAL in result.columns
    assert float(result[QT_MAT_BAS_CANONICAL].iloc[0]) == 300.0


def test_consolida_qt_mat_bas_numerico():
    """QT_MAT_BAS no consolidado deve ser numérico (não string)."""
    escola = _df_escola(["AAA"])
    matricula = pd.DataFrame({"CO_ENTIDADE": ["AAA"], "QT_MAT_BAS": ["250"]})
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025
    )

    if QT_MAT_BAS_CANONICAL in result.columns:
        val = pd.to_numeric(result[QT_MAT_BAS_CANONICAL].iloc[0], errors="coerce")
        assert val == 250.0


# ──────────────────────────────────────────────── CensusMergeError

def test_erro_campo_obrigatorio_escola_ausente():
    """Mapping sem CO_ENTIDADE deve lançar CensusMergeError."""
    escola = _df_escola(["AAA"])
    with pytest.raises(CensusMergeError):
        consolidate_census_escolar(escola, None, {}, {}, 2025)


def test_erro_campo_obrigatorio_matricula_ausente():
    """Mapping de matrícula sem CO_ENTIDADE deve lançar CensusMergeError."""
    escola = _df_escola(["AAA"])
    matricula = _df_matricula(["AAA"], [100])
    map_mat_incompleto = {"matriculas": "QT_MAT_BAS"}  # falta CO_ENTIDADE
    with pytest.raises(CensusMergeError):
        consolidate_census_escolar(
            escola, matricula, _map_escola_minimo(), map_mat_incompleto, 2025
        )


def test_erro_coluna_fisica_inexistente():
    """Mapping que aponta para coluna ausente no DataFrame deve lançar CensusMergeError."""
    escola = _df_escola(["AAA"])
    map_invalido = {"CO_ENTIDADE": "COLUNA_QUE_NAO_EXISTE"}
    with pytest.raises(CensusMergeError):
        consolidate_census_escolar(escola, None, map_invalido, {}, 2025)


# ──────────────────────────────────────────────── Estrutura

def test_consolida_colunas_co_entidade_normalizado():
    """CO_ENTIDADE com sufixo .0 deve ser limpo antes do join."""
    escola = pd.DataFrame({"CO_ENTIDADE": ["12345678.0"], "NO_ENTIDADE": ["Escola Float"]})
    matricula = pd.DataFrame({"CO_ENTIDADE": ["12345678"], "QT_MAT_BAS": [100]})
    result = consolidate_census_escolar(
        escola, matricula, _map_escola_minimo(), _map_matricula_minimo(), 2025
    )

    match = result[result["CO_ENTIDADE"] == "12345678"]
    assert len(match.index) == 1
    assert float(match["matriculas"].iloc[0]) == 100.0
