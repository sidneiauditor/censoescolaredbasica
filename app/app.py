"""
App local Streamlit — cruzamento DMS-Educação × Censo (Básico e Superior).

Modos
-----
  operacional  → Ed. Básica Salvador  (controllers.operacional)
  superior     → Ed. Superior Salvador (controllers.operacional_superior)
  tecnico      → Técnico completo      (controllers.tecnico)
"""

from __future__ import annotations

from config import APP_DIR
from controllers.common import configure_logging
from controllers.operacional import main_operacional_ui
from controllers.operacional_superior import main_superior_ui
from controllers.tecnico import render_main_technical
from state.pipeline_state import PipelineState, SessionKeys
from ui import mode as ui_mode

import streamlit as st


def main() -> None:
    configure_logging()
    st.set_page_config(
        page_title="DMS × Censo · CIF",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    modo = ui_mode.modo_sidebar_radio()

    with st.sidebar:
        if modo in (ui_mode.MODO_OPERACIONAL, ui_mode.MODO_SUPERIOR):
            ex = st.number_input(
                "Ano do Censo / DMS",
                min_value=1996, max_value=2050,
                value=PipelineState.exercise(),
                step=1,
                key="cif_operacional_ctx_exercise",
                help="Ano de referência — usado para filtro de maio no relatório de divergências.",
            )
            st.session_state[SessionKeys.EXERCISE_DEFAULT] = int(ex)
            ui_mode.salvador_sidebar_contexto_ui(int(ex))

            if modo == ui_mode.MODO_OPERACIONAL:
                st.checkbox(
                    "Processar automaticamente quando os três arquivos forem válidos",
                    value=bool(st.session_state.get(ui_mode.AUTO_PROCESS_KEY, True)),
                    key=ui_mode.AUTO_PROCESS_KEY,
                )
        else:
            st.header("Ajuda rápida")
            st.markdown(
                "- Fluxo típico (modo **Técnico**): **carregar Escola (+ Matrícula)** → "
                "UF/município → consolidar → **DMS** → **Etapa 2** (CNPJ) → **Etapa 3** (cruzamento).\n\n"
                "- Export **divergências** ao fim da Etapa 3.\n\n"
                "- Logs em `outputs/app.log` · use **⋮ → Clear cache** ao trocar ficheiros grandes."
            )

        APP_DIR.mkdir(parents=True, exist_ok=True)
        (APP_DIR / "uploads").mkdir(parents=True, exist_ok=True)
        (APP_DIR / "outputs").mkdir(parents=True, exist_ok=True)

    if modo == ui_mode.MODO_OPERACIONAL:
        main_operacional_ui()
    elif modo == ui_mode.MODO_SUPERIOR:
        main_superior_ui()
    else:
        render_main_technical()


if __name__ == "__main__":
    main()
