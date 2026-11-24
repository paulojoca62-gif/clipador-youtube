"""Grava a live, lê o chat e detecta os picos. Uma instância de Canal por canal vigiado.

Baseado no script do Clipador: a live fica gravada em blocos de 4 s (buffer
rotativo) e, quando o chat dispara acima do normal ou várias pessoas pedem
"clip", o trecho anterior ao pico vira um corte.
"""

import logging
import os
import random
import re
import subprocess
import threading
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from cortesex import util

log = logging.getLogger("cortesex")

RE_HYPE = re.compile(
    r"(k{4,}|lul|omegalul|kekw|pog|aaaa+|hahaha|rsrs|mds|caralho|que isso|\?{3,}|!{3,})", re.I)
RE_CLIP = re.compile(r"\bclip(a|ar|ou|ei|em)?\b|\bcorta\b|\bcortem\b", re.I)
RE_PRIVMSG = re.compile(r"^(?:@\S+ )?:(\w+)!\S+ PRIVMSG #\w+ :(.*)$")
STOPWORDS = set("a o e de da do que é eu ele ela não nao no na um uma pra pq q mano kkk vc com se os as".split())
FORMATO_BLOCO = "%Y%m%d-%H%M%S"   # nome dos blocos (o Windows não aceita %s no strftime)


def listar_segmentos(pasta: Path):
    """Blocos gravados [(epoch, caminho)] em ordem, lidos do disco (servem mesmo após reiniciar)."""
    out = []
    for p in Path(pasta).glob("*.ts"):
        try:
            out.append((time.mktime(time.strptime(p.stem, FORMATO_BLOCO)), p))
        except ValueError:
            pass
    out.sort()
    return out


def palavras_frequentes(msgs, n=8):
    c = Counter()
    for t in msgs:
        for p in set(re.findall(r"\w{3,}", t.lower())):
            if p not in STOPWORDS and not re.fullmatch(r"(k|ha|rs)+", p):
                c[p] += 1
    return [p for p, _ in c.most_common(n)]


