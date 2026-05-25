"""
Inicializador sem terminal — duplo clique abre o app no browser.
"""
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent / "app"
STREAMLIT = BASE / ".venv" / "Scripts" / "streamlit.exe"
APP = BASE / "app.py"

subprocess.Popen(
    [str(STREAMLIT), "run", str(APP)],
    cwd=str(BASE),
    creationflags=subprocess.CREATE_NO_WINDOW,
)
