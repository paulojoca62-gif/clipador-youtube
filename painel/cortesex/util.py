"""Caminhos de ferramentas e utilitários que funcionam igual no Windows e no Linux."""

import os
import shutil
import ssl
import subprocess
import sys
from pathlib import Path

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no Windows, ffmpeg sem abrir console


def pasta_app() -> Path:
    """Onde estão os arquivos do programa (dentro do .exe ou na pasta do código)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def ffmpeg() -> str:
    nome = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    for cand in (pasta_app() / "bin" / nome, Path(sys.executable).parent / "bin" / nome):
        if cand.exists():
            return str(cand)
    return shutil.which("ffmpeg") or nome


def comando_streamlink() -> list:
    """No .exe, o próprio programa vira o streamlink (veja run.py); no código, python -m streamlink."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--streamlink"]
    return [sys.executable, "-m", "streamlink"]


def pasta_fontes() -> Path:
    return pasta_app() / "fonts"


def contexto_ssl() -> ssl.SSLContext:
    arquivo = os.environ.get("SSL_CERT_FILE")
    if not arquivo:
        try:
            import certifi
            arquivo = certifi.where()
        except ImportError:
            arquivo = None
    ctx = ssl.create_default_context(cafile=arquivo)
    if sys.platform == "win32":
        ctx.load_default_certs()
    return ctx


def rodar(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, creationflags=SEM_JANELA, **kw)
