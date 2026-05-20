"""
Testes de resolver_vigencia_dms — serviço de resolução de vigência da DMS.

Cobre:
  - Remoção de CANCELADAS
  - Substituição de ORIGINAL por RETIFICADORA na mesma competência/CNPJ
  - Coexistência de períodos distintos sem interferência
  - Degradação graciosamente sem colunas TIPO, CNPJ ou competência
  - Case-insensitive nos valores de SITUACAO e TIPO
  - Base vazia
"""

from __future__ import annotations

import pandas as pd
import pytest

from services.cnpj_aggregation import resolver_vigencia_dms


def _df(**kwargs: list) -> pd.DataFrame:
    return pd.DataFrame(kwargs)


# ─────────────────────────────────────────────────────── fixtures básicas

class TestRemoveCanceladas:
    def test_remove_cancelada_simples(self):
        df = _df(
            NUCNPJ=["11111111000100", "11111111000100"],
            DTCOMPETENCIA=["2024-05-01", "2024-05-01"],
            SITUACAO=["CANCELADA", "ATIVA"],
            TIPO=["ORIGINAL", "ORIGINAL"],
            QUANTIDADE=[500, 300],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1
        assert resultado.iloc[0]["QUANTIDADE"] == 300

    def test_remove_cancelada_case_insensitive(self):
        df = _df(
            NUCNPJ=["11111111000100"],
            DTCOMPETENCIA=["2024-05-01"],
            SITUACAO=["cancelada"],
            TIPO=["ORIGINAL"],
            QUANTIDADE=[100],
        )
        resultado = resolver_vigencia_dms(df)
        assert resultado.empty

    def test_mantem_ativa(self):
        df = _df(
            NUCNPJ=["22222222000100"],
            DTCOMPETENCIA=["2024-06-01"],
            SITUACAO=["ATIVA"],
            TIPO=["ORIGINAL"],
            QUANTIDADE=[200],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1

    def test_cancelada_retificadora_ambas_removidas(self):
        """RETIFICADORA com SITUACAO=CANCELADA deve ser removida na etapa 1."""
        df = _df(
            NUCNPJ=["33333333000100", "33333333000100"],
            DTCOMPETENCIA=["2024-05-01", "2024-05-01"],
            SITUACAO=["ATIVA", "CANCELADA"],
            TIPO=["ORIGINAL", "RETIFICADORA"],
            QUANTIDADE=[100, 150],
        )
        resultado = resolver_vigencia_dms(df)
        # RETIFICADORA cancelada removida na etapa 1 → sem RETIFICADORA válida → mantém ORIGINAL
        assert len(resultado) == 1
        assert resultado.iloc[0]["TIPO"] == "ORIGINAL"
        assert resultado.iloc[0]["QUANTIDADE"] == 100


# ─────────────────────────────────────────────────────── resolução ORIGINAL×RETIFICADORA

class TestResolucaoVigencia:
    def test_retificadora_substitui_original_mesma_competencia(self):
        df = _df(
            NUCNPJ=["44444444000100", "44444444000100"],
            DTCOMPETENCIA=["2024-05-01", "2024-05-01"],
            SITUACAO=["ATIVA", "ATIVA"],
            TIPO=["ORIGINAL", "RETIFICADORA"],
            QUANTIDADE=[100, 180],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1
        assert resultado.iloc[0]["TIPO"] == "RETIFICADORA"
        assert resultado.iloc[0]["QUANTIDADE"] == 180

    def test_original_mantida_sem_retificadora(self):
        df = _df(
            NUCNPJ=["55555555000100"],
            DTCOMPETENCIA=["2024-05-01"],
            SITUACAO=["ATIVA"],
            TIPO=["ORIGINAL"],
            QUANTIDADE=[250],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1
        assert resultado.iloc[0]["TIPO"] == "ORIGINAL"

    def test_periodos_distintos_sem_interferencia(self):
        """RETIFICADORA em maio não deve afetar ORIGINAL em junho."""
        df = _df(
            NUCNPJ=["66666666000100"] * 3,
            DTCOMPETENCIA=["2024-05-01", "2024-05-01", "2024-06-01"],
            SITUACAO=["ATIVA", "ATIVA", "ATIVA"],
            TIPO=["ORIGINAL", "RETIFICADORA", "ORIGINAL"],
            QUANTIDADE=[100, 180, 220],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 2
        tipos = set(resultado["TIPO"])
        qtds  = set(resultado["QUANTIDADE"])
        assert "RETIFICADORA" in tipos
        assert "ORIGINAL" in tipos
        assert 180 in qtds   # retificadora de maio
        assert 220 in qtds   # original de junho intacta
        assert 100 not in qtds  # original de maio descartada

    def test_cnpjs_distintos_independentes(self):
        """Resolução por (CNPJ, período) — não deve cruzar CNPJs."""
        df = _df(
            NUCNPJ=["10000000000100", "10000000000100",
                    "20000000000100"],
            DTCOMPETENCIA=["2024-05-01", "2024-05-01", "2024-05-01"],
            SITUACAO=["ATIVA", "ATIVA", "ATIVA"],
            TIPO=["ORIGINAL", "RETIFICADORA", "ORIGINAL"],
            QUANTIDADE=[100, 160, 300],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 2
        # CNPJ 10... → RETIFICADORA (160)
        r10 = resultado[resultado["NUCNPJ"] == "10000000000100"]
        assert len(r10) == 1
        assert r10.iloc[0]["QUANTIDADE"] == 160
        # CNPJ 20... → ORIGINAL (300)
        r20 = resultado[resultado["NUCNPJ"] == "20000000000100"]
        assert len(r20) == 1
        assert r20.iloc[0]["QUANTIDADE"] == 300

    def test_cenario_real_original_cancelada_retificadora_paga(self):
        """
        Caso real identificado: ORIGINAL=CANCELADA, RETIFICADORA=ATIVA.
        Etapa 1 remove CANCELADA; etapa 2 mantém RETIFICADORA sem conflito.
        """
        df = _df(
            NUCNPJ=["77777777000100", "77777777000100"],
            DTCOMPETENCIA=["2024-05-01", "2024-05-01"],
            SITUACAO=["CANCELADA", "ATIVA"],
            TIPO=["ORIGINAL", "RETIFICADORA"],
            QUANTIDADE=[100, 175],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1
        assert resultado.iloc[0]["TIPO"] == "RETIFICADORA"
        assert resultado.iloc[0]["QUANTIDADE"] == 175


# ─────────────────────────────────────────────────────── degradação graciosamente

class TestDegradacao:
    def test_sem_coluna_tipo_retorna_sem_canceladas(self):
        df = _df(
            NUCNPJ=["88888888000100", "88888888000100"],
            DTCOMPETENCIA=["2024-05-01", "2024-05-01"],
            SITUACAO=["ATIVA", "CANCELADA"],
            QUANTIDADE=[100, 200],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1
        assert resultado.iloc[0]["QUANTIDADE"] == 100

    def test_sem_coluna_situacao_sem_cancelada_remove_retificadora(self):
        """Sem SITUACAO, sem CNPJ/competência: RETIFICADORA excluída conservadoramente."""
        df = _df(
            TIPO=["ORIGINAL", "RETIFICADORA"],
            QUANTIDADE=[100, 150],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 1
        assert resultado.iloc[0]["TIPO"] == "ORIGINAL"

    def test_base_vazia(self):
        df = pd.DataFrame(columns=["NUCNPJ", "DTCOMPETENCIA", "SITUACAO", "TIPO", "QUANTIDADE"])
        resultado = resolver_vigencia_dms(df)
        assert resultado.empty

    def test_sem_retificadoras_retorna_intacto(self):
        df = _df(
            NUCNPJ=["99999999000100"] * 3,
            DTCOMPETENCIA=["2024-04-01", "2024-05-01", "2024-06-01"],
            SITUACAO=["ATIVA", "ATIVA", "ATIVA"],
            TIPO=["ORIGINAL", "ORIGINAL", "ORIGINAL"],
            QUANTIDADE=[100, 200, 300],
        )
        resultado = resolver_vigencia_dms(df)
        assert len(resultado) == 3
        assert set(resultado["QUANTIDADE"]) == {100, 200, 300}
