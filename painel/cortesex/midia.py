"""Leitura e validação de vídeo e análise do áudio (tudo local, com ffprobe/ffmpeg + numpy)."""

import json
import math
import subprocess
from pathlib import Path

from cortesex import util


class MidiaInvalida(RuntimeError):
    pass


def sondar(arquivo) -> dict:
    """Dados do contêiner e das faixas via ffprobe. Sem ffprobe, usa o ffmpeg (menos detalhes)."""
    sonda = util.ffprobe()
    if sonda:
        r = util.rodar([sonda, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                        str(arquivo)], timeout=60)
        if r.returncode != 0:
            raise MidiaInvalida("o ffprobe não conseguiu ler o arquivo: " + r.stderr.decode(errors="ignore")[-200:])
        return json.loads(r.stdout or b"{}")
    r = util.rodar([util.ffmpeg(), "-hide_banner", "-i", str(arquivo)], timeout=60)
    texto = r.stderr.decode(errors="ignore")
    if "Invalid data" in texto or "No such file" in texto:
        raise MidiaInvalida("o ffmpeg não conseguiu ler o arquivo")
    import re
    dados = {"format": {}, "streams": []}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", texto)
    if m:
        dados["format"]["duration"] = str(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]))
    v = re.search(r"Video: .*?, (\d{2,5})x(\d{2,5}).*?([\d.]+) fps", texto)
    if v:
        dados["streams"].append({"codec_type": "video", "width": int(v[1]), "height": int(v[2]),
                                 "avg_frame_rate": f"{v[3]}/1"})
    if "Audio:" in texto:
        dados["streams"].append({"codec_type": "audio"})
    return dados


def _fps(texto: str) -> float:
    try:
        n, d = str(texto).split("/")
        return float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def validar(arquivo, duracao_esperada=None, largura=1080, altura=1920, exigir_audio=True) -> dict:
    """Confere que o vídeo pode ir para as redes. Devolve o resumo ou levanta MidiaInvalida."""
    p = Path(arquivo)
    if not p.exists():
        raise MidiaInvalida("o arquivo não existe")
    tamanho = p.stat().st_size
    if tamanho < 10_000:
        raise MidiaInvalida(f"arquivo pequeno demais ({tamanho} bytes)")
    try:
        with open(p, "rb") as f:
            f.read(1)
    except OSError as e:
        raise MidiaInvalida(f"o arquivo não pode ser aberto: {e}") from e
    d = sondar(p)
    videos = [s for s in d.get("streams", []) if s.get("codec_type") == "video"]
    audios = [s for s in d.get("streams", []) if s.get("codec_type") == "audio"]
    if not videos:
        raise MidiaInvalida("sem faixa de vídeo")
    if exigir_audio and not audios:
        raise MidiaInvalida("sem faixa de áudio")
    try:
        dur = float(d.get("format", {}).get("duration") or videos[0].get("duration") or 0)
    except ValueError:
        dur = 0.0
    if dur < 3:
        raise MidiaInvalida(f"duração inválida ({dur:.1f} s)")
    if duracao_esperada and abs(dur - duracao_esperada) > max(3.0, duracao_esperada * 0.1):
        raise MidiaInvalida(f"duração {dur:.1f} s diferente da esperada ({duracao_esperada:.1f} s): render incompleto?")
    w, h = int(videos[0].get("width") or 0), int(videos[0].get("height") or 0)
    if largura and (w, h) != (largura, altura):
        raise MidiaInvalida(f"resolução {w}x{h}, esperado {largura}x{altura}")
    fps = _fps(videos[0].get("avg_frame_rate") or videos[0].get("r_frame_rate") or "0/1")
    if fps and not 10 <= fps <= 120:
        raise MidiaInvalida(f"fps estranho ({fps:.1f})")
    return {"ok": True, "duracao": round(dur, 2), "largura": w, "altura": h, "fps": round(fps, 2),
            "audio": bool(audios), "bytes": tamanho, "codec": videos[0].get("codec_name", "")}


def ler_audio(arquivo, ini=None, dur=None, taxa=16000):
    """Áudio mono 16 kHz como numpy float32 (via ffmpeg)."""
    import numpy as np
    cmd = [util.ffmpeg(), "-v", "error"]
    if ini is not None:
        cmd += ["-ss", f"{ini:.2f}"]
    cmd += ["-i", str(arquivo)]
    if dur is not None:
        cmd += ["-t", f"{dur:.2f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(taxa), "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True, creationflags=util.SEM_JANELA, timeout=300)
    if r.returncode != 0:
        raise MidiaInvalida("não consegui ler o áudio")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def perfil_audio(audio, taxa=16000, passo=0.5) -> dict:
    """Volume (dBFS) a cada `passo` segundos, silêncio e saturação."""
    import numpy as np
    n = int(taxa * passo)
    if len(audio) < n:
        return {"passo": passo, "db": [], "mediana_db": None, "pico_db": None, "silencio": None, "saturacao": None}
    blocos = audio[: len(audio) // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(blocos ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    mediana = float(np.median(db))
    return {
        "passo": passo,
        "db": [round(float(x), 1) for x in db],
        "mediana_db": round(mediana, 1),
        "pico_db": round(float(np.percentile(db, 97)), 1),
        "silencio": round(float(np.mean(db < -45)), 3),
        "saturacao": round(float(np.mean(np.abs(audio) > 0.98)), 5),
    }


def janela_db(perfil: dict, ini: float, fim: float) -> list:
    if not perfil.get("db"):
        return []
    p = perfil["passo"]
    return perfil["db"][max(int(ini / p), 0): max(int(math.ceil(fim / p)), 0)]
