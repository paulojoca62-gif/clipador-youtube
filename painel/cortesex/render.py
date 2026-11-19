"""Etapas de mídia do pipeline: capturar a fonte, transcrever e montar o vídeo 9:16.

- capturar: junta os blocos gravados de uma janela larga em volta do pico num arquivo "fonte" (cópia
  direta, rápido). A fonte fica guardada para re-render (editor de legendas) sem capturar de novo.
- transcrever: Whisper local com tempo por palavra + perfil de volume do áudio.
- montar: corta [ini, fim] da fonte, aplica o layout vertical e queima as legendas.
"""

import json
import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path

from cortesex import legendas, midia, util

log = logging.getLogger("cortesex")

# posições prontas da webcam para o layout "câmera em cima" (x, y, largura, altura em fração da tela)
CAM_PRESETS = {
    "inferior_direito": [0.74, 0.66, 0.25, 0.32], "superior_direito": [0.74, 0.02, 0.25, 0.32],
    "inferior_esquerdo": [0.01, 0.66, 0.25, 0.32], "superior_esquerdo": [0.01, 0.02, 0.25, 0.32],
}


class ErroEtapa(RuntimeError):
    def __init__(self, msg, definitivo=False):
        super().__init__(msg)
        self.definitivo = definitivo


def cam_valida(cam):
    if isinstance(cam, str):
        cam = CAM_PRESETS.get(cam)
    try:
        x, y, w, h = (float(v) for v in cam)
    except (TypeError, ValueError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0.05 <= w <= 1 and 0.05 <= h <= 1 and x + w <= 1.001 and y + h <= 1.001):
        return None
    return [round(x, 4), round(y, 4), round(w, 4), round(h, 4)]


def filtro(layout, cam, fps):
    cam = cam_valida(cam)
    if layout == "split" and cam:
        x, y, w, h = cam
        return (f"[0:v]split=2[a][b];"
                f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=1080:700:force_original_aspect_ratio=increase,crop=1080:700[cam];"
                f"[b]crop=trunc(ih*1080/1220/2)*2:ih,scale=1080:1220[jogo];"
                f"[cam][jogo]vstack,setsar=1,fps={fps}[v]")
    return (f"[0:v]split=2[a][b];"
            f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,scale=1080:1920[bg];"
            f"[b]scale=1080:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}[v]")


def gerar_ass(palavras, por_bloco=3):
    """Compatibilidade com a 1.x (blocos fixos de 3 palavras)."""
    segs, bloco = [], []
    for w in palavras:
        bloco.append(w)
        if len(bloco) >= por_bloco or (bloco and w[1] - bloco[0][0] > 1.2):
            segs.append({"ini": bloco[0][0], "fim": bloco[-1][1] + 0.05, "texto": " ".join(x[2] for x in bloco)})
            bloco = []
    if bloco:
        segs.append({"ini": bloco[0][0], "fim": bloco[-1][1] + 0.05, "texto": " ".join(x[2] for x in bloco)})
    return legendas.gerar_ass(segs, {"estilo": "classico"})


def capturar(segmentos, janela_ini, janela_fim, destino: Path, timeout=300) -> float:
    """Concatena os blocos da janela em `destino`. Devolve o horário (epoch) do início da fonte."""
    segs = [(ts, p) for ts, p in segmentos if janela_ini - 8 <= ts <= janela_fim]
    if not segs:
        raise ErroEtapa("a gravação desse momento não existe mais (buffer expirou ou a live caiu)", definitivo=True)
    tmp = Path(tempfile.mkdtemp(prefix="cortesex_cap_"))
    try:
        lista = tmp / "blocos.txt"
        lista.write_text("".join(f"file '{p.as_posix()}'\n" for _, p in segs), encoding="utf-8")
        saida = tmp / "fonte.mp4"
        r = util.rodar([util.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lista), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
                        "-movflags", "+faststart", str(saida)], timeout=timeout)
        if r.returncode != 0 or not saida.exists() or saida.stat().st_size < 10_000:
            raise ErroEtapa("falha ao juntar os blocos: " + r.stderr.decode(errors="ignore")[-200:])
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(saida), destino)
        return segs[0][0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Renderizador:
    def __init__(self, config):
        self.config = config
        self._modelo = None
        self._nome_modelo = None
        self._lock = threading.Lock()     # um Whisper por vez (CPU)
        self.modelo_carregado = ""

    # ----- transcrição
    def transcrever(self, fonte: Path, nome_modelo="small"):
        """Devolve (palavras [(ini, fim, texto)], perfil de volume)."""
        audio = midia.ler_audio(fonte)
        perfil = midia.perfil_audio(audio)
        if len(audio) < 16000:
            return [], perfil
        with self._lock:
            if self._modelo is None or self._nome_modelo != nome_modelo:
                from faster_whisper import WhisperModel
                from cortesex.config import pasta_dados
                log.info("carregando o Whisper '%s' (na primeira vez ele é baixado, ~500 MB)", nome_modelo)
                self._modelo = WhisperModel(nome_modelo, device="cpu", compute_type="int8",
                                            download_root=str(pasta_dados() / "modelos"))
                self._nome_modelo = nome_modelo
                self.modelo_carregado = nome_modelo
            segs, _ = self._modelo.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True,
                                              condition_on_previous_text=False)
            palavras = [(round(w.start, 3), round(w.end, 3), w.word.strip()) for s in segs for w in (s.words or [])
                        if w.word.strip()]
        return palavras, perfil

    # ----- montagem
    def montar(self, fonte: Path, ini: float, fim: float, saida: Path, layout="blur", cam=None,
               segmentos=None, cfg_legendas=None, fps=30, timeout=900) -> dict:
        dur = max(fim - ini, 1.0)
        vf = filtro(layout, cam, fps)
        tmp = Path(tempfile.mkdtemp(prefix="cortesex_mont_"))
        try:
            if segmentos:
                (tmp / "legenda.ass").write_text(legendas.gerar_ass(segmentos, cfg_legendas or {}), encoding="utf-8")
                shutil.copytree(util.pasta_fontes(), tmp / "fonts", dirs_exist_ok=True)
                vf = vf[:-3] + ",subtitles=legenda.ass:fontsdir=fonts[v]"
            t0 = time.time()
            r = util.rodar([util.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", f"{ini:.2f}", "-i", str(fonte), "-t", f"{dur:.2f}",
                            "-filter_complex", vf, "-map", "[v]", "-map", "0:a:0?",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "saida.mp4"],
                           cwd=tmp, timeout=timeout)
            if r.returncode != 0:
                raise ErroEtapa("o ffmpeg falhou ao montar: " + r.stderr.decode(errors="ignore")[-300:])
            info = midia.validar(tmp / "saida.mp4", duracao_esperada=dur)
            saida.parent.mkdir(parents=True, exist_ok=True)
            parcial = saida.with_suffix(".parcial.mp4")
            shutil.move(str(tmp / "saida.mp4"), parcial)
            parcial.replace(saida)            # troca atômica: nunca fica um .mp4 pela metade
            info["segundos_render"] = round(time.time() - t0, 1)
            return info
        except midia.MidiaInvalida as e:
            raise ErroEtapa(f"o vídeo montado não passou na validação: {e}") from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def meta_json(arquivo: Path, dados: dict):
    arquivo.with_suffix(".json").write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
