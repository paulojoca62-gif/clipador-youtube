"""Servidor do painel: a página e a API JSON, só em 127.0.0.1 (nada fica exposto na rede).

Proteções: só atende Host 127.0.0.1/localhost na porta do painel (bloqueia DNS rebinding); ações (POST)
exigem o cabeçalho X-CortesEX e, se vier Origin, que seja o próprio painel; cabeçalhos de segurança em
toda resposta; vídeos só de dentro da pasta de dados; erros sem segredos.
"""

import json
import logging
import re
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cortesex import MAX_CANAIS, NOME, PORTA, VERSAO, analytics, diagnostico, editorial, ia, seguranca
from cortesex.config import STATUS_AUTORIZACAO, normalizar_canal
from cortesex.motor import NOMES, arquivos, via_zernio
from cortesex.plataformas import facebook, telegram, tiktok, youtube, zernio
from cortesex.render import CAM_PRESETS, cam_valida
from cortesex.util import pasta_app

log = logging.getLogger("cortesex")
WEB = pasta_app() / "web"
SECOES = ("gravacao", "deteccao", "agendador", "youtube", "tiktok", "facebook", "gemini", "telegram",
          "corte", "legendas", "pontuacao", "processamento", "armazenamento", "analytics", "atualizacoes", "zernio", "openrouter")


