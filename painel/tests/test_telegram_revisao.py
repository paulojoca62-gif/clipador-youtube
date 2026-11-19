"""Revisão pelo Telegram: envio do corte com botões e tratamento seguro dos cliques."""
import json

import pytest

from cortesex import db as D
from cortesex import motor as motormod
from cortesex import telegram_revisao as T
from cortesex.plataformas import resultado
from conftest import autorizar, corte

TOKEN = "123456:AAH-token-de-teste-abcdefghijklmnop"
CHAT = "5550001"


class FakeTG:
    """Telegram falso: guarda cada chamada e devolve 'ok'."""
    def __init__(self, updates=None):
        self.chamadas, self.updates = [], list(updates or [])

    def _r(self, result):
        return type("R", (), {"status_code": 200, "json": lambda s: {"ok": True, "result": result}})()

    def post(self, url, **k):
        metodo = url.rsplit("/", 1)[1]
        corpo = k.get("json") or dict(k.get("data") or {})
        if "files" in k:
            corpo["_arquivo"] = k["files"]["video"][0]
        self.chamadas.append((metodo, corpo))
        return self._r({"message_id": 99})

    def get(self, url, **k):
        self.chamadas.append(("getUpdates", k.get("params")))
        u, self.updates = self.updates, []
        return self._r(u)

    def metodos(self):
        return [c[0] for c in self.chamadas]


@pytest.fixture
def rev(ambiente):
    m, pasta = ambiente
    m.config.salvar("telegram", {"bot_token": TOKEN, "chat_id": CHAT})
    tg = FakeTG()
    r = T.RevisaoTelegram(m, post=tg.post, get=tg.get, assincrono=False)
    m.telegram_rev = r
    return r, tg, m, pasta


def clique(acao, cid, chat=CHAT, sig=None, uid=1):
    dados = f"{acao}:{cid}:{sig}" if sig is not None else T.dados_botao(TOKEN, acao, cid)
    return {"update_id": uid, "callback_query": {"id": "q1", "data": dados,
                                                 "message": {"message_id": 7, "chat": {"id": int(chat)}}}}


def pronto(m, pasta, titulo="QUE JOGADA! 🤯"):
    cid = corte(m, pasta, 3.0)
    m.banco.atualizar_textos(cid, titulo, "legenda", ["#alanzoka", "#shorts"])
    m.banco.mudar_estado(cid, D.REVISAO)
    return cid


def test_corte_pronto_chega_com_video_e_botoes(rev):
    r, tg, m, pasta = rev
    cid = pronto(m, pasta, '<b>titulo</b> "aspas" & tags')
    r.enviar_corte(cid)
    metodo, corpo = tg.chamadas[-1]
    assert metodo == "sendVideo" and corpo["_arquivo"].endswith(".mp4") and corpo["chat_id"] == CHAT
    assert "<b>titulo</b>" in corpo["caption"] and "parse_mode" not in corpo     # texto puro, sem HTML
    botoes = json.loads(corpo["reply_markup"])["inline_keyboard"][0]
    assert [b["text"] for b in botoes] == ["✅ Aprovar", "❌ Descartar", "🚀 Postar agora"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in botoes)
    assert TOKEN not in json.dumps(botoes)


def test_video_grande_vai_so_o_texto(rev, monkeypatch):
    r, tg, m, pasta = rev
    cid = pronto(m, pasta)
    monkeypatch.setattr(T, "LIMITE_VIDEO", -1)           # qualquer arquivo fica "grande demais"
    r.enviar_corte(cid)
    assert tg.chamadas[-1][0] == "sendMessage" and "grande demais" in tg.chamadas[-1][1]["text"]


def test_pipeline_avisa_o_telegram_quando_o_corte_fica_pronto(rev, monkeypatch):
    r, tg, m, pasta = rev
    a = m.banco.novo_candidato("a", pasta / "a.mp4", 0, 3, "p", [], 0, 0)
    m.banco.atualizar_textos(a, "T", "L", ["#a"])
    m.banco.mudar_estado(a, D.MONTADO)
    assert m.processar_proximo(etapas=m.ETAPAS_IA)
    assert r.fila.get_nowait() == a


def test_aprovar_pelo_botao(rev):
    r, tg, m, pasta = rev
    cid = pronto(m, pasta)
    r.tratar(clique("a", cid))
    assert m.banco.corte(cid)["estado"] == D.APROVADO
    assert "answerCallbackQuery" in tg.metodos() and "editMessageReplyMarkup" in tg.metodos()
    r.tratar(clique("a", cid))                                   # segundo toque: não faz nada de novo
    assert "já está" in tg.chamadas[-1][1]["text"]


def test_descartar_pelo_botao(rev):
    r, tg, m, pasta = rev
    cid = pronto(m, pasta)
    r.tratar(clique("d", cid))
    assert m.banco.corte(cid)["estado"] == D.DESCARTADO


def test_chat_estranho_e_assinatura_falsa_sao_ignorados(rev):
    r, tg, m, pasta = rev
    cid = pronto(m, pasta)
    r.tratar(clique("a", cid, chat="999"))
    r.tratar(clique("a", cid, sig="0" * 16))
    r.tratar(clique("a", cid + 1, sig=T.assinatura(TOKEN, "a", cid)))   # assinatura de outro corte
    r.tratar(clique("x", cid, sig=T.assinatura(TOKEN, "x", cid)))       # ação inventada
    assert m.banco.corte(cid)["estado"] == D.REVISAO
    assert "editMessageReplyMarkup" not in tg.metodos()


def test_postar_agora_usa_o_motor_e_respeita_autorizacao(rev, monkeypatch):
    r, tg, m, pasta = rev
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"plataformas": {"youtube": True, "tiktok": False, "facebook": False}})
    cid = pronto(m, pasta)
    enviados = []
    monkeypatch.setattr(motormod.youtube, "postar",
                        lambda cfg, c: enviados.append(c["id"]) or resultado("https://youtu.be/x", "x"))
    r.tratar(clique("p", cid))                                    # canal ainda sem autorização para publicar
    assert enviados == [] and "❌ YouTube" in tg.chamadas[-1][1]["text"]
    autorizar(m)
    cid2 = pronto(m, pasta)
    r.tratar(clique("p", cid2))
    assert enviados == [cid2] and "✅ YouTube: https://youtu.be/x" in tg.chamadas[-1][1]["text"]
    r.tratar(clique("p", cid2))                                   # repetir o toque não posta de novo
    assert enviados == [cid2]


def test_cliques_nao_sao_reprocessados_depois_de_reabrir(rev):
    r, tg, m, pasta = rev
    cid = pronto(m, pasta)
    tg.updates = [clique("a", cid, uid=41), {"update_id": 42, "message": {"chat": {"id": 777}, "text": "oi"}}]
    assert r.buscar_cliques(espera=0) == 2
    assert m.banco.get_meta("telegram_offset") == "43"
    assert m.banco.get_meta("telegram_ultimo_chat") == "777"     # o "Descobrir" do painel usa isso
    r.buscar_cliques(espera=0)
    assert tg.chamadas[-1] == ("getUpdates", {"offset": 43, "timeout": 0,
                                              "allowed_updates": '["callback_query","message","channel_post"]'})


def test_desligado_nao_envia(rev):
    r, tg, m, pasta = rev
    m.config.salvar("telegram", {"revisar_pelo_telegram": False})
    r.notificar(pronto(m, pasta))
    assert r.fila.empty()
