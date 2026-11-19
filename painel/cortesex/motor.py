"""O motor: vigia os canais, gera os cortes, escreve os textos e posta no ritmo do agendador."""

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

from cortesex import MAX_CANAIS, ia
from cortesex.config import Config, pasta_dados
from cortesex.db import Banco
from cortesex.gravador import Canal
from cortesex.plataformas import ErroPostagem, facebook, telegram, tiktok, youtube
from cortesex.render import Renderizador, meta_json

log = logging.getLogger("cortesex")
PLATAFORMAS = {"youtube": youtube, "tiktok": tiktok, "facebook": facebook}
NOMES = {"youtube": "YouTube", "tiktok": "TikTok", "facebook": "Facebook"}


class LogNoBanco(logging.Handler):
    def __init__(self, banco):
        super().__init__(logging.INFO)
        self.banco = banco

    def emit(self, rec):
        try:
            self.banco.evento(rec.levelname.lower(), rec.getMessage())
        except Exception:  # noqa: BLE001
            pass


def conectado(config: Config, plataforma: str) -> bool:
    c = config.get(plataforma)
    if plataforma == "youtube":
        return bool(c.get("refresh_token"))
    if plataforma == "tiktok":
        return bool(c.get("refresh_token"))
    return bool(c.get("page_token") and c.get("page_id"))


def periodo_segundos(periodo: str) -> int:
    return 86400 if periodo == "dia" else 3600


def dentro_da_janela(ag: dict, agora: datetime) -> bool:
    if agora.weekday() not in ag.get("dias", list(range(7))):
        return False
    ini, fim = int(ag.get("hora_inicio", 0)), int(ag.get("hora_fim", 24))
    h = agora.hour
    return ini <= h < fim if ini <= fim else (h >= ini or h < fim)


def vez_de_postar(ag: dict, ultima: float, feitos_no_periodo: int, agora: float) -> bool:
    """N vídeos por período, espalhados: um a cada (período / N)."""
    n = max(int(ag.get("quantidade") or 1), 1)
    intervalo = periodo_segundos(ag.get("periodo", "hora")) / n
    return feitos_no_periodo < n and agora - ultima >= intervalo


