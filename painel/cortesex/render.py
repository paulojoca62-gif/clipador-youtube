"""Transforma o trecho gravado num corte vertical 9:16, com legenda queimada."""

import json
import logging
import shutil
import tempfile
import threading
import time
import wave
from pathlib import Path

from cortesex import util

log = logging.getLogger("cortesex")


def filtro(layout, cam, fps):
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


def _ts_ass(t):
    t = max(t, 0)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def gerar_ass(palavras, por_bloco=3):
    cab = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
           "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
           "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
           "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           "Style: Corte,DejaVu Sans,78,&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,560,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    linhas, bloco = [], []
    for w in palavras:
        bloco.append(w)
        if len(bloco) >= por_bloco or (bloco and w[1] - bloco[0][0] > 1.2):
            linhas.append(bloco)
            bloco = []
    if bloco:
        linhas.append(bloco)
    ev = []
    for b in linhas:
        txt = " ".join(x[2] for x in b).upper().replace("{", "").replace("}", "")
        ev.append(f"Dialogue: 0,{_ts_ass(b[0][0])},{_ts_ass(b[-1][1] + 0.05)},Corte,,0,0,0,,{txt}")
    return cab + "\n".join(ev) + "\n"


class Renderizador:
    def __init__(self, config):
        self.config = config
        self._modelo = None
        self._nome_modelo = None
        self._lock = threading.Lock()

    def renderizar(self, job) -> dict | None:
        """Gera o corte. Devolve {"arquivo", "duracao", "fala"} ou None."""
        canal = job["canal"]
        G = self.config.get("gravacao")
        segs = [(ts, p) for ts, p in canal.segmentos() if job["inicio"] - 10 <= ts <= job["fim"]]
        if not segs:
            log.warning("[%s] sem gravação para esse momento, corte descartado", canal.nome)
            return None
        offset = max(job["inicio"] - segs[0][0], 0)
        dur = job["fim"] - max(job["inicio"], segs[0][0])
        saida = canal.dir_cortes / f"{canal.nome_corte(job['momento'])}.mp4"
        tmp = Path(tempfile.mkdtemp(prefix="cortesex_"))
        try:
            # 1) junta os blocos (cópia, rápido) num arquivo só
            lista = tmp / "blocos.txt"
            lista.write_text("".join(f"file '{p.as_posix()}'\n" for _, p in segs), encoding="utf-8")
            bruto = tmp / "bruto.mp4"
            r = util.rodar([util.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                            "-i", str(lista), "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(bruto)])
            if r.returncode != 0:
                log.error("[%s] falha ao juntar blocos: %s", canal.nome, r.stderr.decode(errors="ignore")[-300:])
                return None
            fala = ""
            vf = filtro(canal.layout, canal.cam, G["fps_saida"])
            if G.get("legendas"):
                palavras = self._legendar(bruto, offset, dur, tmp, G.get("modelo_whisper", "small"))
                if palavras:
                    fala = " ".join(p[2] for p in palavras)
                    (tmp / "legenda.ass").write_text(gerar_ass(palavras), encoding="utf-8")
                    shutil.copytree(util.pasta_fontes(), tmp / "fonts", dirs_exist_ok=True)
                    vf = vf[:-3] + ",subtitles=legenda.ass:fontsdir=fonts[v]"
            t0 = time.time()
            r = util.rodar([util.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                            "-ss", f"{offset:.2f}", "-i", "bruto.mp4", "-t", f"{dur:.2f}",
                            "-filter_complex", vf, "-map", "[v]", "-map", "0:a:0?",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "saida.mp4"], cwd=tmp)
            if r.returncode != 0:
                log.error("[%s] ffmpeg falhou: %s", canal.nome, r.stderr.decode(errors="ignore")[-500:])
                return None
            shutil.move(str(tmp / "saida.mp4"), saida)
            log.info("[%s] corte pronto em %.0f s: %s", canal.nome, time.time() - t0, saida.name)
            return {"arquivo": saida, "duracao": round(dur, 1), "fala": fala}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _legendar(self, entrada, offset, dur, tmp, nome_modelo):
        wav = tmp / "audio.wav"
        r = util.rodar([util.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{offset:.2f}",
                        "-i", str(entrada), "-t", f"{dur:.2f}", "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        if r.returncode != 0:
            return []
        try:
            return self._transcrever(wav, nome_modelo)
        except Exception:  # noqa: BLE001
            log.exception("falha na legenda (o corte sai sem legenda)")
            return []

    def _transcrever(self, wav, nome_modelo):
        import numpy as np
        with self._lock:
            if self._modelo is None or self._nome_modelo != nome_modelo:
                from faster_whisper import WhisperModel
                from cortesex.config import pasta_dados
                log.info("carregando o Whisper '%s' (na primeira vez ele é baixado, ~500 MB)", nome_modelo)
                self._modelo = WhisperModel(nome_modelo, device="cpu", compute_type="int8",
                                            download_root=str(pasta_dados() / "modelos"))
                self._nome_modelo = nome_modelo
            with wave.open(str(wav)) as w:
                audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
            segs, _ = self._modelo.transcribe(audio, language="pt", word_timestamps=True, vad_filter=True)
            return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or [])]


def meta_json(arquivo: Path, dados: dict):
    arquivo.with_suffix(".json").write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
