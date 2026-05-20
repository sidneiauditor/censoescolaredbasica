"""
Relatório Operacional de Divergências — DMS Educação × Censo Escolar.

Produto institucional da Inteligência Fiscal / GEFIS.
Exporta UMA aba com UMA linha por CNPJ declarante na DMS.

Função pública
--------------
exportar_divergencias_operacionais(df, exercicio) -> bytes (.xlsx)
"""
from __future__ import annotations

import io
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# ─── constantes visuais
_BLUE_HEADER   = "1F4E79"   # azul institucional
_ZEBRA_EVEN    = "EBF3FB"   # azul muito claro — linhas pares
_HIGHLIGHT_TOP = "FCE4D6"   # laranja suave — maiores divergências

_COLS_FINAIS = [
    "CNPJ",
    "Razão Social",
    "Qtd Matrículas Censo Escolar",
    "Qtd Média Matrículas DMS (Ano)",
    "Qtd Matrículas DMS Educação (Maio)",
    "Diferença Censo × Média Ano",
    "Diferença Censo × Maio",
]

_COL_WIDTHS = {
    "CNPJ":                               18,
    "Razão Social":                       50,
    "Qtd Matrículas Censo Escolar":       22,
    "Qtd Média Matrículas DMS (Ano)":     24,
    "Qtd Matrículas DMS Educação (Maio)": 26,
    "Diferença Censo × Média Ano":        24,
    "Diferença Censo × Maio":             22,
}

_FMT_INTEIRO = "#,##0"
_FMT_DECIMAL = "#,##0.0"

_NUMERIC_COLS = {
    "Qtd Matrículas Censo Escolar":       _FMT_INTEIRO,
    "Qtd Média Matrículas DMS (Ano)":     _FMT_DECIMAL,
    "Qtd Matrículas DMS Educação (Maio)": _FMT_INTEIRO,
    "Diferença Censo × Média Ano":        _FMT_DECIMAL,
    "Diferença Censo × Maio":             _FMT_DECIMAL,
}


# ─── público ──────────────────────────────────────────────────────────────────

def exportar_divergencias_operacionais(
    df: pd.DataFrame,
    exercicio: int | None = None,
    col_censo_entidade: str = "censo__CO_ENTIDADE",
) -> bytes:
    """
    Gera o Relatório Operacional de Divergências em .xlsx.

    Parâmetros
    ----------
    df               : base integrada DMS × Censo (saída do pipeline de merge).
    exercicio        : ano de referência para filtro de maio. Auto-detectado se None.
    col_censo_entidade : coluna que identifica a entidade de ensino no Censo.
                        "censo__CO_ENTIDADE" para Ed. Básica (padrão);
                        "censo__CO_IES" para Ensino Superior.

    Retorna bytes prontos para st.download_button(data=...).
    """
    work = df.copy()

    # ── 1. Garantir tipos numéricos
    work["__qtd"] = pd.to_numeric(work.get("dms__QUANTIDADE", pd.Series(dtype=float)), errors="coerce")
    work["__censo_mat"] = pd.to_numeric(work.get("censo__QT_MAT_BAS", pd.Series(dtype=float)), errors="coerce")
    work["__comp"] = pd.to_datetime(work.get("dms__DTCOMPETENCIA", pd.Series(dtype=object)), errors="coerce")

    # ── 2. Exercício (ano de referência)
    if exercicio is None:
        anos = work["__comp"].dt.year.dropna()
        exercicio = int(anos.mode().iloc[0]) if not anos.empty else 2025

    # ── 3. Média anual por CNPJ
    #   Soma por (CNPJ, mês) primeiro — para não inflar por múltiplos CGAs —
    #   depois média das somas mensais.
    comp_valida = work["__comp"].notna()
    mensal = (
        work.loc[comp_valida]
        .assign(__mes=work.loc[comp_valida, "__comp"].dt.to_period("M"))
        .groupby(["dms__NUCNPJ", "__mes"], sort=False)["__qtd"]
        .sum()
        .reset_index()
    )
    media_anual: pd.Series = (
        mensal.groupby("dms__NUCNPJ", sort=False)["__qtd"]
        .mean()
        .rename("media_anual")
    )

    # ── 4. Valor de Maio
    mask_maio = (work["__comp"].dt.month == 5) & (work["__comp"].dt.year == exercicio)
    qtd_maio: pd.Series = (
        work.loc[mask_maio]
        .groupby("dms__NUCNPJ", sort=False)["__qtd"]
        .sum()
        .rename("qtd_maio")
    )

    # ── 5. Matrículas Censo por CNPJ — soma de entidades únicas vinculadas
    censo_por_cnpj: pd.Series
    if col_censo_entidade in work.columns:
        entidades_unicas = (
            work.loc[work[col_censo_entidade].notna()]
            .drop_duplicates(["dms__NUCNPJ", col_censo_entidade])
        )
        censo_por_cnpj = (
            entidades_unicas.groupby("dms__NUCNPJ", sort=False)["__censo_mat"]
            .sum()
            .rename("qtd_censo")
        )
    else:
        censo_por_cnpj = pd.Series(dtype=float, name="qtd_censo")

    # ── 6. Razão Social (primeiro valor não nulo por CNPJ)
    razao_social: pd.Series = (
        work.groupby("dms__NUCNPJ", sort=False)["dms__NMRAZAOSOCIAL"]
        .first()
        .rename("razao_social")
    )

    # ── 7. Montar tabela única por CNPJ
    resultado = (
        razao_social.to_frame()
        .join(media_anual, how="left")
        .join(qtd_maio, how="left")
        .join(censo_por_cnpj, how="left")
        .reset_index()
        .rename(columns={"dms__NUCNPJ": "cnpj_raw"})
    )

    # ── 8. Normalizar CNPJ para 14 dígitos (zero-fill)
    resultado["CNPJ"] = (
        resultado["cnpj_raw"]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
        .str.zfill(14)
    )

    # ── 9. Diferenças absolutas
    resultado["Diferença Censo × Média Ano"] = (
        resultado["qtd_censo"] - resultado["media_anual"]
    ).abs()
    resultado["Diferença Censo × Maio"] = (
        resultado["qtd_censo"] - resultado["qtd_maio"]
    ).abs()

    # ── 10. Filtro: manter apenas linhas com quantidade DMS relevante
    tem_qtd = resultado["media_anual"].gt(0) | resultado["qtd_maio"].gt(0)
    resultado = resultado.loc[tem_qtd | resultado["media_anual"].isna() & resultado["qtd_maio"].isna()]
    # Excluir linhas sem absolutamente nenhuma informação quantitativa
    resultado = resultado.loc[resultado["media_anual"].notna() | resultado["qtd_maio"].notna()]

    # ── 11. Ordenar por divergência descrescente
    resultado = resultado.sort_values(
        ["Diferença Censo × Média Ano", "Diferença Censo × Maio"],
        ascending=[False, False],
        na_position="last",
    )

    # ── 12. Renomear e selecionar colunas finais
    resultado = resultado.rename(columns={
        "razao_social": "Razão Social",
        "qtd_censo":    "Qtd Matrículas Censo Escolar",
        "media_anual":  "Qtd Média Matrículas DMS (Ano)",
        "qtd_maio":     "Qtd Matrículas DMS Educação (Maio)",
    })

    colunas_presentes = [c for c in _COLS_FINAIS if c in resultado.columns]
    resultado = resultado[colunas_presentes].reset_index(drop=True)

    return _escrever_xlsx(resultado)


