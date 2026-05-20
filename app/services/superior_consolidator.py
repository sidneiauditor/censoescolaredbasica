"""
Consolida microdados do Censo da Educação Superior (IES + Cursos).

Retorna um DataFrame com uma linha por IES, contendo:
  - identificação: CO_IES, NO_IES, NO_MANTENEDORA, CO_MANTENEDORA
  - localização: SG_UF_IES, CO_MUNICIPIO_IES, NO_MUNICIPIO_IES
  - tipologia: TP_CATEGORIA_ADMINISTRATIVA, TP_ORGANIZACAO_ACADEMICA
  - QT_MAT_BAS: total de matrículas (nome padronizado para compatibilidade
    com divergencia_export_service)

Função pública
--------------
consolidate_censo_superior(ies_df, cur_df, municipio_codigo, include_ead)
"""
from __future__ import annotations

import logging

import pandas as pd

LOG = logging.getLogger(__name__)

MUNICIPIO_SALVADOR = 2927408

# Colunas do arquivo IES que interessam
_IES_COLS = [
    "CO_IES",
    "NO_IES",
    "NO_MANTENEDORA",
    "CO_MANTENEDORA",
    "SG_UF_IES",
    "CO_MUNICIPIO_IES",
    "NO_MUNICIPIO_IES",
    "TP_CATEGORIA_ADMINISTRATIVA",
    "TP_ORGANIZACAO_ACADEMICA",
    "TP_REDE",
]

# Categorias administrativas privadas (4=privada c/fins lucrativos, 5=privada s/fins)
_CAT_PRIVADA = {4, 5}

# TP_MODALIDADE_ENSINO: 1=Presencial, 2=EaD
_MODALIDADE_EAD = 2


def consolidate_censo_superior(
    ies_df: pd.DataFrame,
    cur_df: pd.DataFrame,
    municipio_codigo: int | None = MUNICIPIO_SALVADOR,
    include_ead: bool = True,
    apenas_privadas: bool = False,
) -> pd.DataFrame:
    """
    Consolida IES + Cursos em uma linha por IES.

    Parâmetros
    ----------
    municipio_codigo : filtra IES por CO_MUNICIPIO_IES. None = sem filtro.
    include_ead      : se False, exclui cursos com TP_MODALIDADE_ENSINO == 2.
    apenas_privadas  : se True, mantém apenas TP_CATEGORIA_ADMINISTRATIVA in {4, 5}.

    Retorna DataFrame com QT_MAT_BAS = soma de QT_MAT dos cursos por IES.
    """
    # ── 1. Matrículas por IES (soma de cursos)
    cur = cur_df.copy()
    cur["QT_MAT"] = pd.to_numeric(
        cur.get("QT_MAT", pd.Series(dtype=float)), errors="coerce"
    ).fillna(0)

    if not include_ead and "TP_MODALIDADE_ENSINO" in cur.columns:
        n_antes = len(cur)
        cur = cur[cur["TP_MODALIDADE_ENSINO"] != _MODALIDADE_EAD]
        LOG.info("EaD excluído: %s → %s cursos.", n_antes, len(cur))

    mat_por_ies = (
        cur.groupby("CO_IES", sort=False)["QT_MAT"]
        .sum()
        .reset_index()
        .rename(columns={"QT_MAT": "QT_MAT_BAS"})
    )

    # ── 2. Colunas do arquivo IES
    cols_ok = [c for c in _IES_COLS if c in ies_df.columns]
    ies = ies_df[cols_ok].copy()

    # ── 3. Join IES ← matrículas
    resultado = ies.merge(mat_por_ies, on="CO_IES", how="left")
    resultado["QT_MAT_BAS"] = (
        pd.to_numeric(resultado["QT_MAT_BAS"], errors="coerce").fillna(0).astype(int)
    )

    # ── 4. Filtro municipal
    if municipio_codigo is not None and "CO_MUNICIPIO_IES" in resultado.columns:
        n_antes = len(resultado)
        resultado = resultado[resultado["CO_MUNICIPIO_IES"] == municipio_codigo]
        LOG.info(
            "Filtro municipal %s: %s → %s IES.", municipio_codigo, n_antes, len(resultado)
        )

    # ── 5. Filtro privadas (opcional)
    if apenas_privadas and "TP_CATEGORIA_ADMINISTRATIVA" in resultado.columns:
        n_antes = len(resultado)
        resultado = resultado[
            resultado["TP_CATEGORIA_ADMINISTRATIVA"].isin(_CAT_PRIVADA)
        ]
        LOG.info("Filtro privadas: %s → %s IES.", n_antes, len(resultado))

    LOG.info(
        "Censo Superior consolidado: %s IES · QT_MAT_BAS=%s.",
        len(resultado),
        resultado["QT_MAT_BAS"].sum(),
    )

    return resultado.reset_index(drop=True)