class Canal:
    def __init__(self, cfg_canal: dict, config, pasta: Path, agendar_corte, avisar=None, pode_gravar=None):
        self.nome = cfg_canal["nome"].lower().strip().lstrip("@")
        self.plataforma = cfg_canal.get("plataforma") if cfg_canal.get("plataforma") in ("twitch", "kick") else "twitch"
        self.layout = cfg_canal.get("layout", "blur")
        self.cam = cfg_canal.get("cam")
        self.config = config
        self.agendar_corte = agendar_corte
        self.avisar = avisar or (lambda nivel, texto: None)
        self.pode_gravar = pode_gravar or (lambda: True)
        self.reinicios = 0              # reinícios forçados por travamento
        self.falhas_seguidas = 0
        self.ultimo_erro = ""
        self.ultimo_bloco = 0.0
        self.dir_seg = pasta / "buffer" / self.nome
        self.dir_cortes = pasta / "cortes" / self.nome
        self.dir_seg.mkdir(parents=True, exist_ok=True)
        self.dir_cortes.mkdir(parents=True, exist_ok=True)
        self.msgs = deque()
        self.baseline = None
        self.inicio_chat = None
        self.ultimo_corte = 0.0
        self.cortes_hora = deque()
        self.gravando = False
        self.taxa_atual = 0.0
        self.parar = threading.Event()
        self._ff = None
        self._fluxo = None
        self._threads = []
        self.audio = deque()             # (epoch, dB) a cada 0,5 s, dos blocos já gravados (Kick)
        self._blocos_ouvidos = set()
        self.nivel_audio = None          # dB do último bloco / "normal" do streamer (para o painel)
        self.normal_audio = None

    # ----- ciclo de vida
    def iniciar(self):
        alvos = (self._loop_gravar, self._loop_chat, self._loop_detectar) if self.plataforma == "twitch" else \
            (self._loop_gravar, self._loop_audio)
        for alvo in alvos:
            t = threading.Thread(target=alvo, name=f"{alvo.__name__}-{self.nome}", daemon=True)
            t.start()
            self._threads.append(t)

    def encerrar(self):
        self.parar.set()
        for proc in (self._fluxo, self._ff):
            util.matar_arvore(proc, espera=2)

    # ----- estado
    def segmentos(self):
        return listar_segmentos(self.dir_seg)

    @property
    def ao_vivo(self):
        if not self.gravando:
            return False
        segs = self.segmentos()
        return bool(segs) and time.time() - segs[-1][0] < 25

    def estado(self):
        return {"nome": self.nome, "plataforma": self.plataforma, "detector": "chat" if self.plataforma == "twitch" else "áudio",
                "nivel_audio": self.nivel_audio, "normal_audio": self.normal_audio,
                "ao_vivo": self.ao_vivo, "chat": self.inicio_chat is not None,
                "taxa": round(self.taxa_atual, 1), "normal": round(self.baseline or 0, 1),
                "gravando": self.gravando, "reinicios": self.reinicios, "falhas_seguidas": self.falhas_seguidas,
                "ultimo_bloco": self.ultimo_bloco, "ultimo_erro": self.ultimo_erro}

    # ----- gravação (streamlink -> ffmpeg em blocos de 4 s)
    def _loop_gravar(self):
        """Supervisor da gravação: reinicia com espera crescente (30 s → 5 min) quando falha seguido."""
        avisou = False
        while not self.parar.is_set():
            if not self.pode_gravar():
                self.parar.wait(30)
                continue
            G = self.config.get("gravacao")
            inicio = time.time()
            try:
                self._gravar_uma_vez(G["qualidade"])
            except Exception as e:  # noqa: BLE001
                self.ultimo_erro = str(e)[:200]
                if not avisou:
                    log.info("[%s] offline ou indisponível (%s)", self.nome, str(e)[:120])
                    avisou = True
            finally:
                self.gravando = False
            if time.time() - inicio > 60:
                log.info("[%s] gravação encerrada (live acabou, caiu ou travou)", self.nome)
                avisou = False
                self.falhas_seguidas = 0
                espera = 5
            else:
                self.falhas_seguidas += 1
                espera = util.backoff(self.falhas_seguidas, base=30, maximo=300, jitter=0.1)
            self.parar.wait(espera)

    def _gravar_uma_vez(self, qualidade):
        """streamlink num processo separado (como o script original), ffmpeg corta em blocos."""
        r, w = os.pipe()
        erros = self.dir_seg.parent / f"streamlink_{self.nome}.log"   # arquivo: um pipe cheio travaria
        try:
            with open(erros, "wb") as saida_erro:
                self._fluxo = util.popen_vinculado(
                    util.comando_streamlink() + ["--loglevel", "warning", "--stdout"] + self.url_live() + [qualidade],
                    stdout=w, stderr=saida_erro)
            self._ff = util.popen_vinculado(
                [util.ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                 "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "segment", "-segment_time", "4",
                 "-reset_timestamps", "1", "-strftime", "1", str(self.dir_seg / f"{FORMATO_BLOCO}.ts")],
                stdin=r, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            os.close(r)
            os.close(w)
        inicio = time.time()
        travado_desde = None
        while self._fluxo.poll() is None and not self.parar.is_set():
            segs = self.segmentos()
            if segs:
                self.ultimo_bloco = segs[-1][0]
            if not self.gravando and time.time() - inicio > 8 and segs:
                self.gravando = True
                log.info("[%s] ao vivo: gravando", self.nome)
            # travamento: o streamlink está vivo mas nenhum bloco novo há 45 s
            parado = time.time() - max(self.ultimo_bloco, inicio)
            if parado > 45:
                travado_desde = travado_desde or time.time()
                log.warning("[%s] gravação travada (%.0f s sem blocos novos): reiniciando", self.nome, parado)
                self.reinicios += 1
                break
            if self._ff.poll() is not None:
                log.warning("[%s] o ffmpeg da gravação parou: reiniciando", self.nome)
                self.reinicios += 1
                break
            if not self.pode_gravar():
                log.warning("[%s] gravação interrompida: pouco espaço em disco", self.nome)
                break
            self.parar.wait(1)
        if self._fluxo.poll() is None:
            util.matar_arvore(self._fluxo)
        try:
            erro = erros.read_bytes()[-2000:]
        except OSError:
            erro = b""
        try:
            self._ff.wait(timeout=10)            # fim normal: o ffmpeg fecha o último bloco
        except subprocess.TimeoutExpired:
            util.matar_arvore(self._ff)
        if time.time() - inicio < 60:
            linhas = erro.decode(errors="ignore").strip().splitlines()
            raise RuntimeError(linhas[-1] if linhas else "sem live agora")

    def url_live(self) -> list:
        if self.plataforma == "kick":
            return [f"https://kick.com/{self.nome}"]
        return ["--twitch-low-latency", f"https://www.twitch.tv/{self.nome}"]

    # ----- Kick: pico pelo áudio (a Kick não oferece o chat a programas no PC sem um servidor)
    def _loop_audio(self):
        while not self.parar.wait(2):
            try:
                self.ouvir_blocos()
            except Exception:  # noqa: BLE001
                log.exception("[%s] erro na detecção pelo áudio", self.nome)

    def ouvir_blocos(self, agora=None, ler=None):
        """Mede o volume dos blocos novos (menos o que está sendo gravado) e testa o pico."""
        from cortesex import midia
        ler = ler or midia.ler_audio
        segs = self.segmentos()[:-1]
        disparos = []
        for ts, p in segs:
            if p.name in self._blocos_ouvidos:
                continue
            self._blocos_ouvidos.add(p.name)
            try:
                db = midia.perfil_audio(ler(p))["db"]
            except Exception:  # noqa: BLE001
                continue
            for i, v in enumerate(db):
                self.audio.append((ts + i * 0.5, v))
            if db:
                self.nivel_audio = round(max(db), 1)
            # blocos antigos (de antes de abrir o programa) só ensinam o "normal"; não disparam corte
            recente = (agora or time.time()) - ts < 60
            r = self.tick_audio(ts + len(db) * 0.5, db, ao_vivo=recente and (self.ao_vivo if agora is None else True))
            if r:
                disparos.append(r)
        nomes = {p.name for _, p in segs[-200:]}
        self._blocos_ouvidos &= nomes | {p.name for _, p in self.segmentos()[-1:]}
        return disparos

    def tick_audio(self, fim_bloco, db_bloco, ao_vivo=True):
        """Normal = mediana do volume nos últimos 5 min (precisa de 2 min de áudio).
        Pico = média de 1 s acima do normal + limiar e acima do mínimo absoluto."""
        D = self.config.get("deteccao")
        while self.audio and self.audio[0][0] < fim_bloco - 300:
            self.audio.popleft()
        anteriores = [v for t, v in self.audio if t < fim_bloco - len(db_bloco) * 0.5]
        if len(anteriores) < 240 or len(db_bloco) < 2:       # 240 × 0,5 s = 2 min de aquecimento
            return None
        anteriores.sort()
        normal = anteriores[len(anteriores) // 2]
        self.normal_audio = round(normal, 1)
        janelas = [(db_bloco[i] + db_bloco[i + 1]) / 2 for i in range(len(db_bloco) - 1)]
        pico = max(janelas)
        excesso = pico - normal
        limiar, minimo = float(D.get("audio_limiar_db", 9)), float(D.get("audio_minimo_db", -35))
        if not ao_vivo or excesso < limiar or pico < minimo:
            return None
        quando = fim_bloco - len(db_bloco) * 0.5 + janelas.index(pico) * 0.5
        score = round(10 ** (excesso / 20), 2)               # razão de amplitude: +9 dB ≈ 2,8x
        return self.disparar(f"reação no áudio +{excesso:.0f} dB", score, quando)

    # ----- chat (Twitch por WebSocket na porta 443, passa por firewall e proxy)
    def _loop_chat(self):
        import websocket
        while not self.parar.is_set():
            ws = None
            try:
                opcoes = {"sslopt": {"context": util.contexto_ssl()}, "timeout": 30}
                proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                if proxy:
                    u = urlparse(proxy)
                    opcoes.update(http_proxy_host=u.hostname, http_proxy_port=u.port or 80, proxy_type="http")
                ws = websocket.create_connection("wss://irc-ws.chat.twitch.tv:443", **opcoes)
                ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
                ws.send(f"JOIN #{self.nome}")
                self.inicio_chat = time.time()
                self.falhas_chat = 0
                log.info("[%s] conectado ao chat", self.nome)
                ws.settimeout(400)
                while not self.parar.is_set():
                    bloco = ws.recv()
                    if not bloco:
                        break
                    for linha in str(bloco).split("\r\n"):
                        if linha.startswith("PING"):
                            ws.send("PONG :tmi.twitch.tv")
                            continue
                        m = RE_PRIVMSG.match(linha)
                        if m:
                            self.nova_msg(m.group(2))
            except Exception as e:  # noqa: BLE001
                if not self.parar.is_set():
                    log.warning("[%s] chat desconectado: %s", self.nome, str(e)[:120])
            finally:
                if ws:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass
            self.inicio_chat = None
            self.falhas_chat = getattr(self, "falhas_chat", 0) + 1
            self.parar.wait(util.backoff(self.falhas_chat, base=5, maximo=120, jitter=0.2))

    def nova_msg(self, texto, agora=None):
        agora = agora or time.time()
        peso = 2.0 if RE_HYPE.search(texto) else 1.0
        self.msgs.append((agora, peso, texto))

    # ----- detecção
    def _loop_detectar(self):
        while not self.parar.wait(1):
            try:
                self.tick(time.time(), self.ao_vivo)
            except Exception:  # noqa: BLE001
                log.exception("[%s] erro na detecção", self.nome)

    def tick(self, agora, ao_vivo=True):
        D = self.config.get("deteccao")
        J = D["janela_segundos"]
        while self.msgs and self.msgs[0][0] < agora - 120:
            self.msgs.popleft()
        recentes = [m for m in self.msgs if m[0] >= agora - J]
        taxa = sum(m[1] for m in recentes) / J
        self.taxa_atual = taxa
        if self.inicio_chat is None:
            return None
        aquecido = agora - self.inicio_chat >= D["aquecimento_segundos"]
        if self.baseline is None:
            if agora - self.inicio_chat >= 30:
                janela = [m for m in self.msgs if m[0] >= agora - 30]
                self.baseline = max(sum(m[1] for m in janela) / 30, 0.2)
            return None
        mult = D["multiplicador"]
        pedidos = sum(1 for m in self.msgs if m[0] >= agora - 15 and RE_CLIP.search(m[2]))
        score = taxa / self.baseline
        disparou = None
        if aquecido and ao_vivo:
            if score >= mult and taxa >= D["minimo_msgs_por_seg"]:
                disparou = f"chat {score:.1f}x acima do normal"
            elif pedidos >= D["pedidos_clip"]:
                disparou = f"{pedidos} pedidos de clip"
        alpha = 1 / 120   # o "normal" acompanha devagar o ritmo do chat; picos contam pouco
        self.baseline = max(self.baseline * (1 - alpha) + min(taxa, self.baseline * mult) * alpha, 0.2)
        if disparou:
            return self.disparar(disparou, score, agora)
        return None

    def disparar(self, motivo, score=0.0, agora=None, manual=False):
        agora = agora or time.time()
        D, G = self.config.get("deteccao"), self.config.get("gravacao")
        if not manual:
            if agora - self.ultimo_corte < D["cooldown_segundos"]:
                amostra = [m[2] for m in self.msgs if m[0] >= agora - D["janela_segundos"] - 4]
                self.agendar_corte({"canal": self, "momento": agora, "score": round(score, 2), "motivo": motivo,
                                    "chat": amostra[-20:], "somente_agrupar": True})
                return None
            while self.cortes_hora and self.cortes_hora[0] < agora - 3600:
                self.cortes_hora.popleft()
            if len(self.cortes_hora) >= G["max_cortes_por_hora"]:
                return None
        self.ultimo_corte = agora
        self.cortes_hora.append(agora)
        amostra = [m[2] for m in self.msgs if m[0] >= agora - D["janela_segundos"] - 4]
        job = {
            "canal": self, "inicio": agora - D["antes_segundos"], "fim": agora + D["depois_segundos"],
            "momento": agora, "motivo": motivo, "score": round(score, 2), "chat": amostra[-20:],
            "palavras": palavras_frequentes(amostra),
        }
        log.info("[%s] PICO: %s -> corte agendado", self.nome, motivo)
        self.agendar_corte(job)
        return job

    def nome_corte(self, momento):
        return datetime.fromtimestamp(momento).strftime("%Y%m%d_%H%M%S") + f"_{self.nome}"

    def limpar(self, buffer_minutos, protegido_desde=None, maximo_horas=3):
        limite = time.time() - buffer_minutos * 60
        if protegido_desde:
            # guarda desde o início da janela do candidato pendente mais antigo (com teto de segurança)
            limite = max(min(limite, protegido_desde - 10), time.time() - maximo_horas * 3600)
        for ts, p in self.segmentos():
            if ts < limite:
                try:
                    p.unlink()
                except OSError:
                    pass  # no Windows, o bloco em gravação fica travado: sai na próxima volta
