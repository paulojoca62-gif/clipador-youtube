"""Revisão pelo Telegram: cada corte pronto chega no seu chat com o vídeo e os botões
✅ Aprovar · ❌ Descartar · 🚀 Postar agora.

- Sem servidor nem porta aberta: o programa pergunta ao Telegram (getUpdates, long polling) se houve clique.
- Só obedece ao chat configurado em Configurações → Telegram. Cada botão leva uma assinatura HMAC
  (chave = token do bot) com a ação e o id do corte: um cliente adulterado não consegue inventar comandos.
- Postar agora usa o mesmo `Motor.postar` do painel: autorização do canal, reserva única e "incerto" valem igual.
- Os textos vão sem formatação (parse_mode vazio): o que a IA escreveu é exibido como texto puro.
"""

import hashlib
import hmac
import json
import logging
import queue
import threading
from pathlib import Path

import requests

from cortesex import db as D
from cortesex import seguranca

log = logging.getLogger("cortesex")

API = "https://api.telegram.org/bot{token}/{metodo}"
LIMITE_VIDEO = 49 * 1024 * 1024        # bots enviam até 50 MB
ACOES = {"a": "aprovar", "d": "descartar", "p": "postar"}


def assinatura(token: str, acao: str, cid: int) -> str:
    return hmac.new(token.encode(), f"{acao}:{cid}".encode(), hashlib.sha256).hexdigest()[:16]


def dados_botao(token, acao, cid) -> str:
    return f"{acao}:{cid}:{assinatura(token, acao, cid)}"          # < 64 bytes (limite do Telegram)


def ler_botao(token, dados: str):
    """Devolve (acao, id) se a assinatura confere; senão None."""
    try:
        acao, cid, sig = str(dados).split(":")
        cid = int(cid)
    except ValueError:
        return None
    if acao not in ACOES or not hmac.compare_digest(sig, assinatura(token, acao, cid)):
        return None
    return acao, cid


def teclado(token, cid) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Aprovar", "callback_data": dados_botao(token, "a", cid)},
        {"text": "❌ Descartar", "callback_data": dados_botao(token, "d", cid)},
        {"text": "🚀 Postar agora", "callback_data": dados_botao(token, "p", cid)}]]}


def legenda(c: dict) -> str:
    nota = c.get("score_editorial")
    linhas = [f"🎬 {c.get('titulo') or '(sem título)'}",
              f"📺 {c['canal']} · pico {float(c.get('score') or 0):.1f}x" + (f" · nota {nota:.0f}" if nota else ""),
              "", c.get("legenda") or "", " ".join(c.get("hashtags") or []), "", f"Corte #{c['id']}"]
    return "\n".join(linhas).strip()[:1000]


