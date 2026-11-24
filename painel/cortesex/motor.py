"""O motor: vigia os canais e conduz cada corte por um pipeline persistente até a publicação.

Pipeline (cada estado fica no banco; ao reabrir, retoma do último checkpoint):
    detectado → capturando → capturado → transcrevendo → transcrito → montando → montado
              → gerando_textos → aguardando_revisao | aprovado          (falhou / descartado em qualquer ponto)
Publicação: estado independente por rede em `postagens` (enviando → processando → postado | erro | incerto).
"""

import json
import logging
import shutil
import tempfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

import requests

from cortesex import MAX_CANAIS, editorial, ia, legendas, midia, seguranca, util
from cortesex import db as D
from cortesex.config import Config, pasta_dados
from cortesex.db import Banco
from cortesex.gravador import Canal, listar_segmentos
from cortesex.plataformas import (COTA, nunca_conectou, EXIGEM_USUARIO, MIDIA, PERMANENTE, PROCESSAMENTO, TEMPORARIO,
                                  ErroPostagem, facebook, telegram, tiktok, youtube, zernio)
from cortesex.render import ErroEtapa, Renderizador, capturar, meta_json

log = logging.getLogger("cortesex")
PLATAFORMAS = {"youtube": youtube, "tiktok": tiktok, "facebook": facebook}
NOMES = {"youtube": "YouTube", "tiktok": "TikTok", "facebook": "Facebook"}
PAUSA_USUARIO = 10 * 365 * 86400       # “até o usuário resolver”


class LogNoBanco(logging.Handler):
    def __init__(self, banco):
        super().__init__(logging.INFO)
        self.banco = banco
        self.addFilter(seguranca.RedigirLog())

    def emit(self, rec):
        try:
            self.banco.evento(rec.levelname.lower(), rec.getMessage())
        except Exception:  # noqa: BLE001
            pass


def via_zernio(config: Config, plataforma: str) -> bool:
    return plataforma in zernio.REDES and config.get(plataforma, "via") == "zernio"


def modulo(config: Config, plataforma: str):
    """Destino da postagem: a integração direta ou, se escolhido, o Zernio."""
    return zernio.Rede(plataforma, config) if via_zernio(config, plataforma) else PLATAFORMAS[plataforma]


def conectado(config: Config, plataforma: str) -> bool:
    if via_zernio(config, plataforma):
        z = config.get("zernio")
        return bool(z.get("api_key") and (z.get("contas") or {}).get(plataforma))
    c = config.get(plataforma)
    if plataforma in ("youtube", "tiktok"):
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


def autorizado_para(canal_cfg: dict | None, plataforma: str, hoje: date | None = None) -> tuple[bool, str]:
    """A autorização registrada pelo usuário permite publicar esse canal nessa rede?"""
    if not canal_cfg:
        return False, "canal não está cadastrado"
    a = canal_cfg.get("autorizacao") or {}
    if a.get("status") != "autorizado":
        return False, f"autorização do canal: {a.get('status', 'nao_registrada')}"
    if not a.get("publicar"):
        return False, "a autorização não inclui publicar"
    if plataforma not in (a.get("redes") or []):
        return False, f"a autorização não inclui {NOMES.get(plataforma, plataforma)}"
    if a.get("validade"):
        try:
            if (hoje or date.today()) > date.fromisoformat(a["validade"]):
                return False, "a autorização venceu"
        except ValueError:
            return False, "validade da autorização inválida"
    return True, ""


def arquivos(corte: dict) -> dict:
    base = Path(corte["arquivo"])
    return {"video": base, "fonte": base.with_name(base.stem + "_fonte.mp4"),
            "palavras": base.with_name(base.stem + "_palavras.json"),
            "legendas": base.with_name(base.stem + "_legendas.json"),
            "original": base.with_name(base.stem + "_original.mp4"), "meta": base.with_suffix(".json")}


def _ler_json(p: Path, padrao):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def _gravar_json(p: Path, dados):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


