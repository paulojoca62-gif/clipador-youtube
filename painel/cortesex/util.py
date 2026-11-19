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


_job = None


def _job_windows():
    """Job Object com KILL_ON_JOB_CLOSE: quando o CortesEX-PRESS morre (até à força), o Windows mata os filhos."""
    global _job
    if _job is not None:
        return _job
    import ctypes
    from ctypes import wintypes

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

    class EXT(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    info = EXT()
    info.Basic.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise OSError(ctypes.get_last_error(), "não consegui criar o Job Object")
    _job = (k32, job)
    return _job


def _morrer_com_o_pai():
    """Linux: o filho recebe SIGKILL se a thread que o criou (e o programa) morrer."""
    import ctypes
    import signal
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)   # PR_SET_PDEATHSIG


def popen_vinculado(cmd, **kw) -> subprocess.Popen:
    """Popen de processo longo (gravação) que não sobrevive ao CortesEX-PRESS, nem se ele for morto à força.
    Cada um ganha o próprio grupo de processos, para `matar_arvore` levar junto os filhos dele."""
    flags = SEM_JANELA
    if sys.platform.startswith("linux"):
        kw.setdefault("preexec_fn", _morrer_com_o_pai)
    if sys.platform == "win32":
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kw.setdefault("start_new_session", True)
    proc = subprocess.Popen(cmd, creationflags=flags, **kw)
    if sys.platform == "win32":
        try:
            k32, job = _job_windows()
            if not k32.AssignProcessToJobObject(job, int(proc._handle)):
                raise OSError("AssignProcessToJobObject falhou")
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger("cortesex").warning("não consegui vincular o processo filho ao programa: %s", e)
    return proc


def matar_arvore(proc, espera=5) -> None:
    """Encerra o processo e todos os que ele abriu, e espera terminar (sem deixar zumbi)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                           creationflags=SEM_JANELA, timeout=15)
        else:
            import os
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=espera)
    except subprocess.TimeoutExpired:
        pass


def rodar(cmd, timeout=None, **kw) -> subprocess.CompletedProcess:
    """Roda um programa sem abrir janela. Se passar do timeout, mata e devolve código 124."""
    try:
        return subprocess.run(cmd, capture_output=True, creationflags=SEM_JANELA, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, e.stdout or b"", (e.stderr or b"") + b"\n[tempo esgotado]")
