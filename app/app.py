"""
App local Streamlit — cruzamento DMS-Educação × Censo Escolar.

Ponto de entrada: delega inteiramente aos controllers.
  - Modo operacional Salvador → controllers.operacional.main_operacional_ui
  - Modo técnico             → controllers.tecnico.render_main_technical
"""

from __future__ import annotations

from config import APP_DIR
from controllers.common import configure_logging
from controllers.operacional import main_operacional_ui
from controllers.tecnico import render_main_technical
from state.pipeline_state import PipelineState, SessionKeys

import streamlit as st

from ui import mode as ui_mode


def main() -> None:
    configure_logging()
    st.set_page_config(
        page_title="DMS × Censo Escolar · CIF",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    modo_op = ui_mode.modo_sidebar_radio()

    with st.sidebar:
        if modo_op:
            ex = st.number_input(
                "Ano do Censo",
                min_value=1996, max_value=2050,
                value=PipelineState.exercise(),
                step=1,
                key="cif_operacional_ctx_exercise",
                help="Referência temporal registada nos metadados da base integrada final.",
            )
            st.session_state[SessionKeys.EXERCISE_DEFAULT] = int(ex)
            ui_mode.salvador_sidebar_contexto_ui(int(ex))
            st.checkbox(
                "Processar automaticamente quando os três arquivos forem válidos",
                value=bool(st.session_state.get(ui_mode.AUTO_PROCESS_KEY, True)),
                key=ui_mode.AUTO_PROCESS_KEY,
                help="Quando há um novo trio de ficheiros, o encadeamento completo corre neste rerun.",
            )
        else:
            st.header("Ajuda rápida")
            st.markdown(
                "- Fluxo típico (modo **Técnico**): **carregar Escola (+ Matrícula)** → UF/município → "
                "consolidar Escola⊕Matrícula → **DMS** → **Etapa 2** (CNPJ) → **Etapa 3** (cruzamento).\n\n"
                "- Export **base integrada** ao fim da Etapa 3.\n\n"
                "- Logs em `outputs/app.log` · use **⋮ → Clear cache** ao trocar ficheiros muito grandes."
            )

        # Garantir que as pastas de trabalho existem
        APP_DIR.mkdir(parents=True, exist_ok=True)
        (APP_DIR / "uploads").mkdir(parents=True, exist_ok=True)
        (APP_DIR / "outputs").mkdir(parents=True, exist_ok=True)

    if modo_op:
        main_operacional_ui()
    else:
        render_main_technical()


if __name__ == "__main__":
    main()