class Motor:
    def __init__(self, config: Config | None = None, pasta: Path | None = None):
        self.pasta = pasta or pasta_dados()
        self.config = config or Config()
        self.banco = Banco(self.pasta / "cortesex.db")
        self.banco.limpar_enviando()
        logging.getLogger("cortesex").addHandler(LogNoBanco(self.banco))
        self.render = Renderizador(self.config)
        self.canais: dict[str, Canal] = {}
        self.fila = queue.PriorityQueue()
        self.pausa = {}          # plataforma -> (até quando, motivo)
        self._lock_post = threading.Lock()
        self.parar = threading.Event()

    # ----- ciclo de vida
    def iniciar(self):
        self.sincronizar_canais()
        for alvo in (self._loop_render, self._loop_agendador, self._loop_limpeza):
            threading.Thread(target=alvo, name=alvo.__name__, daemon=True).start()
        log.info("CortesEX-PRESS iniciado")

    def encerrar(self):
        self.parar.set()
        for c in self.canais.values():
            c.encerrar()

    def sincronizar_canais(self):
        """Liga os canais novos e desliga os removidos, sem mexer nos outros."""
        desejados = {c["nome"].lower().strip().lstrip("@"): c for c in self.config.get("canais")[:MAX_CANAIS]
                     if c.get("nome") and c.get("ativo", True)}
        for nome in list(self.canais):
            if nome not in desejados:
                self.canais.pop(nome).encerrar()
                log.info("[%s] removido da vigilância", nome)
        for nome, cfg in desejados.items():
            if nome not in self.canais:
                canal = Canal(cfg, self.config, self.pasta, self.agendar_corte, self.avisar)
                self.canais[nome] = canal
                canal.iniciar()
                log.info("[%s] vigiando", nome)
            else:
                self.canais[nome].layout = cfg.get("layout", "blur")
                self.canais[nome].cam = cfg.get("cam")

    def estado(self):
        ag = self.config.get("agendador")
        agora = time.time()
        plats = {}
        for p in PLATAFORMAS:
            pausa = self.pausa.get(p)
            plats[p] = {"ligado": bool(ag["plataformas"].get(p)), "conectado": conectado(self.config, p),
                        "pausa": pausa[1] if pausa and pausa[0] > agora else "",
                        "ultima": self.banco.ultima_postagem(p)}
        return {"canais": [c.estado() for c in self.canais.values()], "plataformas": plats,
                "fila_render": self.fila.qsize(), "agendador": ag["ativo"],
                "gemini": bool(self.config.get("gemini", "api_key"))}

    # ----- cortes
    def agendar_corte(self, job):
        self.fila.put((job["fim"] + 8, id(job), job))

    def clipar_agora(self, nome):
        canal = self.canais.get(nome)
        if not canal:
            raise KeyError(nome)
        return canal.disparar("manual (painel)", score=0, manual=True) is not None

    def _loop_render(self):
        while not self.parar.is_set():
            try:
                quando, _, job = self.fila.get(timeout=1)
            except queue.Empty:
                continue
            espera = quando - time.time()
            if espera > 0:
                self.fila.put((quando, id(job), job))
                self.parar.wait(min(espera, 1))
                continue
            try:
                self._processar(job)
            except Exception:  # noqa: BLE001
                log.exception("erro ao gerar o corte")

    def _processar(self, job):
        canal = job["canal"]
        feito = self.render.renderizar(job)
        if not feito:
            return
        textos = {"titulo": "", "legenda": "", "hashtags": []}
        gem = self.config.get("gemini")
        if gem.get("api_key"):
            try:
                textos = ia.gerar_textos(canal.nome, feito["fala"], job["chat"], gem["api_key"],
                                         gem.get("modelo") or "gemini-flash-latest")
                log.info("[%s] textos da IA: %s", canal.nome, textos["titulo"])
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] a IA não gerou os textos: %s", canal.nome, e)
        cid = self.banco.novo_corte(canal.nome, feito["arquivo"], job["momento"], feito["duracao"],
                                    job["score"], job["motivo"], job["chat"], feito["fala"], **textos)
        meta_json(feito["arquivo"], {"id": cid, "motivo": job["motivo"], "score": job["score"],
                                     "chat": job["chat"], "fala": feito["fala"], **textos})

    def regerar_textos(self, corte_id):
        c = self.banco.corte(corte_id)
        gem = self.config.get("gemini")
        t = ia.gerar_textos(c["canal"], c["fala"], c["chat"], gem.get("api_key"), gem.get("modelo"))
        self.banco.atualizar_textos(corte_id, t["titulo"], t["legenda"], t["hashtags"])
        return t

    # ----- postagem
    def postar(self, corte: dict, plataforma: str, origem="agendador") -> str:
        mod = PLATAFORMAS[plataforma]
        pid = self.banco.nova_postagem(corte["id"], plataforma, origem)
        try:
            cfg = self.config.get(plataforma)
            if plataforma == "tiktok":
                novo = tiktok.renovar(cfg)
                if novo:
                    self.config.salvar("tiktok", novo)
                    cfg.update(novo)
            if not Path(corte["arquivo"]).exists():
                raise ErroPostagem("O arquivo do corte não existe mais.", False)
            url = mod.postar(cfg, corte)
        except Exception as e:  # noqa: BLE001
            msg = str(e) or type(e).__name__
            self.banco.fim_postagem(pid, "erro", erro=msg)
            log.warning("[%s] %s: erro ao postar: %s", corte["canal"], NOMES[plataforma], msg)
            if isinstance(e, ErroPostagem) and not e.tentar_de_novo:
                self.pausa[plataforma] = (time.time() + 3600, msg)
            self.avisar("erro", f"❌ {NOMES[plataforma]}: {msg}\nCorte: {corte.get('titulo') or corte['arquivo']}")
            raise
        self.banco.fim_postagem(pid, "postado", url=url)
        self.pausa.pop(plataforma, None)
        log.info("[%s] postado no %s: %s", corte["canal"], NOMES[plataforma], url)
        self.avisar("postagem", f"✅ Postado no {NOMES[plataforma]}\n{corte.get('titulo')}\n"
                                f"Pico do chat: {corte['score']:.1f}x\n{url}")
        return url

    def _loop_agendador(self):
        while not self.parar.wait(20):
            try:
                self.rodar_agendador()
            except Exception:  # noqa: BLE001
                log.exception("erro no agendador")

    def rodar_agendador(self, agora: float | None = None):
        ag = self.config.get("agendador")
        agora = agora or time.time()
        if not ag.get("ativo") or not dentro_da_janela(ag, datetime.fromtimestamp(agora)):
            return []
        feitos = []
        inicio_periodo = agora - periodo_segundos(ag.get("periodo", "hora"))
        for plat, ligado in ag.get("plataformas", {}).items():
            if not ligado or plat not in PLATAFORMAS or not conectado(self.config, plat):
                continue
            pausa = self.pausa.get(plat)
            if pausa and pausa[0] > agora:
                continue
            if not vez_de_postar(ag, self.banco.ultima_postagem(plat),
                                 self.banco.postagens_desde(inicio_periodo, plat), agora):
                continue
            corte = self.banco.proximo_para(plat, float(ag.get("score_minimo", 0)),
                                            float(ag.get("horas_validade", 24)), ag.get("exigir_textos_ia", True))
            if not corte:
                continue
            with self._lock_post:
                try:
                    self.postar(corte, plat)
                    feitos.append((plat, corte["id"]))
                except Exception:  # noqa: BLE001
                    pass
        return feitos

    # ----- avisos e limpeza
    def avisar(self, tipo, texto):
        tg = self.config.get("telegram")
        if not tg.get("bot_token") or not tg.get("chat_id"):
            return
        if (tipo == "postagem" and not tg.get("avisar_postagem")) or (tipo == "erro" and not tg.get("avisar_erro")):
            return
        if tipo not in ("postagem", "erro"):
            return
        threading.Thread(target=self._telegram, args=(tg, texto), daemon=True).start()

    def _telegram(self, tg, texto):
        try:
            telegram.enviar(tg, texto)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: %s", e)

    def _loop_limpeza(self):
        while not self.parar.wait(30):
            G = self.config.get("gravacao")
            for c in list(self.canais.values()):
                c.limpar(G["buffer_minutos"])
            for c in self.banco.cortes_antigos(time.time() - G["dias_guardar_cortes"] * 86400):
                p = Path(c["arquivo"])
                for arq in (p, p.with_suffix(".json")):
                    try:
                        arq.unlink(missing_ok=True)
                    except OSError:
                        pass