def extrair_codigo(texto: str) -> tuple[str, str]:
    """Aceita o código puro ou o endereço inteiro para onde o login mandou."""
    texto = (texto or "").strip()
    if "code=" in texto:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(texto).query)
        return (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    return texto, ""


def _num(q, k, padrao=0.0):
    try:
        return float((q.get(k) or [padrao])[0])
    except (TypeError, ValueError):
        return padrao


def _txt(q, k):
    v = (q.get(k) or [""])[0]
    return re.sub(r"[^\w\-]", "", v)[:40]


class Painel:
    def __init__(self, motor, porta=PORTA):
        self.motor = motor
        self.porta = porta
        self.redirect = f"http://127.0.0.1:{porta}/"
        self.estados = {}        # state do OAuth -> plataforma
        self.fb_paginas = {}     # id -> página (com token), da última consulta
        self.ao_encerrar = None
        self.atualizador = None  # cortesex.atualizador.Atualizador (só no programa aberto pelo app)
        self.verificadores = {}  # state -> code_verifier (PKCE do TikTok)
        # Token anti-CSRF: aleatório por execução, só vai dentro da página servida com Host válido.
        # Outro site não consegue lê-lo (mesma origem) nem enviar cabeçalho próprio sem CORS.
        self.token = secrets.token_urlsafe(32)

    # ----- OAuth
    def novo_estado(self, plataforma):
        e = f"{plataforma}-{secrets.token_urlsafe(16)}"
        self.estados[e] = plataforma
        return e

    def concluir_login(self, plataforma, codigo, estado=""):
        cfg = self.motor.config
        if estado and estado not in self.estados:
            raise ValueError("Login expirado ou de outra janela. Clique em Conectar de novo.")
        verificador = self.verificadores.pop(estado, "") if estado else (
            next(iter(self.verificadores.values()), "") if len(self.verificadores) == 1 else "")
        if estado and self.estados.pop(estado, None) != plataforma:
            raise ValueError("Login de outra plataforma.")
        if plataforma == "youtube":
            y = cfg.get("youtube")
            refresh = youtube.trocar_codigo(y, codigo, y.get("redirect_uri") or self.redirect)
            cfg.salvar("youtube", {"refresh_token": refresh})
            try:
                cfg.salvar("youtube", {"canal": youtube.nome_canal(cfg.get("youtube"))})
            except Exception:  # noqa: BLE001
                pass
            return "YouTube conectado"
        if plataforma != "tiktok":
            raise ValueError("plataforma desconhecida")
        t = cfg.get("tiktok")
        novo = tiktok.trocar_codigo(t, codigo, t.get("redirect_uri") or self.redirect, verificador)
        cfg.salvar("tiktok", novo)
        try:
            d = tiktok.info(cfg.get("tiktok"))
            cfg.salvar("tiktok", {"usuario": d.get("creator_username", "")})
        except Exception:  # noqa: BLE001
            pass
        return "TikTok conectado"

    # ----- leitura
    def get(self, caminho, q):
        m = self.motor
        if caminho == "/api/estado":
            return {"nome": NOME, "versao": VERSAO, "max_canais": MAX_CANAIS, "redirect": self.redirect,
                    "youtube_login_embutido": bool(youtube.embutido()[0]),
                    **m.estado()}
        if caminho == "/api/config":
            return {**m.config.publico(), "_cam_presets": CAM_PRESETS, "_categorias": editorial.NOMES_CATEGORIAS,
                    "_status_autorizacao": STATUS_AUTORIZACAO}
        if caminho in ("/api/ranking", "/api/biblioteca"):
            return m.banco.biblioteca(limite=int(_num(q, "limite", 200)), horas=_num(q, "horas") or None,
                                      canal=_txt(q, "canal") or None, categoria=_txt(q, "categoria") or None,
                                      estado=_txt(q, "estado") or None,
                                      ordenar=_txt(q, "ordenar") or ("pico" if caminho == "/api/ranking" else "editorial"))
        if mm := re.fullmatch(r"/api/corte/(\d+)", caminho):
            c = m.banco.corte(int(mm.group(1)))
            if not c:
                raise KeyError(caminho)
            c["historico"] = m.banco.historico_de(c["id"])
            c["postagens"] = m.banco.postagens_de(c["id"])
            return c
        if mm := re.fullmatch(r"/api/editor/(\d+)", caminho):
            return m.editor(int(mm.group(1)))
        if caminho == "/api/fila":
            ag = m.config.get("agendador")
            plats = {}
            for p in NOMES:
                bons, fora = m.candidatos(p, ag)
                plats[p] = {"proximos": [_resumo(c) for c in bons[:10]],
                            "bloqueados": [{**_resumo(c), "motivo": c["_fora"]} for c in fora[:20]]}
            return {"modo": ag.get("modo"), "ativo": ag.get("ativo"), "plataformas": plats,
                    "incertos": m.banco.postagens_status("incerto"),
                    "processando": m.banco.postagens_status("processando"),
                    "revisao": [_resumo(c) for c in m.banco.biblioteca(limite=50, estado="aguardando_revisao")],
                    "aprovados": [_resumo(c) for c in m.banco.biblioteca(limite=50, estado="aprovado")],
                    "andamento": [_resumo(c) for c in m.banco.biblioteca(limite=50, estado="andamento", ordenar="recente")],
                    "falhas": [_resumo(c) for c in m.banco.biblioteca(limite=50, estado="falhou", ordenar="recente")]}
        if caminho == "/api/log":
            return m.banco.log()
        if caminho == "/api/eventos":
            return m.banco.eventos(nivel=_txt(q, "nivel") or None)
        if caminho == "/api/analytics":
            return analytics.relatorio(m.banco)
        if caminho == "/api/diagnostico":
            return diagnostico.coletar(m)
        if caminho == "/api/atualizacao":
            return self._estado_atualizacao()
        raise KeyError(caminho)

    def _estado_atualizacao(self):
        if not self.atualizador:
            return {"situacao": "indisponivel", "mensagem": "atualizador desligado nesta execução",
                    "build_atual": None, "build_disponivel": None}
        return {**self.atualizador.estado, "modo": self.atualizador.cfg()["modo"]}

    # ----- ações
    def post(self, caminho, corpo):
        m, cfg = self.motor, self.motor.config
        if caminho == "/api/canais":
            canais = []
            atuais = {c["nome"]: c for c in cfg.get("canais")}
            for c in corpo.get("canais", [])[:MAX_CANAIS]:
                nome = re.sub(r"[^a-z0-9_]", "", str(c.get("nome", "")).lower().rstrip("/").split("/")[-1])[:25]
                if not nome:
                    continue
                aut = dict((atuais.get(nome) or {}).get("autorizacao") or {})
                aut.update({k: v for k, v in (c.get("autorizacao") or {}).items()
                            if k in ("status", "data_registro", "data_autorizacao", "validade", "observacoes",
                                     "gravar", "editar", "publicar", "redes")})
                if aut.get("status") == "autorizado" and not aut.get("data_registro"):
                    from datetime import date
                    aut["data_registro"] = date.today().isoformat()
                aut["observacoes"] = str(aut.get("observacoes", ""))[:500]
                layout = c.get("layout") if c.get("layout") in ("blur", "split") else "blur"
                plataforma = c.get("plataforma") if c.get("plataforma") in ("twitch", "kick") else "twitch"
                canais.append(normalizar_canal({"nome": nome, "plataforma": plataforma, "layout": layout,
                                                "ativo": bool(c.get("ativo", True)), "cam": cam_valida(c.get("cam")),
                                                "autorizacao": aut}))
            cfg.salvar("canais", canais)
            m.sincronizar_canais()
            return {"ok": True, "canais": cfg.get("canais")}
        if mm := re.fullmatch(r"/api/config/(\w+)", caminho):
            secao = mm.group(1)
            if secao not in SECOES or not isinstance(corpo, dict):
                raise KeyError(secao)
            cfg.salvar(secao, corpo)
            return {"ok": True}
        if mm := re.fullmatch(r"/api/desconectar/(\w+)", caminho):
            p = mm.group(1)
            campos = {"youtube": ["refresh_token"], "tiktok": ["refresh_token", "access_token"],
                      "facebook": ["page_token"], "gemini": ["api_key"], "telegram": ["bot_token"],
                      "openrouter": ["api_key"], "zernio": ["api_key"]}[p]
            for k in campos:
                cfg.apagar_segredo(p, k)
            return {"ok": True}
        if mm := re.fullmatch(r"/api/retomar/(\w+)", caminho):
            if mm.group(1) not in NOMES:
                raise KeyError(caminho)
            m.retomar(mm.group(1))
            return {"ok": True, "msg": f"{NOMES[mm.group(1)]} retomado"}
        if mm := re.fullmatch(r"/api/postagem/(\d+)/resolver", caminho):
            p = m.banco.postagem(int(mm.group(1)))
            if not p or p["status"] != "incerto":
                raise KeyError(caminho)
            url = str(corpo.get("url") or "")[:300]
            if url and not url.startswith("https://"):
                raise ValueError("link inválido")
            m.banco.resolver_incerto(p["id"], bool(corpo.get("publicado")), url)
            return {"ok": True}
        if caminho == "/api/encerrar":
            if self.ao_encerrar:
                threading.Timer(0.5, self.ao_encerrar).start()
            return {"ok": True}
        if caminho in ("/api/atualizacao/verificar", "/api/atualizacao/aplicar"):
            if not self.atualizador:
                raise ValueError("o atualizador não está ligado nesta execução")
            if caminho.endswith("verificar"):
                self.atualizador.verificar()
            else:
                self.atualizador.passo(forcar=True)
            return self._estado_atualizacao()
        if caminho == "/api/clipar":
            return {"ok": m.clipar_agora(str(corpo.get("canal", "")))}
        if mm := re.fullmatch(r"/api/corte/(\d+)/(\w+)", caminho):
            return self._acao_corte(int(mm.group(1)), mm.group(2), corpo)
        return self._contas(caminho, corpo)

    def _acao_corte(self, cid, acao, corpo):
        m = self.motor
        corte = m.banco.corte(cid)
        if not corte:
            raise KeyError(cid)
        if acao == "postar":
            res = {}
            for p in corpo.get("plataformas", []):
                if p not in NOMES:
                    continue
                try:
                    url = m.postar(m.banco.corte(cid), p, origem="manual", forcar=bool(corpo.get("forcar")),
                                   ignorar_autorizacao=bool(corpo.get("ignorar_autorizacao")))
                    res[p] = {"ok": True, "url": url}
                except Exception as e:  # noqa: BLE001
                    res[p] = {"ok": False, "erro": seguranca.redigir(e)}
            return res
        if acao == "descartar":
            return m.descartar(cid, bool(corpo.get("sim", True)), str(corpo.get("motivo", ""))[:200])
        if acao in ("aprovar", "desaprovar"):
            if corte["estado"] not in ("aguardando_revisao", "aprovado"):
                raise ValueError("só cortes prontos podem ser aprovados")
            m.banco.aprovar(cid, acao == "aprovar")
            return {"ok": True}
        if acao in ("regerar", "reanalisar"):
            return m.regerar_textos(cid)
        if acao == "reprocessar":
            return {"ok": True, "estado": m.reprocessar(cid)}
        if acao == "remontar":
            c = m.remontar(cid, segmentos=corpo.get("segmentos"), ini=corpo.get("ini"), fim=corpo.get("fim"))
            return {"ok": True, "versao": c.get("versao_render"), "duracao": c.get("duracao")}
        if acao == "textos":
            tags = corpo.get("hashtags", [])
            if isinstance(tags, str):
                tags = [t for t in re.split(r"[\s,]+", tags) if t]
            tags = ["#" + re.sub(r"[^\wÀ-ÿ]", "", t.lstrip("#"))[:40] for t in tags if t.strip("#")][:15]
            m.banco.atualizar_textos(cid, str(corpo.get("titulo", ""))[:100], str(corpo.get("legenda", ""))[:2000], tags)
            return {"ok": True}
        raise KeyError(acao)

    def _contas(self, caminho, corpo):
        cfg = self.motor.config
        if caminho == "/api/youtube/login":
            y = cfg.get("youtube")
            if not youtube.login_pronto(y):
                raise ValueError("Esta versão não tem o login do YouTube embutido: preencha o ID do cliente e a "
                                 "chave secreta em Avançado e salve.")
            y["redirect_uri"] = y.get("redirect_uri") or self.redirect
            return {"url": youtube.link_login(y, y["redirect_uri"], self.novo_estado("youtube"))}
        if caminho == "/api/tiktok/login":
            t = cfg.get("tiktok")
            if not t.get("client_key") or not t.get("client_secret"):
                raise ValueError("Preencha a Client key e a Client secret e salve.")
            estado = self.novo_estado("tiktok")
            self.verificadores = {estado: tiktok.novo_verificador()}
            return {"url": tiktok.link_login(t, t.get("redirect_uri") or self.redirect, estado,
                                             self.verificadores[estado])}
        if mm := re.fullmatch(r"/api/(youtube|tiktok)/codigo", caminho):
            codigo, estado = extrair_codigo(corpo.get("texto", ""))
            if not codigo:
                raise ValueError("Não achei o código nesse endereço.")
            return {"ok": True, "msg": self.concluir_login(mm.group(1), codigo, estado)}
        if caminho == "/api/youtube/testar":
            return {"msg": youtube.testar(cfg.get("youtube"))}
        if caminho == "/api/zernio/contas":
            if corpo.get("api_key"):
                cfg.salvar("zernio", {"api_key": corpo["api_key"]})
            contas = zernio.listar_contas(cfg.get("zernio", "api_key"))
            return {"contas": contas, "msg": f"{len(contas)} conta(s) de TikTok/Facebook no Zernio"}
        if caminho == "/api/zernio/escolher":
            contas = {c["id"]: c for c in zernio.listar_contas(cfg.get("zernio", "api_key"))}
            escolha, nomes = {}, {}
            for rede in zernio.REDES:
                cid = str(corpo.get(rede) or "")
                if cid and (cid not in contas or contas[cid]["plataforma"] != rede):
                    raise ValueError(f"conta inválida para {NOMES[rede]}")
                escolha[rede] = cid
                nomes[rede] = (contas[cid]["usuario"] or contas[cid]["nome"]) if cid else ""
            cfg.salvar("zernio", {"contas": escolha, "nomes": nomes})
            return {"ok": True, "msg": "Contas do Zernio salvas"}
        if caminho in ("/api/tiktok/testar", "/api/facebook/testar") and via_zernio(cfg, caminho.split("/")[2]):
            return {"msg": zernio.Rede(caminho.split("/")[2], cfg).testar()}
        if caminho == "/api/tiktok/testar":
            t = cfg.get("tiktok")
            novo = tiktok.renovar(t)
            if novo:
                cfg.salvar("tiktok", novo)
            return {"msg": tiktok.testar(cfg.get("tiktok"))}
        if caminho == "/api/facebook/paginas":
            f = cfg.get("facebook")
            paginas = facebook.paginas_do_token(corpo.get("app_id") or f["app_id"],
                                                corpo.get("app_secret") or f["app_secret"], corpo["token_usuario"])
            self.fb_paginas = {p["id"]: p for p in paginas}
            return [{"id": p["id"], "nome": p["nome"]} for p in paginas]
        if caminho == "/api/facebook/escolher":
            p = self.fb_paginas[corpo["id"]]
            cfg.salvar("facebook", {"page_id": p["id"], "page_nome": p["nome"], "page_token": p["token"]})
            self.fb_paginas = {}
            return {"ok": True, "msg": f"Página escolhida: {p['nome']}"}
        if caminho == "/api/facebook/testar":
            return {"msg": facebook.testar(cfg.get("facebook"))}
        if caminho == "/api/openrouter/testar":
            o = cfg.get("openrouter")
            modelos = [x for x in str(o.get("modelos") or "").replace("\n", ",").split(",") if x.strip()]
            t = ia.analisar_openrouter("teste", "Meu Deus, que jogada! Ganhei a partida no último segundo!",
                                       ["KKKKKK", "QUE ISSO", "clipa"], o.get("api_key"), modelos)
            return {"msg": f"Funcionando ({t['modelo']}). Exemplo: {t['titulo']}"}
        if caminho == "/api/gemini/testar":
            g = cfg.get("gemini")
            t = ia.gerar_textos("teste", "Meu Deus, que jogada! Ganhei a partida no último segundo!",
                                ["KKKKKK", "QUE ISSO", "clipa"], g.get("api_key"), g.get("modelo"))
            self.motor.ia_status = {"ok": True, "quando": time.time(), "erro": ""}
            return {"msg": f"Funcionando. Exemplo: {t['titulo']}"}
        if caminho == "/api/telegram/descobrir":
            try:
                chat = telegram.descobrir_chat(cfg.get("telegram"))
            except Exception:
                # com a revisão pelo Telegram ligada, quem lê as mensagens é ela (o Telegram só deixa um leitor)
                chat = self.motor.banco.get_meta("telegram_ultimo_chat", "")
                if not chat:
                    raise
            cfg.salvar("telegram", {"chat_id": chat})
            return {"msg": f"Chat encontrado: {chat}", "chat_id": chat}
        if caminho == "/api/telegram/testar":
            telegram.enviar(cfg.get("telegram"), f"✂️ {NOME}: avisos funcionando!")
            return {"msg": "Mensagem enviada. Veja o Telegram."}
        raise KeyError(caminho)

    # ----- servidor HTTP
    def handler(self):
        painel = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "CortesEX"
            sys_version = ""

            def log_message(self, *a):
                pass

            def _cabecalhos(self):
                for k, v in seguranca.CABECALHOS.items():
                    self.send_header(k, v)

            def _json(self, dados, status=200, fechar=False):
                b = json.dumps(dados, ensure_ascii=False, default=str).encode()
                self.send_response(status)
                if fechar:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self._cabecalhos()
                self.end_headers()
                self.wfile.write(b)

            def _arquivo(self, p: Path, tipo):
                b = p.read_bytes()
                if p.name == "index.html":
                    b = b.replace(b"__CORTESEX_TOKEN__", painel.token.encode())
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(b)))
                self._cabecalhos()
                self.end_headers()
                self.wfile.write(b)

            timeout = 30           # conexão parada não prende uma thread para sempre

            def _host_ok(self):
                hosts = self.headers.get_all("Host") or []
                if len(hosts) == 1 and seguranca.host_permitido(hosts[0], painel.porta):
                    return True
                self._json({"erro": "host não permitido"}, 403)
                return False

            def do_GET(self):
                if not self._host_ok():
                    return
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                try:
                    if u.path == "/" and "code" in q:
                        return self._callback(q)
                    if u.path in ("/", "/index.html"):
                        return self._arquivo(WEB / "index.html", "text/html; charset=utf-8")
                    if mm := re.fullmatch(r"/video/(\d+)", u.path):
                        c = painel.motor.banco.corte(int(mm.group(1)))
                        if c:
                            arq = arquivos(c)["original" if (q.get("v") or [""])[0] == "original" else "video"]
                            if arq.exists() and seguranca.dentro_de(arq, painel.motor.pasta):
                                return self._video(arq)
                        return self._json({"erro": "vídeo não encontrado"}, 404)
                    if u.path.startswith("/api/"):
                        return self._json(painel.get(u.path, q))
                    return self._json({"erro": "não encontrado"}, 404)
                except KeyError:
                    return self._json({"erro": "não encontrado"}, 404)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    return        # o navegador pulou o vídeo ou fechou o player: não é erro
                except Exception as e:  # noqa: BLE001
                    log.exception("erro na API")
                    return self._json({"erro": seguranca.redigir(e)}, 500)

            def do_POST(self):
                bruto_tam = self.headers.get("Content-Length")
                if self.headers.get("Transfer-Encoding"):
                    return self._json({"erro": "Transfer-Encoding não suportado"}, 411, fechar=True)
                try:
                    n = int(bruto_tam) if bruto_tam is not None else 0
                except ValueError:
                    n = -1
                if n < 0:
                    return self._json({"erro": "Content-Length inválido"}, 400, fechar=True)
                if n > 2_000_000:
                    return self._json({"erro": "pedido grande demais"}, 413, fechar=True)
                bruto = self.rfile.read(n) if n else b""
                if not self._host_ok():
                    return
                # CSRF: token da página + Origin (se vier) + Sec-Fetch-Site (se vier). Nenhum deles é login.
                token = self.headers.get("X-CortesEX") or ""
                if not secrets.compare_digest(token, painel.token) or \
                        not seguranca.origem_permitida(self.headers.get("Origin"), painel.porta) or \
                        not seguranca.fetch_site_permitido(self.headers.get("Sec-Fetch-Site")):
                    return self._json({"erro": "proibido"}, 403)
                if "json" not in (self.headers.get("Content-Type") or "application/json").lower():
                    return self._json({"erro": "use JSON"}, 415)
                try:
                    corpo = json.loads(bruto or b"{}")
                except (ValueError, UnicodeDecodeError):
                    return self._json({"erro": "JSON inválido"}, 400)
                if not isinstance(corpo, dict):
                    return self._json({"erro": "o corpo precisa ser um objeto JSON"}, 400)
                try:
                    return self._json(painel.post(urllib.parse.urlparse(self.path).path, corpo))
                except KeyError as e:
                    return self._json({"erro": f"não encontrado: {seguranca.redigir(e)}"}, 404)
                except Exception as e:  # noqa: BLE001
                    log.warning("API %s: %s", urllib.parse.urlparse(self.path).path, seguranca.redigir(e))
                    return self._json({"erro": seguranca.redigir(e)}, 400)

            def do_PUT(self):
                return self._json({"erro": "método não permitido"}, 405)

            do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = do_PUT

            def do_HEAD(self):
                if self._host_ok():
                    self._json({"erro": "método não permitido"}, 405)

            def _callback(self, q):
                estado = (q.get("state") or [""])[0]
                plat = painel.estados.get(estado) or estado.split("-")[0]
                try:
                    msg = painel.concluir_login(plat, q["code"][0], estado)
                    ok = True
                except Exception as e:  # noqa: BLE001
                    msg, ok = seguranca.redigir(e), False
                destino = "/#contas?" + urllib.parse.urlencode({"ok" if ok else "erro": msg})
                self.send_response(303)
                self.send_header("Location", destino)
                self.send_header("Content-Length", "0")
                self._cabecalhos()
                self.end_headers()

            def _video(self, p: Path):
                tam = p.stat().st_size
                ini, fim = 0, tam - 1
                m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        ini = min(int(m.group(1)), tam - 1)
                        fim = min(int(m.group(2)) if m.group(2) else tam - 1, tam - 1)
                    else:
                        ini = max(tam - int(m.group(2)), 0)
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {ini}-{fim}/{tam}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(fim - ini + 1))
                self._cabecalhos()
                self.end_headers()
                try:
                    with open(p, "rb") as f:
                        f.seek(ini)
                        falta = fim - ini + 1
                        while falta > 0:
                            b = f.read(min(65536, falta))
                            if not b:
                                break
                            self.wfile.write(b)
                            falta -= len(b)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return H

    def iniciar(self) -> ThreadingHTTPServer:
        srv = ThreadingHTTPServer(("127.0.0.1", self.porta), self.handler())
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True, name="painel").start()
        log.info("painel em %s", self.redirect)
        return srv


def _resumo(c: dict) -> dict:
    return {k: c.get(k) for k in ("id", "canal", "titulo", "score", "score_editorial", "confianca", "categoria",
                                   "estado", "criado", "duracao", "erro", "proxima_acao", "flags")}
