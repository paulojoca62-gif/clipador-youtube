import subprocess
import time

import pytest
import requests

from cortesex import config as cfgmod
from cortesex import motor as motormod


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    """Nenhum teste fala com a internet (redes sociais, Gemini, Telegram). Chamadas reais são bugs de teste."""
    def bloqueado(*a, **k):
        raise AssertionError("teste tentou acessar a rede")
    monkeypatch.setattr(requests, "post", bloqueado)
    monkeypatch.setattr(requests, "get", bloqueado)
    monkeypatch.setattr(requests, "put", bloqueado)
    monkeypatch.setattr(requests.Session, "request", bloqueado)


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTESEX_DADOS", str(tmp_path))
    c = cfgmod.Config(tmp_path / "config.json")
    m = motormod.Motor(config=c, pasta=tmp_path)
    # vídeos de teste são 1 byte: a validação real tem testes próprios (test_midia.py)
    monkeypatch.setattr(motormod.midia, "validar", lambda *a, **k: {"ok": True})
    return m, tmp_path


def autorizar(m, canal="alanzoka", redes=("youtube", "tiktok", "facebook"), **extra):
    canais = [c for c in m.config.get("canais") if c["nome"] != canal]
    aut = {"status": "autorizado", "publicar": True, "redes": list(redes), **extra}
    canais.append({"nome": canal, "autorizacao": aut})
    m.config.salvar("canais", canais)


def corte(m, pasta, score, titulo="TÍTULO", horas_atras=0.1, canal="alanzoka"):
    arq = pasta / f"c{score}_{time.time_ns()}.mp4"
    arq.write_bytes(b"x")
    return m.banco.novo_corte(canal, arq, time.time() - horas_atras * 3600, 55, score, "pico", ["kkk"], "fala",
                              titulo, "legenda", ["#a"])


def video_teste(destino, segundos=6, w=1280, h=720, audio=True, tom=440):
    """Gera um vídeo real com ffmpeg (barras de teste + tom)."""
    from cortesex import util
    cmd = [util.ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate=30:duration={segundos}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={tom}:duration={segundos}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(destino)]
    subprocess.run(cmd, check=True)
    return destino
