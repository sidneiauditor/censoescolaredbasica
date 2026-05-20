"""Constantes de configuração globais da aplicação."""

from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

SELECT_SENTINEL = "-- Selecionar coluna --"
UX_SIMPLES = "Simples (recomendado) — município + automático"
UX_AVANCADO = "Avançado — mapeamento manual e diagnósticos"