class Motor:
    def __init__(self, config: Config | None = None, pasta: Path | None = None):
        self.pasta = Path(pasta or pasta_dados())
        self.config = config or Config()
        self.banco = Banco(self.pasta / "cortesex.db", self.pasta / "backups")
        for m in self.banco.migracoes + self.config.migracoes:
            log.info("migração: %s", m)
        retomados = self.banco.recuperar_interrompidos()
        incertos = self.banco.limpar_enviando()
        if not any(isinstance(h, LogNoBanco) for h in log.handlers):
            log.addHandler(LogNoBanco(self.banco))
        if retomados:
            log.info("%s corte(s) retomados do ponto onde pararam", retomados)
        if incertos:
            log.warning("%s envio(s) interrompidos: confira na aba Fila se foram publicados", incertos)
        self.render = Renderizador(self.config)
        self.canais: dict[str, Canal] = {}
        self.parar = threading.Event()
        self.batimentos: dict[str, float] = {}
        self.tentativas_ia: dict[int, int] = {}
        self.disco_baixo = False
        self.ia_status = {"ok": None, "quando": 0.0, "erro": ""}
        self.telegram_rev = None   # cortesex.telegram_revisao.RevisaoTelegram (ligado pelo app)
        self._lock_post = threading.Lock()
        self._lock_pegar = threading.Lock()
        self.workers = max(1, min(int(self.config.get("processamento", "workers_montagem") or 1), 4))
        # AUD-M2: pipeline e editor dividem o mesmo limite de montagens simultâneas
        self._sem_montagem = threading.BoundedSemaphore(self.workers)
        self._lock_reserva = threading.Lock()

    # ----- ciclo de vida
    def iniciar(self):
        self.sincronizar_canais()
        workers = self.workers
        alvos = [(f"pipeline-{i + 1}", self._loop_pipeline) for i in range(workers)] + [
            ("ia", self._loop_ia),
            ("agendador", self._loop_agendador), ("verificador", self._loop_verificar),
            ("limpeza", self._loop_limpeza), ("textos", self._loop_textos), ("analytics", self._loop_analytics)]
        for nome, alvo in alvos:
            threading.Thread(target=alvo, args=(nome,), name=nome, daemon=True).start()
        log.info("CortesEX-PRESS iniciado (%s worker(s) de montagem)", workers)

    def encerrar(self):
        self.parar.set()
        for c in self.canais.values():
            c.encerrar()

    def _bater(self, nome):
        self.batimentos[nome] = time.time()

    def sincronizar_canais(self):
        """Liga os canais novos e desliga os removidos, sem mexer nos outros."""
        desejados = {}
        for c in self.config.get("canais")[:MAX_CANAIS]:
            if c.get("nome") and c.get("ativo", True) and (c.get("autorizacao") or {}).get("gravar", True):
                desejados[c["nome"].lower().strip().lstrip("@")] = c
        for nome in list(self.canais):
            if nome not in desejados:
                self.canais.pop(nome).encerrar()
                log.info("[%s] removido da vigilância", nome)
        for nome, cfg in desejados.items():
            if nome not in self.canais:
                canal = Canal(cfg, self.config, self.pasta, self.agendar_corte, self.avisar,
                              pode_gravar=lambda: not self.disco_baixo)
                self.canais[nome] = canal
                canal.iniciar()
                log.info("[%s] vigiando", nome)
            elif self.canais[nome].plataforma != cfg.get("plataforma", "twitch"):
                self.canais.pop(nome).encerrar()          # trocou Twitch <-> Kick: recomeça a vigilância
                canal = Canal(cfg, self.config, self.pasta, self.agendar_corte, self.avisar,
                              pode_gravar=lambda: not self.disco_baixo)
                self.canais[nome] = canal
                canal.iniciar()
                log.info("[%s] vigiando (%s)", nome, canal.plataforma)
            else:
                self.canais[nome].layout = cfg.get("layout", "blur")
                self.canais[nome].cam = cfg.get("cam")

    def estado(self):
        ag = self.config.get("agendador")
        agora = time.time()
        plats = {}
        for p in PLATAFORMAS:
            pausa = self.banco.pausa(p)
            plats[p] = {"ligado": bool(ag["plataformas"].get(p)), "conectado": conectado(self.config, p),
                        "pausa": pausa["motivo"] if pausa and pausa["ate"] > agora else "",
                        "pausa_ate": pausa["ate"] if pausa and pausa["ate"] > agora else 0,
                        "pausa_tipo": pausa["tipo"] if pausa and pausa["ate"] > agora else "",
                        "ultima": self.banco.ultima_postagem(p)}
        estados = self.banco.contagem_estados()
        andamento = sum(n for e, n in estados.items() if e in (D.DETECTADO, D.CAPTURANDO, D.CAPTURADO, D.TRANSCREVENDO,
                                                                D.TRANSCRITO, D.MONTANDO, D.MONTADO, D.GERANDO_TEXTOS))
        return {"canais": [c.estado() for c in self.canais.values()], "plataformas": plats,
                "fila_render": andamento, "estados": estados, "agendador": ag["ativo"], "modo": ag.get("modo"),
                "gemini": ia.configurada(self.config), "disco_baixo": self.disco_baixo,
                "incertos": len(self.banco.postagens_status("incerto"))}

    # ----- detecção → candidato persistente
    def agendar_corte(self, job):
        canal = job["canal"]
        C, D_ = self.config.get("corte"), self.config.get("deteccao")
        if C.get("adaptativo", True):
            antes, depois = float(C.get("contexto_antes_max", 60)), float(C.get("contexto_depois_max", 30))
        else:
            antes, depois = float(D_["antes_segundos"]), float(D_["depois_segundos"])
        momento = job["momento"]
        aberto = self.banco.candidato_aberto(canal.nome, momento - float(D_.get("agrupar_segundos", 30)))
        if job.get("somente_agrupar") and not aberto:
            return None       # pico dentro do intervalo mínimo e sem corte aberto: ignorado (como antes)
        if aberto:
            self.banco.agrupar(aberto["id"], momento + depois, job["score"], job["motivo"], job["chat"])
            log.info("[%s] pico agrupado ao corte #%s (mesmo evento)", canal.nome, aberto["id"])
            return aberto["id"]
        arquivo = canal.dir_cortes / f"{canal.nome_corte(momento)}.mp4"
        cid = self.banco.novo_candidato(canal.nome, arquivo, momento, job["score"], job["motivo"], job["chat"],
                                        momento - antes, momento + depois, canal.layout)
        return cid

    def clipar_agora(self, nome):
        canal = self.canais.get(nome)
        if not canal:
            raise KeyError(nome)
        return canal.disparar("manual (painel)", score=0, manual=True) is not None

    # ----- pipeline
    ETAPAS_MIDIA = (D.DETECTADO, D.CAPTURADO, D.TRANSCRITO)
    ETAPAS_IA = (D.MONTADO,)

    def _loop_ia(self, nome):
        """AUD-M4: a IA (rede, até ~2,5 min no pior caso) roda fora dos workers de mídia."""
        while not self.parar.is_set():
            self._bater(nome)
            try:
                feito = self.processar_proximo(etapas=self.ETAPAS_IA)
            except Exception:  # noqa: BLE001
                log.exception("erro na etapa da IA")
                feito = False
            if not feito:
                self.parar.wait(1)

    def _loop_pipeline(self, nome):
        while not self.parar.is_set():
            self._bater(nome)
            try:
                feito = self.processar_proximo(etapas=self.ETAPAS_MIDIA)
            except Exception:  # noqa: BLE001
                log.exception("erro no pipeline")
                feito = False
            if not feito:
                self.parar.wait(1)

    def _pronto_para_tentar(self, c, agora):
        t = int(c.get("etapa_tentativas") or 0)
        return t == 0 or agora >= float(c.get("atualizado") or 0) + util.backoff(t, base=20, maximo=600, jitter=0)

    def processar_proximo(self, agora=None, etapas=None) -> bool:
        agora = agora or time.time()
        todas = [(D.DETECTADO, D.CAPTURANDO, self._etapa_capturar),
                 (D.CAPTURADO, D.TRANSCREVENDO, self._etapa_transcrever),
                 (D.TRANSCRITO, D.MONTANDO, self._etapa_montar),
                 (D.MONTADO, D.GERANDO_TEXTOS, self._etapa_textos)]
        etapas = [e for e in todas if etapas is None or e[0] in etapas]
        with self._lock_pegar:
            escolhido = None
            for de, para, func in etapas:
                for c in self.banco.pendentes([de], limite=30):
                    if de == D.DETECTADO and c["janela_fim"] + 8 > agora:
                        continue
                    if not self._pronto_para_tentar(c, agora):
                        continue
                    if self.banco.reivindicar(c["id"], de, para):
                        escolhido = (self.banco.corte(c["id"]), de, func)
                        break
                if escolhido:
                    break
        if not escolhido:
            return False
        corte, de, func = escolhido
        maximo = int(self.config.get("processamento", "tentativas_etapa") or 3)
        try:
            func(corte)
        except Exception as e:  # noqa: BLE001
            msg = seguranca.redigir(str(e) or type(e).__name__)
            definitivo = isinstance(e, ErroEtapa) and e.definitivo
            if definitivo or int(corte.get("etapa_tentativas") or 0) >= maximo:
                self.banco.mudar_estado(corte["id"], D.FALHOU, erro=msg,
                                        proxima="Reprocessar (botão no corte)" if not definitivo else "")
                log.warning("[%s] corte #%s falhou na etapa %s: %s", corte["canal"], corte["id"], de, msg)
            else:
                self.banco.mudar_estado(corte["id"], de, erro=msg, zerar_tentativas=False,
                                        proxima=f"nova tentativa automática ({corte.get('etapa_tentativas')}/{maximo})")
                log.info("[%s] corte #%s: etapa falhou, vai tentar de novo: %s", corte["canal"], corte["id"], msg)
        return True

    def _etapa_capturar(self, c):
        if self.disco_baixo:
            raise ErroEtapa("pouco espaço em disco")
        arq = arquivos(c)
        segs = listar_segmentos(self.pasta / "buffer" / c["canal"])
        fonte_ini = capturar(segs, c["janela_ini"], c["janela_fim"], arq["fonte"],
                             timeout=int(self.config.get("processamento", "timeout_ffmpeg") or 900))
        self.banco.mudar_estado(c["id"], D.CAPTURADO, "fonte capturada", fonte=str(arq["fonte"]), fonte_ini=fonte_ini)

    def _etapa_transcrever(self, c):
        arq = arquivos(c)
        if not arq["fonte"].exists():
            raise ErroEtapa("a fonte do corte sumiu", definitivo=True)
        G = self.config.get("gravacao")
        erro_transcricao = ""
        if G.get("legendas") or self.config.get("corte", "adaptativo"):
            try:
                palavras, perfil = self.render.transcrever(arq["fonte"], G.get("modelo_whisper", "small"))
            except Exception as e:  # noqa: BLE001
                # AUD-H2: sem Whisper o corte segue sem legenda e com janela fixa (como na 1.x), marcado
                erro_transcricao = seguranca.redigir(str(e) or type(e).__name__)
                log.warning("[%s] corte #%s sem transcrição: %s", c["canal"], c["id"], erro_transcricao)
                palavras, perfil = [], midia.perfil_audio(midia.ler_audio(arq["fonte"]))
        else:
            palavras, perfil = [], midia.perfil_audio(midia.ler_audio(arq["fonte"]))
        _gravar_json(arq["palavras"], {"palavras": palavras, "perfil": perfil, "erro_transcricao": erro_transcricao})
        detalhe = f"{len(palavras)} palavras transcritas" if not erro_transcricao else \
            f"sem transcrição: {erro_transcricao}"
        self.banco.mudar_estado(c["id"], D.TRANSCRITO, detalhe)

    def _limites(self, c, palavras, perfil):
        C, Dt = self.config.get("corte"), self.config.get("deteccao")
        t_pico = c["momento"] - c["fonte_ini"]
        dur_fonte = len(perfil.get("db") or []) * perfil.get("passo", 0.5) or (c["janela_fim"] - c["fonte_ini"])
        if C.get("adaptativo", True):
            lim = editorial.limites_adaptativos(palavras, t_pico, dur_fonte, {
                "duracao_min": C.get("duracao_min", 20), "duracao_max": C.get("duracao_max", 90),
                "antes_padrao": Dt["antes_segundos"], "depois_padrao": Dt["depois_segundos"]})
        else:
            lim = {"ini": max(0.0, t_pico - Dt["antes_segundos"]), "fim": min(dur_fonte, t_pico + Dt["depois_segundos"]),
                   "inicio_alinhado": False, "fim_alinhado": False, "metodo": "fixo"}
        return lim, t_pico

    def _etapa_montar(self, c, limites=None, segmentos=None, estado_final=D.MONTADO):
        with self._sem_montagem:
            return self._montar(c, limites, segmentos, estado_final)

    def _montar(self, c, limites, segmentos, estado_final):
        arq = arquivos(c)
        if not arq["fonte"].exists():
            raise ErroEtapa("a fonte do corte sumiu", definitivo=True)
        dados = _ler_json(arq["palavras"], {"palavras": [], "perfil": {}})
        palavras = [tuple(p) for p in dados.get("palavras", [])]
        perfil = dados.get("perfil") or {}
        lim, t_pico = self._limites(c, palavras, perfil)
        if limites:
            lim.update(limites)
            lim["metodo"] = "manual"
        ini, fim = lim["ini"], lim["fim"]
        G, L = self.config.get("gravacao"), self.config.get("legendas")
        no_corte = legendas.recortar(palavras, ini, fim)
        if segmentos is None:
            segmentos = legendas.segmentar(no_corte, L.get("estilo", "classico"), L.get("max_caracteres")) \
                if G.get("legendas") else []
        _gravar_json(arq["legendas"], {"segmentos": segmentos, "estilo": L.get("estilo", "classico")})
        canal_cfg = self.config.canal(c["canal"]) or {}
        info = self.render.montar(arq["fonte"], ini, fim, arq["video"], layout=canal_cfg.get("layout", c.get("layout")),
                                  cam=canal_cfg.get("cam"), segmentos=segmentos, cfg_legendas=L,
                                  fps=G.get("fps_saida", 30),
                                  timeout=int(self.config.get("processamento", "timeout_ffmpeg") or 900))
        sinal = editorial.sinais(palavras, perfil, ini, fim, t_pico)
        flags = editorial.avisos(sinal, lim, len(no_corte))
        if dados.get("erro_transcricao"):
            flags.append("sem_transcricao")
        fala = " ".join(p[2] for p in no_corte)
        campos = {"corte_ini": ini, "corte_fim": fim, "duracao": round(fim - ini, 1), "fala": fala,
                  "validacao": {**info, "limites": lim, "sinais": sinal}, "flags": flags,
                  "score_tecnico": editorial.score_tecnico(info, sinal),
                  "versao_render": int(c.get("versao_render") or 0) + 1}
        campos.update(self._pontuacao(c, sinal, lim, c.get("analise") or {}))
        self.banco.mudar_estado(c["id"], estado_final, f"vídeo montado ({fim - ini:.0f} s, {lim['metodo']})", **campos)
        log.info("[%s] corte #%s montado: %.0f s (%s)", c["canal"], c["id"], fim - ini, lim["metodo"])

    def _pontuacao(self, c, sinal, lim, analise):
        crit = editorial.criterios(c.get("score"), sinal, lim, analise, manual=c.get("score", 0) == 0)
        pesos = self.config.get("pontuacao", "pesos") or editorial.PESOS_PADRAO
        p = editorial.pontuar(crit, pesos)
        return {"criterios": {k: (round(v, 3) if v is not None else None) for k, v in crit.items()},
                "score_editorial": p["score"], "confianca": p["confianca"]}

    def _etapa_textos(self, c):
        detalhe = "sem IA configurada (Gemini/OpenRouter): textos ficam para o usuário"
        if c.get("titulo"):
            detalhe = "textos já existentes mantidos"       # retomada após queda no editor: não sobrescreve
        elif ia.configurada(self.config):
            try:
                r = ia.gerar(self.config, c["canal"], c.get("fala", ""), c.get("chat", []))
                self._aplicar_ia(c, r)
                detalhe = f"textos da IA ({r['provedor']}): {r['titulo']}"
                self.ia_status = {"ok": True, "quando": time.time(), "erro": ""}
                log.info("[%s] textos da IA: %s", c["canal"], r["titulo"])
            except Exception as e:  # noqa: BLE001
                msg = seguranca.redigir(str(e))
                self.ia_status = {"ok": False, "quando": time.time(), "erro": msg}
                detalhe = f"a IA não gerou os textos (vai tentar de novo): {msg}"
                log.warning("[%s] a IA não gerou os textos: %s", c["canal"], msg)
        c = self.banco.corte(c["id"])
        destino = D.REVISAO
        if self.config.get("agendador", "modo") == "automatico" and self._passa_filtro_automatico(c):
            destino = D.APROVADO
        self.banco.mudar_estado(c["id"], destino, detalhe)
        if destino == D.REVISAO and self.telegram_rev:
            self.telegram_rev.notificar(c["id"])
        meta_json(Path(c["arquivo"]), {"id": c["id"], "motivo": c["motivo"], "score": c["score"],
                                       "score_editorial": c.get("score_editorial"), "chat": c["chat"],
                                       "fala": c.get("fala", ""), "titulo": c["titulo"], "legenda": c["legenda"],
                                       "hashtags": c["hashtags"], "categoria": c.get("categoria")})

    def _aplicar_ia(self, c, r):
        analise = r.get("analise") or {}
        self.banco.atualizar_textos(c["id"], r["titulo"], r["legenda"], r["hashtags"], r.get("textos_plataforma") or {})
        flags = list(dict.fromkeys((c.get("flags") or []) + [f"ia:{x}" for x in analise.get("riscos", [])]))
        sinal = (c.get("validacao") or {}).get("sinais") or {}
        lim = (c.get("validacao") or {}).get("limites") or {}
        campos = {"analise": analise, "categoria": analise.get("categoria", "outros"), "flags": flags}
        campos.update(self._pontuacao(c, sinal, lim, analise))
        self.banco.atualizar(c["id"], **campos)

    def _passa_filtro_automatico(self, c) -> bool:
        ag = self.config.get("agendador")
        if c.get("score", 0) < float(ag.get("score_minimo", 0)):
            return False
        minimo = float(ag.get("score_editorial_minimo") or 0)
        if minimo and (c.get("score_editorial") is None or c["score_editorial"] < minimo):
            return False
        cobertura_min = float(ag.get("cobertura_minima", 0.5) or 0)
        if minimo and float(c.get("confianca") or 0) < cobertura_min:
            return False      # nota alta com pouca informação avaliada não passa sozinha
        if ag.get("exigir_textos_ia", True) and not c.get("titulo"):
            return False
        return self._validado(c)

    def _validado(self, c) -> bool:
        """Cortes da 1.x não têm validação gravada: valida o arquivo uma vez e guarda o resultado."""
        v = c.get("validacao") or {}
        if v:
            return bool(v.get("ok"))
        try:
            info = midia.validar(c["arquivo"])
        except midia.MidiaInvalida as e:
            info = {"ok": False, "erro": str(e)}
        except Exception:  # noqa: BLE001
            return False
        self.banco.atualizar(c["id"], validacao=info)
        c["validacao"] = info
        return bool(info.get("ok"))

    def regerar_textos(self, corte_id):
        c = self.banco.corte(corte_id)
        r = ia.gerar(self.config, c["canal"], c.get("fala", ""), c.get("chat", []))
        self._aplicar_ia(c, r)
        self.ia_status = {"ok": True, "quando": time.time(), "erro": ""}
        return {"titulo": r["titulo"], "legenda": r["legenda"], "hashtags": r["hashtags"]}

    def reprocessar(self, corte_id):
        """Corte que falhou volta para o último checkpoint que ainda tem material."""
        c = self.banco.corte(corte_id)
        arq = arquivos(c)
        if arq["palavras"].exists() and arq["fonte"].exists():
            volta = D.TRANSCRITO
        elif arq["fonte"].exists():
            volta = D.CAPTURADO
        else:
            volta = D.DETECTADO
        self.banco.mudar_estado(corte_id, volta, "reprocessamento pedido pelo usuário", descartado=0)
        return volta

    def remontar(self, corte_id, segmentos=None, ini=None, fim=None):
        """Editor: monta de novo a partir da fonte, com legendas/limites editados. Guarda o vídeo original.

        AUD-M2: só para cortes prontos; o corte é reservado (estado 'montando') durante a montagem, então
        o pipeline não o pega no meio, e a montagem divide o limite de workers com o pipeline.
        AUD-L1: respeita a permissão "editar" registrada para o canal.
        """
        c = self.banco.corte(corte_id)
        if not c:
            raise KeyError(corte_id)
        if c["estado"] not in D.PRONTOS:
            raise ValueError(f"o corte está '{c['estado']}': só cortes prontos podem ser editados")
        aut = (self.config.canal(c["canal"]) or {}).get("autorizacao") or {}
        if aut.get("editar") is False:
            raise ValueError("a autorização deste canal não permite editar os cortes")
        arq = arquivos(c)
        if not arq["fonte"].exists():
            raise ErroEtapa("a fonte deste corte já foi apagada (retenção); não dá para montar de novo")
        limites = None
        if ini is not None and fim is not None:
            dmax = float(self.config.get("corte", "duracao_max") or 90)
            ini, fim = max(0.0, float(ini)), float(fim)
            if not 3 <= fim - ini <= max(dmax, 180):
                raise ValueError("duração do corte fora dos limites")
            limites = {"ini": ini, "fim": fim, "inicio_alinhado": True, "fim_alinhado": True}
        if segmentos is not None:
            dur = (fim if fim is not None else c["corte_fim"]) - (ini if ini is not None else c["corte_ini"])
            segmentos = legendas.validar_segmentos(segmentos, dur)
        estado_antes = c["estado"]
        if not self.banco.reivindicar(corte_id, estado_antes, D.MONTANDO):
            raise ValueError("o corte mudou de estado agora há pouco; tente de novo")
        try:
            if arq["video"].exists() and not arq["original"].exists():
                shutil.copy2(arq["video"], arq["original"])
            self._etapa_montar(self.banco.corte(corte_id), limites=limites, segmentos=segmentos,
                               estado_final=estado_antes)
        except Exception:
            self.banco.mudar_estado(corte_id, estado_antes, "edição falhou; vídeo anterior mantido",
                                    zerar_tentativas=False)
            raise
        self.banco.historico(corte_id, estado_antes, "remontado no editor")
        return self.banco.corte(corte_id)

    def _apagar_arquivos(self, c) -> bool:
        """Apaga vídeo, fonte, legendas e cópias do corte. Não mexe em corte com envio em andamento."""
        for rede in PLATAFORMAS:
            p = self.banco.ja_postado(c["id"], rede)
            if p and p["status"] in ("enviando", "processando"):
                return False
        arq = arquivos(c)
        base = Path(c["arquivo"])
        extras = [base.with_name(base.stem + "_reel60.mp4")]
        for p in (arq["video"], arq["meta"], arq["original"], arq["legendas"], arq["fonte"], arq["palavras"], *extras):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        self.banco.atualizar(c["id"], arquivo_removido=1)
        return True

    def descartar(self, corte_id, sim=True, motivo="") -> dict:
        """Descarta (ou recupera) um corte. Com "apagar ao descartar", o vídeo sai do disco na hora."""
        if not sim and (self.banco.corte(corte_id) or {}).get("arquivo_removido"):
            raise ValueError("o vídeo desse corte já foi apagado do disco; não dá para recuperar")
        self.banco.descartar(corte_id, sim, motivo)
        apagado = False
        if sim and self.config.get("armazenamento", "apagar_ao_descartar"):
            apagado = self._apagar_arquivos(self.banco.corte(corte_id))
            if apagado:
                self.banco.historico(corte_id, D.DESCARTADO, "vídeo apagado do disco ao descartar")
        return {"ok": True, "apagado": apagado}

    def editor(self, corte_id) -> dict:
        c = self.banco.corte(corte_id)
        arq = arquivos(c)
        leg = _ler_json(arq["legendas"], {"segmentos": [], "estilo": ""})
        dados = _ler_json(arq["palavras"], {"palavras": [], "perfil": {}})
        dur_fonte = len((dados.get("perfil") or {}).get("db") or []) * 0.5
        return {"corte": c, "segmentos": leg.get("segmentos", []), "tem_fonte": arq["fonte"].exists(),
                "duracao_fonte": dur_fonte, "palavras_fonte": len(dados.get("palavras", [])),
                "tem_original": arq["original"].exists()}

    # ----- postagem
    def postar(self, corte: dict, plataforma: str, origem="agendador", forcar=False, ignorar_autorizacao=False):
        mod = modulo(self.config, plataforma)
        # AUD-H1: conferir "já postado" e registrar o envio é uma operação só (agendador x botão x clique duplo)
        with self._lock_reserva:
            if not forcar:
                ja = self.banco.ja_postado(corte["id"], plataforma)
                if ja:
                    raise ErroPostagem(f"{NOMES[plataforma]}: esse corte já está {ja['status']} nessa rede "
                                       "(use 'postar de novo' para repetir de propósito)", False, PERMANENTE)
            if not ignorar_autorizacao:
                ok, motivo = autorizado_para(self.config.canal(corte["canal"]), plataforma)
                if not ok:
                    raise ErroPostagem(f"{NOMES[plataforma]}: publicação bloqueada — {motivo}", False, PERMANENTE)
            try:
                pid = self.banco.nova_postagem(corte["id"], plataforma, origem, forcado=forcar)
            except D.PostagemDuplicada as e:
                raise ErroPostagem(f"{NOMES[plataforma]}: {e}", False, PERMANENTE) from e
        try:
            if not Path(corte["arquivo"]).exists() or corte.get("arquivo_removido"):
                raise ErroPostagem("O arquivo do corte não existe mais.", False, MIDIA)
            try:
                midia.validar(corte["arquivo"])
            except midia.MidiaInvalida as e:
                raise ErroPostagem(f"vídeo inválido: {e}", False, MIDIA) from e
            cfg = self.config.get(plataforma)
            if plataforma == "tiktok" and not via_zernio(self.config, plataforma):
                novo = tiktok.renovar(cfg)
                if novo:
                    self.config.salvar("tiktok", novo)
                    cfg.update(novo)
            canal_cfg = self.config.canal(corte["canal"]) or {}
            res = mod.postar(cfg, {**corte, "_postagem": pid, "_plataforma_live": canal_cfg.get("plataforma", "twitch")})
            if isinstance(res, str):
                res = {"url": res, "ext_id": "", "status": "postado", "detalhe": ""}
        except Exception as e:  # noqa: BLE001
            msg = seguranca.redigir(str(e) or type(e).__name__)
            tipo = getattr(e, "tipo", TEMPORARIO if not isinstance(e, ErroPostagem) else PERMANENTE)
            # AUD-H4: conexão perdida no meio do envio = pode ter publicado; não volta para a fila sozinho
            incerto = getattr(e, "incerto", False) or (isinstance(e, requests.RequestException) and
                                                       not nunca_conectou(e))
            self.banco.fim_postagem(pid, "incerto" if incerto else "erro",
                                    erro=msg + (" — confira na rede se o vídeo foi publicado" if incerto else ""),
                                    tipo_erro=tipo)
            log.warning("[%s] %s: erro ao postar (%s): %s", corte["canal"], NOMES[plataforma], tipo, msg)
            self._pausar_por_erro(plataforma, e, tipo, msg)
            self.avisar("erro", f"❌ {NOMES[plataforma]}: {msg}\nCorte: {corte.get('titulo') or corte['arquivo']}")
            raise
        self.banco.fim_postagem(pid, res["status"], url=res.get("url", ""), ext_id=res.get("ext_id", ""),
                                detalhe=res.get("detalhe", ""))
        self.banco.retomar(plataforma)
        log.info("[%s] %s no %s: %s %s", corte["canal"], "enviado" if res["status"] == "processando" else "postado",
                 NOMES[plataforma], res.get("url", ""), res.get("detalhe", ""))
        if res["status"] == "postado":
            self._avisar_postado(corte, plataforma, res)
        return res.get("url", "")

    def _avisar_postado(self, corte, plataforma, res):
        extra = f"\n⚠️ {res['detalhe']}" if res.get("detalhe") else ""
        nota = corte.get("score_editorial")
        self.avisar("postagem", f"✅ Postado no {NOMES[plataforma]}\n{corte.get('titulo')}\n"
                                f"Pico do chat: {corte['score']:.1f}x" + (f" · nota editorial {nota:.0f}" if nota else "") +
                                f"\n{res.get('url', '')}{extra}")

    def _pausar_por_erro(self, plataforma, e, tipo, msg):
        if tipo in (MIDIA, PERMANENTE) or not isinstance(e, ErroPostagem):
            if not isinstance(e, ErroPostagem):
                tipo = TEMPORARIO
            else:
                return
        antes = self.banco.pausa(plataforma)
        tent = (antes["tentativas"] if antes else 0) + 1
        agora = time.time()
        ra = getattr(e, "retry_after", None) or 0
        if tipo in EXIGEM_USUARIO:
            ate = agora + PAUSA_USUARIO
            msg = msg + " — resolva e clique em Retomar"
        elif tipo == COTA:
            ate = agora + max(ra, util.backoff(tent, base=3600, maximo=6 * 3600))
        else:  # temporário / processamento
            ate = agora + max(ra, util.backoff(tent, base=120, maximo=3600))
        self.banco.pausar(plataforma, ate, msg, tipo, tent)

    def retomar(self, plataforma):
        self.banco.retomar(plataforma)
        log.info("%s: postagens retomadas pelo usuário", NOMES.get(plataforma, plataforma))

    # ----- agendador
    def _loop_agendador(self, nome):
        while not self.parar.wait(20):
            self._bater(nome)
            try:
                self.rodar_agendador()
            except Exception:  # noqa: BLE001
                log.exception("erro no agendador")

    def candidatos(self, plataforma, ag=None):
        """Cortes elegíveis para o agendador nessa rede, já ordenados, com o motivo de quem ficou de fora."""
        ag = ag or self.config.get("agendador")
        modo = ag.get("modo", "semiautomatico")
        if modo == "manual":
            return [], []
        estados = (D.APROVADO,) if modo == "semiautomatico" else (D.APROVADO, D.REVISAO)
        bons, fora = [], []
        for c in self.banco.candidatos_para(plataforma, float(ag.get("horas_validade", 24)), estados):
            motivo = ""
            ok, m_aut = autorizado_para(self.config.canal(c["canal"]), plataforma)
            if not ok:
                motivo = m_aut
            elif c["estado"] == D.REVISAO and not self._passa_filtro_automatico(c):
                motivo = "abaixo dos filtros do modo automático"
            elif c["estado"] == D.APROVADO and ag.get("exigir_textos_ia", True) and not c.get("titulo"):
                motivo = "sem título"
            elif not Path(c["arquivo"]).exists():
                motivo = "arquivo não encontrado"
            (fora if motivo else bons).append({**c, "_fora": motivo} if motivo else c)
        ordem = ag.get("ordenar_por", "editorial")
        if ordem == "pico":
            bons.sort(key=lambda c: (c["score"], c["criado"]), reverse=True)
        elif ordem == "recente":
            bons.sort(key=lambda c: c["criado"], reverse=True)
        else:
            # AUD-M5: primeiro os cortes com cobertura suficiente, depois a nota; nota parcial não "fura a fila"
            cob = float(ag.get("cobertura_minima", 0.5) or 0)
            bons.sort(key=lambda c: (float(c.get("confianca") or 0) >= cob,
                                     c.get("score_editorial") if c.get("score_editorial") is not None else -1,
                                     c["score"]), reverse=True)
        if ag.get("evitar_mesmo_canal_seguido") and len(bons) > 1:
            ultimo = self.banco.ultimo_canal_postado(plataforma)
            outros = [c for c in bons if c["canal"] != ultimo]
            if outros:
                bons = outros + [c for c in bons if c["canal"] == ultimo]
        return bons, fora

    def rodar_agendador(self, agora: float | None = None):
        ag = self.config.get("agendador")
        agora = agora or time.time()
        if not ag.get("ativo") or ag.get("modo") == "manual" or not dentro_da_janela(ag, datetime.fromtimestamp(agora)):
            return []
        feitos = []
        inicio_periodo = agora - periodo_segundos(ag.get("periodo", "hora"))
        for plat, ligado in ag.get("plataformas", {}).items():
            if not ligado or plat not in PLATAFORMAS or not conectado(self.config, plat):
                continue
            pausa = self.banco.pausa(plat)
            if pausa and pausa["ate"] > agora:
                continue
            if not vez_de_postar(ag, self.banco.ultima_postagem(plat),
                                 self.banco.postagens_desde(inicio_periodo, plat), agora):
                continue
            bons, _ = self.candidatos(plat, ag)
            if not bons:
                continue
            with self._lock_post:
                try:
                    self.postar(bons[0], plat)
                    feitos.append((plat, bons[0]["id"]))
                except Exception:  # noqa: BLE001
                    pass
        return feitos

    # ----- verificação pós-envio
    def _loop_verificar(self, nome):
        while not self.parar.wait(60):
            self._bater(nome)
            try:
                self.verificar_processando()
            except Exception:  # noqa: BLE001
                log.exception("erro ao verificar postagens")

    def verificar_processando(self, agora=None) -> int:
        agora = agora or time.time()
        n = 0
        for p in self.banco.postagens_status("processando"):
            mod = modulo(self.config, p["plataforma"]) if p["plataforma"] in PLATAFORMAS else None
            if not mod or not hasattr(mod, "verificar") or not p.get("ext_id"):
                self.banco.fim_postagem(p["id"], "postado", url=p["url"], detalhe="sem verificação disponível")
                continue
            try:
                cfg = self.config.get(p["plataforma"])
                if p["plataforma"] == "tiktok" and not via_zernio(self.config, "tiktok"):
                    novo = tiktok.renovar(cfg)
                    if novo:
                        self.config.salvar("tiktok", novo)
                        cfg.update(novo)
                r = mod.verificar(cfg, p["ext_id"])
            except Exception as e:  # noqa: BLE001
                if agora - p["quando"] > 86400:
                    self.banco.fim_postagem(p["id"], "incerto", url=p["url"],
                                            erro=f"não consegui confirmar a publicação: {seguranca.redigir(e)}")
                continue
            if r["status"] == "postado":
                self.banco.fim_postagem(p["id"], "postado", url=r.get("url") or p["url"], detalhe=r.get("detalhe", ""))
                corte = self.banco.corte(p["corte_id"])
                if corte:
                    self._avisar_postado(corte, p["plataforma"], r)
                n += 1
            elif r["status"] == "erro":
                self.banco.fim_postagem(p["id"], "erro", url=r.get("url", ""), erro=r.get("erro", "falhou"),
                                        tipo_erro=r.get("tipo", PROCESSAMENTO))
            elif agora - p["quando"] > 86400:
                self.banco.fim_postagem(p["id"], "incerto", url=p["url"], erro="ainda processando após 24 h")
        return n

    # ----- textos que faltaram
    def _loop_textos(self, nome):
        while not self.parar.wait(300):
            self._bater(nome)
            try:
                self.completar_textos()
            except Exception:  # noqa: BLE001
                log.exception("erro ao completar os textos da IA")

    def completar_textos(self, maximo=5) -> int:
        """Tenta de novo a IA nos cortes que ficaram sem título (ela estava lenta ou ocupada)."""
        if not ia.configurada(self.config):
            return 0
        feitos = 0
        horas = float(self.config.get("agendador", "horas_validade") or 24)
        for c in self.banco.sem_textos(horas):
            if feitos >= maximo or self.parar.is_set():
                break
            if self.tentativas_ia.get(c["id"], 0) >= 3:
                continue
            try:
                t = self.regerar_textos(c["id"])
                log.info("[%s] textos da IA (nova tentativa): %s", c["canal"], t["titulo"])
                feitos += 1
            except Exception as e:  # noqa: BLE001
                self.tentativas_ia[c["id"]] = self.tentativas_ia.get(c["id"], 0) + 1
                log.warning("[%s] a IA falhou de novo (%s/3): %s", c["canal"],
                            self.tentativas_ia[c["id"]], seguranca.redigir(e))
        return feitos

    # ----- analytics
    def _loop_analytics(self, nome):
        while not self.parar.wait(600):
            self._bater(nome)
            try:
                self.coletar_metricas()
            except Exception:  # noqa: BLE001
                log.exception("erro ao coletar métricas")

    def coletar_metricas(self) -> int:
        A = self.config.get("analytics")
        if not A.get("ativo") or not conectado(self.config, "youtube"):
            return 0
        posts = self.banco.postagens_para_metricas("youtube", time.time() - float(A.get("dias_acompanhar", 30)) * 86400,
                                                   float(A.get("horas_entre_coletas", 6)) * 3600)
        if not posts:
            return 0
        stats = youtube.estatisticas(self.config.get("youtube"), [p["ext_id"] for p in posts])
        for p in posts:
            s = stats.get(p["ext_id"])
            if s:
                self.banco.nova_metrica(p["id"], s.get("views"), s.get("likes"), s.get("comentarios"))
        return len(stats)

    # ----- avisos e limpeza
    def avisar(self, tipo, texto):
        tg = self.config.get("telegram")
        if not tg.get("bot_token") or not tg.get("chat_id"):
            return
        if tipo == "postagem" and not tg.get("avisar_postagem"):
            return
        if tipo in ("erro", "sistema") and not tg.get("avisar_erro"):
            return
        if tipo not in ("postagem", "erro", "sistema"):
            return
        threading.Thread(target=self._telegram, args=(tg, seguranca.redigir(texto)), daemon=True).start()

    def _telegram(self, tg, texto):
        try:
            telegram.enviar(tg, texto)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: %s", seguranca.redigir(e))

    def _loop_limpeza(self, nome):
        while not self.parar.wait(30):
            self._bater(nome)
            try:
                self.limpar()
            except Exception:  # noqa: BLE001
                log.exception("erro na limpeza")

    def limpar(self, agora=None) -> dict:
        agora = agora or time.time()
        G, A = self.config.get("gravacao"), self.config.get("armazenamento")
        pendentes = {}
        for c in self.banco.pendentes([D.DETECTADO, D.CAPTURANDO], limite=500):
            pendentes[c["canal"]] = min(pendentes.get(c["canal"], c["janela_ini"]), c["janela_ini"])
        for c in list(self.canais.values()):
            c.limpar(G["buffer_minutos"], protegido_desde=pendentes.get(c.nome))
        # espaço em disco
        livre_gb = shutil.disk_usage(self.pasta).free / 1e9
        baixo = livre_gb < float(A.get("minimo_livre_gb", 5))
        if baixo != self.disco_baixo:
            self.disco_baixo = baixo
            if baixo:
                log.warning("pouco espaço em disco (%.1f GB livres): gravação pausada", livre_gb)
                self.avisar("sistema", f"⚠️ CortesEX-PRESS: só {livre_gb:.1f} GB livres. A gravação foi pausada.")
            else:
                log.info("espaço em disco normalizado (%.1f GB livres): gravação liberada", livre_gb)
        removidos = {"fontes": 0, "cortes": 0, "temporarios": 0}
        andamento = (D.DETECTADO, D.CAPTURANDO, D.CAPTURADO, D.TRANSCREVENDO, D.TRANSCRITO, D.MONTANDO, D.MONTADO,
                     D.GERANDO_TEXTOS)
        # fontes (janela larga para re-render)
        limite_fonte = agora - float(A.get("dias_guardar_fontes", 3)) * 86400
        for c in self.banco.para_limpeza(limite_fonte):
            if c["estado"] in andamento:
                continue
            arq = arquivos(c)
            for chave in ("fonte", "palavras"):
                if arq[chave].exists():
                    try:
                        arq[chave].unlink()
                        removidos["fontes"] += chave == "fonte"
                    except OSError:
                        pass
        # descartados: prazo próprio (mais curto)
        dias_desc = A.get("dias_guardar_descartados")
        if dias_desc is not None:
            for c in self.banco.para_limpeza(agora - max(float(dias_desc), 0) * 86400):
                if c["estado"] == D.DESCARTADO and self._apagar_arquivos(c):
                    removidos["cortes"] += 1
        # vídeos dos cortes
        limite = agora - float(G.get("dias_guardar_cortes", 7)) * 86400
        for c in self.banco.para_limpeza(limite):
            if c["estado"] in andamento or c["estado"] == D.APROVADO:
                continue                      # aguardando publicação: protegido
            ocupado = self.banco.ja_postado(c["id"], "youtube") or self.banco.ja_postado(c["id"], "tiktok") or \
                self.banco.ja_postado(c["id"], "facebook")
            if ocupado and ocupado["status"] in ("enviando", "processando"):
                continue
            if ocupado and A.get("manter_publicados"):
                continue
            arq = arquivos(c)
            for p in (arq["video"], arq["meta"], arq["original"], arq["legendas"], arq["fonte"], arq["palavras"]):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            self.banco.atualizar(c["id"], arquivo_removido=1)
            removidos["cortes"] += 1
        # temporários de render que sobraram de uma queda
        for p in Path(tempfile.gettempdir()).glob("cortesex_*"):
            try:
                if p.is_dir() and agora - p.stat().st_mtime > 6 * 3600:
                    shutil.rmtree(p, ignore_errors=True)
                    removidos["temporarios"] += 1
            except OSError:
                pass
        return removidos
