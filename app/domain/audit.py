"""
Trilha de auditoria do pipeline fiscal DMS × Censo.

Cada processamento completo (merge determinístico) gera um MergeAuditRecord
gravado em outputs/audit_log.jsonl (append-only, uma linha JSON por execução).

Uso:
    from domain.audit import MergeAuditRecord
    record = MergeAuditRecord(
        dms_hash="abc123",
        censo_hash="def456",
        exercise=2025,
        ...
    )
    record.append_to_log(app_dir / "outputs" / "audit_log.jsonl")
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger(__name__)


@dataclass
class MergeAuditRecord:
    """Registro imutável de uma execução completa do pipeline de merge."""

    # Identificação temporal
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    # Hashes dos arquivos de entrada (SHA-256 primeiros 16 chars)
    dms_hash: str = ""
    escola_hash: str = ""
    matricula_hash: str = ""

    # Contexto geográfico / temporal
    exercise: int = 0
    municipio_codigo: str = ""
    municipio_label: str = ""
    uf: str = ""
    modo_ui: str = ""  # "operacional" | "simples" | "avancado"

    # Volumes de entrada
    n_dms_linhas: int = 0
    n_escola_linhas_original: int = 0
    n_escola_linhas_apos_municipal: int = 0
    n_escola_linhas_apos_fiscal: int = 0
    n_censo_consolidado_linhas: int = 0

    # Filtro fiscal aplicado na consolidação
    filtro_publicas_aplicado: bool = True
    n_publicas_excluidas: int = 0
    n_superior_puro_excluidas: int = 0
    dependencia_col_encontrada: bool = True

    # Resultado do merge determinístico por CNPJ
    n_match_cnpj_exato: int = 0
    n_multiplas_escolas: int = 0
    n_sem_correspondencia_cnpj: int = 0
    n_sem_cnpj_dms: int = 0
    n_cnpj_invalido_dms: int = 0
    n_match_texto_complementar: int = 0
    n_sem_correspondencia_texto: int = 0

    # Parâmetros de merge usados
    cnpj_col_dms: str = ""
    cnpj_col_censo: str = ""
    tempo_merge_segundos: float = 0.0

    # Totais fiscais agregados do resultado
    total_iss: float = 0.0
    total_matriculas_censo: float = 0.0
    total_matriculas_dms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def append_to_log(self, log_path: Path) -> None:
        """Grava o registro ao final do arquivo JSONL (cria se não existir)."""
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(self.to_json_line() + "\n")
            LOG.info("Audit log gravado: %s", log_path)
        except Exception as exc:
            LOG.warning("Falha ao gravar audit_log.jsonl: %s", exc)

    @classmethod
    def from_summary(
        cls,
        summary: object,
        *,
        dms_hash: str = "",
        escola_hash: str = "",
        matricula_hash: str = "",
        exercise: int = 0,
        municipio_codigo: str = "",
        municipio_label: str = "",
        uf: str = "",
        modo_ui: str = "",
        n_dms_linhas: int = 0,
        n_escola_linhas_original: int = 0,
        n_escola_linhas_apos_municipal: int = 0,
        n_escola_linhas_apos_fiscal: int = 0,
        n_censo_consolidado_linhas: int = 0,
        filtro_fiscal_meta: dict | None = None,
        cnpj_col_dms: str = "",
        cnpj_col_censo: str = "",
        total_iss: float = 0.0,
        total_matriculas_censo: float = 0.0,
        total_matriculas_dms: float = 0.0,
    ) -> "MergeAuditRecord":
        """
        Constrói o record a partir do CNPJDeterministicSummary retornado por
        deterministic_merge_by_cnpj, complementado pelos metadados de contexto.
        """
        ff = filtro_fiscal_meta or {}
        s = summary  # type: ignore[assignment]

        def _g(attr: str, default: int = 0) -> int:
            return int(getattr(s, attr, default))

        return cls(
            dms_hash=dms_hash,
            escola_hash=escola_hash,
            matricula_hash=matricula_hash,
            exercise=exercise,
            municipio_codigo=municipio_codigo,
            municipio_label=municipio_label,
            uf=uf,
            modo_ui=modo_ui,
            n_dms_linhas=n_dms_linhas or _g("linhas_dms"),
            n_escola_linhas_original=n_escola_linhas_original,
            n_escola_linhas_apos_municipal=n_escola_linhas_apos_municipal,
            n_escola_linhas_apos_fiscal=n_escola_linhas_apos_fiscal,
            n_censo_consolidado_linhas=n_censo_consolidado_linhas,
            filtro_publicas_aplicado=True,
            n_publicas_excluidas=int(ff.get("n_publicas_excluidas", 0)),
            n_superior_puro_excluidas=int(ff.get("n_superior_puro_excluidas", 0)),
            dependencia_col_encontrada=not bool(ff.get("dependencia_col_missing", False)),
            n_match_cnpj_exato=_g("match_cnpj_exato"),
            n_multiplas_escolas=_g("multiplas_escolas_mesmo_cnpj"),
            n_sem_correspondencia_cnpj=_g("sem_correspondencia_cnpj"),
            n_sem_cnpj_dms=_g("sem_cnpj_dms"),
            n_cnpj_invalido_dms=_g("cnpj_dms_invalido"),
            cnpj_col_dms=cnpj_col_dms,
            cnpj_col_censo=cnpj_col_censo,
            tempo_merge_segundos=float(getattr(s, "tempo_segundos", 0.0)),
            total_iss=total_iss,
            total_matriculas_censo=total_matriculas_censo,
            total_matriculas_dms=total_matriculas_dms,
        )
