"""Servidor do painel: a página e a API JSON, só em 127.0.0.1 (nada fica exposto na rede)."""

import json
import logging
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cortesex import MAX_CANAIS, NOME, PORTA, VERSAO, ia
from cortesex.plataformas import facebook, telegram, tiktok, youtube
from cortesex.util import pasta_app

log = logging.getLogger("cortesex")
WEB = pasta_app() / "web"


def extrair_codigo(texto: str) -> tuple[str, str]:
    """Aceita o código puro ou o endereço inteiro para onde o login mandou."""
    texto = (texto or "").strip()
    if "code=" in texto:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(texto).query)
        return (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    return texto, ""


class Painel:
    def __init__(self, motor, porta=PORTA):
        self.motor = motor
        self.porta = porta
        self.redirect = f"http://127.0.0.1:{porta}/"
        self.estados = {}       # state do OAuth -> plataforma
        self.fb_paginas = {}    # id -> página (com token), da última consulta
        self.ao_encerrar = None
        self.verificadores = {}  # state -> code_verifier (PKCE do TikTok)

    # ----- OAuth
    def novo_estado(self, plataforma):
        e = f"{plataforma}-{secrets.token_urlsafe(16)}"
        self.estados[e] = plataforma
        return e

    def concluir_login(self, plataforma, codigo, estado=""):
        cfg = self.motor.config
        verificador = self.verificadores.pop(estado, "") if estado else (
            next(iter(self.verificadores.values()), "") if len(self.verificadores) == 1 else "")
        if estado and self.estados.pop(estado, None) not in (None, plataforma):
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
        t = cfg.get("tiktok")
        novo = tiktok.trocar_codigo(t, codigo, t.get("redirect_uri") or self.redirect, verificador)
        cfg.salvar("tiktok", novo)
        try:
            d = tiktok.info(cfg.get("tiktok"))
            cfg.salvar("tiktok", {"usuario": d.get("creator_username", "")})
        except Exception:  # noqa: BLE001
            pass
        return "TikTok conectado"

    # ----- rotas
    def get(self, caminho, q):
        m = self.motor
        if caminho == "/api/estado":
            return {"nome": NOME, "versao": VERSAO, "max_canais": MAX_CANAIS, "redirect": self.redirect,
                    **m.estado()}
        if caminho == "/api/config":
            return m.config.publico()
        if caminho == "/api/ranking":
            horas = float((q.get("horas") or ["0"])[0]) or None
            return m.banco.ranking(limite=int((q.get("limite") or ["100"])[0]), horas=horas)
        if caminho == "/api/log":
            return m.banco.log()
        if caminho == "/api/eventos":
            return m.banco.eventos()
        raise KeyError(caminho)

    def post(self, caminho, corpo):
        m, cfg = self.motor, self.motor.config
        if caminho == "/api/canais":
            canais = []
            for c in corpo.get("canais", [])[:MAX_CANAIS]:
                nome = re.sub(r"[^a-z0-9_]", "", str(c.get("nome", "")).lower().split("/")[-1])
                if nome:
                    canais.append({"nome": nome, "plataforma": "twitch", "layout": c.get("layout", "blur"),
                                   "ativo": bool(c.get("ativo", True)), "cam": c.get("cam")})
            cfg.salvar("canais", canais)
            m.sincronizar_canais()
            return {"ok": True, "canais": canais}
        if mm := re.fullmatch(r"/api/config/(\w+)", caminho):
            secao = mm.group(1)
            if secao not in ("gravacao", "deteccao", "agendador", "youtube", "tiktok", "facebook", "gemini",
                             "telegram"):
                raise KeyError(secao)
            cfg.salvar(secao, corpo)
            return {"ok": True}
        if mm := re.fullmatch(r"/api/desconectar/(\w+)", caminho):
            p = mm.group(1)
            campos = {"youtube": ["refresh_token"], "tiktok": ["refresh_token", "access_token"],
                      "facebook": ["page_token"], "gemini": ["api_key"], "telegram": ["bot_token"]}[p]
            for k in campos:
                cfg.apagar_segredo(p, k)
            return {"ok": True}
        if caminho == "/api/encerrar":
            if self.ao_encerrar:
                threading.Timer(0.5, self.ao_encerrar).start()
            return {"ok": True}
        if caminho == "/api/clipar":
            return {"ok": m.clipar_agora(corpo["canal"])}
        if mm := re.fullmatch(r"/api/corte/(\d+)/(\w+)", caminho):
            cid, acao = int(mm.group(1)), mm.group(2)
            corte = m.banco.corte(cid)
            if not corte:
                raise KeyError(cid)
            if acao == "postar":
                res = {}
                for p in corpo.get("plataformas", []):
                    try:
                        res[p] = {"ok": True, "url": m.postar(corte, p, origem="manual")}
                    except Exception as e:  # noqa: BLE001
                        res[p] = {"ok": False, "erro": str(e)}
                return res
            if acao == "descartar":
                m.banco.descartar(cid, bool(corpo.get("sim", True)))
                return {"ok": True}
            if acao == "regerar":
                return m.regerar_textos(cid)
            if acao == "textos":
                tags = corpo.get("hashtags", [])
                if isinstance(tags, str):
                    tags = [t for t in re.split(r"[\s,]+", tags) if t]
                tags = ["#" + t.lstrip("#") for t in tags]
                m.banco.atualizar_textos(cid, corpo.get("titulo", "")[:100], corpo.get("legenda", ""), tags)
                return {"ok": True}
        if caminho == "/api/youtube/login":
            y = cfg.get("youtube")
            if not y.get("client_id") or not y.get("client_secret"):
                raise ValueError("Preencha o ID do cliente e a chave secreta e salve.")
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
            return {"ok": True, "msg": f"Página escolhida: {p['nome']}"}
        if caminho == "/api/facebook/testar":
            return {"msg": facebook.testar(cfg.get("facebook"))}
        if caminho == "/api/gemini/testar":
            g = cfg.get("gemini")
            t = ia.gerar_textos("teste", "Meu Deus, que jogada! Ganhei a partida no último segundo!",
                                ["KKKKKK", "QUE ISSO", "clipa"], g.get("api_key"), g.get("modelo"))
            return {"msg": f"Funcionando. Exemplo: {t['titulo']}"}
        if caminho == "/api/telegram/descobrir":
            chat = telegram.descobrir_chat(cfg.get("telegram"))
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

            def log_message(self, *a):
                pass

            def _json(self, dados, status=200):
                b = json.dumps(dados, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)

            def _arquivo(self, p: Path, tipo):
                b = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                try:
                    if u.path == "/" and "code" in q:
                        return self._callback(q)
                    if u.path in ("/", "/index.html"):
                        return self._arquivo(WEB / "index.html", "text/html; charset=utf-8")
                    if mm := re.fullmatch(r"/video/(\d+)", u.path):
                        c = painel.motor.banco.corte(int(mm.group(1)))
                        if c and Path(c["arquivo"]).exists():
                            return self._video(Path(c["arquivo"]))
                        return self._json({"erro": "vídeo não encontrado"}, 404)
                    if u.path.startswith("/api/"):
                        return self._json(painel.get(u.path, q))
                    return self._json({"erro": "não encontrado"}, 404)
                except KeyError:
                    return self._json({"erro": "não encontrado"}, 404)
                except Exception as e:  # noqa: BLE001
                    log.exception("erro na API")
                    return self._json({"erro": str(e)}, 500)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                bruto = self.rfile.read(n) if n else b""
                # Só o próprio painel manda esse cabeçalho; outro site não consegue (CORS).
                if self.headers.get("X-CortesEX") != "1":
                    return self._json({"erro": "proibido"}, 403)
                try:
                    corpo = json.loads(bruto or b"{}")
                    return self._json(painel.post(urllib.parse.urlparse(self.path).path, corpo))
                except KeyError as e:
                    return self._json({"erro": f"não encontrado: {e}"}, 404)
                except Exception as e:  # noqa: BLE001
                    log.warning("API %s: %s", self.path, e)
                    return self._json({"erro": str(e)}, 400)

            def _callback(self, q):
                estado = (q.get("state") or [""])[0]
                plat = painel.estados.get(estado) or estado.split("-")[0]
                try:
                    msg = painel.concluir_login(plat, q["code"][0], estado)
                    ok = True
                except Exception as e:  # noqa: BLE001
                    msg, ok = str(e), False
                destino = "/#contas?" + urllib.parse.urlencode({"ok" if ok else "erro": msg})
                self.send_response(303)
                self.send_header("Location", destino)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _video(self, p: Path):
                tam = p.stat().st_size
                ini, fim = 0, tam - 1
                m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        ini = int(m.group(1))
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