class RevisaoTelegram:
    def __init__(self, motor, post=requests.post, get=requests.get, assincrono=True):
        self.motor, self.post, self.get, self.assincrono = motor, post, get, assincrono
        self.fila: queue.Queue = queue.Queue()
        self.parar = threading.Event()

    # ----- configuração
    def cfg(self) -> dict:
        return self.motor.config.get("telegram") or {}

    def ligado(self) -> bool:
        c = self.cfg()
        return bool(c.get("bot_token") and c.get("chat_id") and c.get("revisar_pelo_telegram", True))

    def _api(self, metodo, timeout=30, **k):
        url = API.format(token=self.cfg()["bot_token"], metodo=metodo)
        r = (self.get if metodo == "getUpdates" else self.post)(url, timeout=timeout, **k)
        try:
            j = r.json()
        except ValueError:
            j = {}
        if not j.get("ok"):
            raise RuntimeError(f"Telegram {metodo}: {r.status_code} {seguranca.redigir(str(j.get('description', '')))}")
        return j.get("result")

    # ----- envio dos cortes prontos
    def notificar(self, cid: int):
        """Chamado pelo motor quando um corte vai para 'aguardando revisão'."""
        if self.ligado():
            self.fila.put(cid)

    def enviar_corte(self, cid: int):
        c = self.motor.banco.corte(cid)
        if not c or c["estado"] != D.REVISAO:
            return
        tg = self.cfg()
        botoes = teclado(tg["bot_token"], cid)
        arq = Path(c["arquivo"])
        texto = legenda(c)
        if tg.get("enviar_video", True) and arq.exists() and arq.stat().st_size <= LIMITE_VIDEO:
            with open(arq, "rb") as f:
                self._api("sendVideo", timeout=300, files={"video": (arq.name, f, "video/mp4")},
                          data={"chat_id": tg["chat_id"], "caption": texto, "supports_streaming": "true",
                                "reply_markup": json.dumps(botoes)})
        else:
            extra = "\n\n(vídeo grande demais para o Telegram; assista no painel)" if arq.exists() else ""
            self._api("sendMessage", json={"chat_id": tg["chat_id"], "text": texto + extra, "reply_markup": botoes})
        self.motor.banco.historico(cid, D.REVISAO, "enviado para revisão no Telegram")

    # ----- cliques
    def tratar(self, upd: dict):
        q = upd.get("callback_query")
        if not q:
            # mensagem comum: guarda o chat para o botão "Descobrir" do painel (que não pode ler junto)
            msg = upd.get("message") or upd.get("channel_post") or {}
            if (msg.get("chat") or {}).get("id"):
                self.motor.banco.set_meta("telegram_ultimo_chat", str(msg["chat"]["id"]))
            return
        tg = self.cfg()
        chat = str(((q.get("message") or {}).get("chat") or {}).get("id", ""))
        if chat != str(tg.get("chat_id")):
            log.warning("Telegram: clique ignorado de um chat não autorizado")
            return self._responder(q, "Este bot não aceita comandos deste chat.")
        lido = ler_botao(tg["bot_token"], q.get("data", ""))
        if not lido:
            log.warning("Telegram: botão com assinatura inválida ignorado")
            return self._responder(q, "Botão inválido.")
        acao, cid = lido
        c = self.motor.banco.corte(cid)
        if not c:
            return self._responder(q, "Esse corte não existe mais.", fechar=True)
        if acao == "a":
            if c["estado"] != D.REVISAO:
                return self._responder(q, f"Esse corte já está: {c['estado'].replace('_', ' ')}.", fechar=True)
            self.motor.banco.aprovar(cid)
            return self._responder(q, f"✅ Corte #{cid} aprovado — entra na fila do agendador.", fechar=True)
        if acao == "d":
            if c["estado"] not in (D.REVISAO, D.APROVADO):
                return self._responder(q, f"Esse corte já está: {c['estado'].replace('_', ' ')}.", fechar=True)
            self.motor.banco.descartar(cid, True, "descartado pelo Telegram")
            return self._responder(q, f"❌ Corte #{cid} descartado.", fechar=True)
        if c["estado"] not in (D.REVISAO, D.APROVADO):
            return self._responder(q, f"Esse corte já está: {c['estado'].replace('_', ' ')}.", fechar=True)
        self._responder(q, f"🚀 Enviando o corte #{cid}…", fechar=True, aviso_curto="Enviando…")
        if c["estado"] == D.REVISAO:
            self.motor.banco.aprovar(cid)
        if self.assincrono:
            threading.Thread(target=self._postar, args=(cid,), daemon=True).start()
        else:
            self._postar(cid)

    def _postar(self, cid):
        from cortesex.motor import NOMES, conectado
        m = self.motor
        redes = [p for p, ligado in (m.config.get("agendador", "plataformas") or {}).items()
                 if ligado and p in NOMES and conectado(m.config, p)]
        if not redes:
            return self._mensagem(f"Corte #{cid}: nenhuma rede ligada e conectada em Configurações → Agendador.")
        linhas = []
        for p in redes:
            try:
                url = m.postar(m.banco.corte(cid), p, origem="telegram")
                linhas.append(f"✅ {NOMES[p]}: {url or 'enviado, processando'}")
            except Exception as e:  # noqa: BLE001
                linhas.append(f"❌ {NOMES[p]}: {seguranca.redigir(e)}")
        self._mensagem(f"Corte #{cid}\n" + "\n".join(linhas))

    def _responder(self, q, texto, fechar=False, aviso_curto=None):
        tg = self.cfg()
        try:
            self._api("answerCallbackQuery", json={"callback_query_id": q.get("id"), "text": (aviso_curto or texto)[:190]})
            msg = q.get("message") or {}
            if fechar and msg.get("message_id"):
                self._api("editMessageReplyMarkup", json={"chat_id": tg["chat_id"], "message_id": msg["message_id"],
                                                          "reply_markup": {"inline_keyboard": []}})
                self._mensagem(texto, responder_a=msg["message_id"])
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: %s", seguranca.redigir(e))

    def _mensagem(self, texto, responder_a=None):
        corpo = {"chat_id": self.cfg()["chat_id"], "text": texto[:4000]}
        if responder_a:
            corpo["reply_parameters"] = {"message_id": responder_a, "allow_sending_without_reply": True}
        try:
            self._api("sendMessage", json=corpo)
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram: %s", seguranca.redigir(e))

    def buscar_cliques(self, espera=25) -> int:
        """Uma consulta de long polling. O offset fica no banco: clique antigo não é reprocessado."""
        banco = self.motor.banco
        offset = int(banco.get_meta("telegram_offset", "0") or 0)
        atualizacoes = self._api("getUpdates", timeout=espera + 10,
                                 params={"offset": offset, "timeout": espera,
                                         "allowed_updates": '["callback_query","message","channel_post"]'}) or []
        for u in atualizacoes:
            banco.set_meta("telegram_offset", str(int(u["update_id"]) + 1))   # antes de agir: nunca repete
            try:
                self.tratar(u)
            except Exception as e:  # noqa: BLE001
                log.warning("Telegram: erro ao tratar clique: %s", seguranca.redigir(e))
        return len(atualizacoes)

    # ----- threads
    def _loop_cliques(self):
        falhas = 0
        while not self.parar.is_set():
            if not self.ligado():
                self.parar.wait(30)
                continue
            try:
                self.buscar_cliques()
                falhas = 0
            except Exception as e:  # noqa: BLE001
                falhas += 1
                log.warning("Telegram (botões): %s", seguranca.redigir(e))
                self.parar.wait(min(5 * 2 ** min(falhas, 6), 300))

    def _loop_envio(self):
        while not self.parar.is_set():
            try:
                cid = self.fila.get(timeout=5)
            except queue.Empty:
                continue
            for tentativa in range(3):
                try:
                    self.enviar_corte(cid)
                    break
                except Exception as e:  # noqa: BLE001
                    log.warning("Telegram: não enviei o corte #%s para revisão: %s", cid, seguranca.redigir(e))
                    self.parar.wait(20 * (tentativa + 1))

    def iniciar(self):
        threading.Thread(target=self._loop_cliques, name="telegram-botoes", daemon=True).start()
        threading.Thread(target=self._loop_envio, name="telegram-envio", daemon=True).start()