# ─── internos ─────────────────────────────────────────────────────────────────

def _escrever_xlsx(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, sheet_name="Divergências", engine="openpyxl")
    buf.seek(0)

    wb = load_workbook(buf)
    ws = wb["Divergências"]

    col_names = [ws.cell(1, c).value or "" for c in range(1, ws.max_column + 1)]

    _formatar_cabecalho(ws, col_names)
    _formatar_dados(ws, col_names)
    _ajustar_larguras(ws, col_names)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _formatar_cabecalho(ws: Any, col_names: list[str]) -> None:
    header_fill = PatternFill("solid", fgColor=_BLUE_HEADER)
    header_font = Font(bold=True, color="FFFFFF", size=10, name="Calibri")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for c in range(1, ws.max_column + 1):
        cell = ws.cell(1, c)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center

    ws.row_dimensions[1].height = 32


def _formatar_dados(ws: Any, col_names: list[str]) -> None:
    zebra = PatternFill("solid", fgColor=_ZEBRA_EVEN)
    destaque = PatternFill("solid", fgColor=_HIGHLIGHT_TOP)

    # Identificar coluna de divergência principal para threshold
    dif_col_idx: int | None = None
    try:
        dif_col_idx = col_names.index("Diferença Censo × Média Ano") + 1
    except ValueError:
        pass

    # Calcular limiar do top-20 %
    threshold: float | None = None
    if dif_col_idx is not None:
        valores = [
            ws.cell(r, dif_col_idx).value
            for r in range(2, ws.max_row + 1)
            if isinstance(ws.cell(r, dif_col_idx).value, (int, float))
               and ws.cell(r, dif_col_idx).value > 0
        ]
        if valores:
            top_n = max(1, len(valores) // 5)
            threshold = sorted(valores, reverse=True)[top_n - 1]

    center_num = Alignment(horizontal="center", vertical="center")
    left_txt   = Alignment(horizontal="left",   vertical="center")

    for row_idx in range(2, ws.max_row + 1):
        # Determinar preenchimento da linha
        dif_val = ws.cell(row_idx, dif_col_idx).value if dif_col_idx else None
        eh_destaque = (
            threshold is not None
            and isinstance(dif_val, (int, float))
            and dif_val >= threshold
        )
        row_fill = destaque if eh_destaque else (zebra if row_idx % 2 == 0 else None)

        for col_idx, col_name in enumerate(col_names, start=1):
            cell = ws.cell(row_idx, col_idx)

            if row_fill:
                cell.fill = row_fill

            if col_name in _NUMERIC_COLS:
                cell.number_format = _NUMERIC_COLS[col_name]
                cell.alignment = center_num
            elif col_name == "CNPJ":
                cell.alignment = center_num
            else:
                cell.alignment = left_txt


def _ajustar_larguras(ws: Any, col_names: list[str]) -> None:
    for col_idx, col_name in enumerate(col_names, start=1):
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = _COL_WIDTHS.get(str(col_name), 20)
