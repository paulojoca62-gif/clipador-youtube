"""Caminhos de ferramentas e utilitários que funcionam igual no Windows e no Linux."""

import os
import random
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


def ffprobe() -> str:
    nome = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    for cand in (pasta_app() / "bin" / nome, Path(sys.executable).parent / "bin" / nome):
        if cand.exists():
            return str(cand)
    return shutil.which("ffprobe") or ""


def backoff(tentativa: int, base: float = 30, maximo: float = 3600, jitter: float = 0.2) -> float:
    """Espera exponencial: base, 2x, 4x... até o máximo, com variação aleatória de ±jitter."""
    espera = min(maximo, base * (2 ** max(tentativa - 1, 0)))
    return max(1.0, espera * (1 + random.uniform(-jitter, jitter)))


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


def rodar(cmd, timeout=None, **kw) -> subprocess.CompletedProcess:
    """Roda um programa sem abrir janela. Se passar do timeout, mata e devolve código 124."""
    try:
        return subprocess.run(cmd, capture_output=True, creationflags=SEM_JANELA, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or b"", (e.stderr or b"") + b"\n[tempo esgotado]")
