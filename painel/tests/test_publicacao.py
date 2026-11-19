"""Publicação: idempotência, autorização, modos, pausas por tipo de erro, verificação pós-envio, limpeza."""
import time
from datetime import date

import pytest

from cortesex import motor as motormod
from cortesex.plataformas import ErroPostagem, resultado
from conftest import autorizar, corte


@pytest.fixture
def pronto(ambiente):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("tiktok", {"refresh_token": "r", "access_token": "a", "expira_em": time.time() + 9999})
    autorizar(m)
    return m, pasta


def test_autorizacao_por_rede_status_e_validade():
    canal = {"autorizacao": {"status": "autorizado", "publicar": True, "redes": ["youtube"], "validade": "2026-12-31"}}
    assert motormod.autorizado_para(canal, "youtube", date(2026, 11, 19)) == (True, "")
    assert not motormod.autorizado_para(canal, "tiktok", date(2026, 11, 19))[0]
    assert "venceu" in motormod.autorizado_para(canal, "youtube", date(2027, 1, 1))[1]
    assert not motormod.autorizado_para({"autorizacao": {"status": "pendente"}}, "youtube")[0]
    assert not motormod.autorizado_para({"autorizacao": {"status": "autorizado", "publicar": False,
                                                         "redes": ["youtube"]}}, "youtube")[0]
    assert not motormod.autorizado_para(None, "youtube")[0]


def test_sem_autorizacao_nao_posta_nem_manual_sem_confirmar(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"ativo": True, "modo": "automatico", "plataformas": {"youtube": True}})
    cid = corte(m, pasta, 4.0)
    enviados = []
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: enviados.append(c["id"]) or resultado("u", "v1"))
    assert m.rodar_agendador() == []
    bons, fora = m.candidatos("youtube")
    assert not bons and fora[0]["_fora"] == "canal não está cadastrado"
    m.config.salvar("canais", [{"nome": "alanzoka"}])          # cadastrado, mas sem autorização registrada
    assert "nao_registrada" in m.candidatos("youtube")[1][0]["_fora"]
    with pytest.raises(ErroPostagem, match="bloqueada"):
        m.postar(m.banco.corte(cid), "youtube", origem="manual")
    assert m.banco.pausa("youtube") is None            # bloqueio de autorização não pausa a rede
    m.postar(m.banco.corte(cid), "youtube", origem="manual", ignorar_autorizacao=True)
    assert enviados == [cid]


def test_modos_manual_semi_automatico(pronto, monkeypatch):
    m, pasta = pronto
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: resultado(f"https://youtu.be/{c['id']}", c["id"]))
    revisao = corte(m, pasta, 4.0)
    aprovado = corte(m, pasta, 3.0)
    m.banco.aprovar(aprovado)
    base = {"ativo": True, "quantidade": 60, "periodo": "hora", "plataformas": {"youtube": True}}
    m.config.salvar("agendador", {**base, "modo": "manual"})
    assert m.rodar_agendador() == []
    m.config.salvar("agendador", {**base, "modo": "semiautomatico"})
    assert m.rodar_agendador() == [("youtube", aprovado)]           # só o aprovado
    m.config.salvar("agendador", {**base, "modo": "automatico"})
    assert m.rodar_agendador(time.time() + 120) == [("youtube", revisao)]


def test_nao_posta_duas_vezes_na_mesma_rede(pronto, monkeypatch):
    m, pasta = pronto
    n = []
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: n.append(1) or resultado("https://youtu.be/a", "a"))
    cid = corte(m, pasta, 3.0)
    m.postar(m.banco.corte(cid), "youtube", origem="manual")
    with pytest.raises(ErroPostagem, match="já está"):
        m.postar(m.banco.corte(cid), "youtube", origem="manual")
    m.postar(m.banco.corte(cid), "youtube", origem="manual", forcar=True)   # de propósito
    assert len(n) == 2
    # outra rede é independente
    monkeypatch.setattr(motormod.tiktok, "postar", lambda cfg, c: resultado("", "pub1", "processando"))
    m.postar(m.banco.corte(cid), "tiktok", origem="manual")
    st = {p["plataforma"]: p["status"] for p in m.banco.postagens_de(cid)}
    assert st == {"youtube": "postado", "tiktok": "processando"}


def test_envio_interrompido_vira_incerto_e_nao_e_repostado(pronto, monkeypatch):
    m, pasta = pronto
    cid = corte(m, pasta, 3.0)
    m.banco.nova_postagem(cid, "youtube")               # programa “caiu” no meio do envio
    m2 = motormod.Motor(config=m.config, pasta=pasta)
    p = m2.banco.postagens_de(cid)[0]
    assert p["status"] == "incerto"
    m2.config.salvar("agendador", {"ativo": True, "modo": "automatico", "plataformas": {"youtube": True}})
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: pytest.fail("repostou um envio incerto"))
    assert m2.rodar_agendador() == []
    m2.banco.resolver_incerto(p["id"], publicado=True, url="https://youtu.be/x")
    assert m2.banco.postagens_de(cid)[0]["status"] == "postado"


@pytest.mark.parametrize("tipo,minimo,maximo", [("temporario", 120 * 0.79, 3600 * 1.3), ("cota", 3600 * 0.79, 6 * 3600 * 1.3),
                                                ("autenticacao", 365 * 86400, None)])
def test_pausa_por_tipo_de_erro(pronto, monkeypatch, tipo, minimo, maximo):
    m, pasta = pronto

    def falha(cfg, c):
        raise ErroPostagem("falhou", tipo == "temporario", tipo)
    monkeypatch.setattr(motormod.youtube, "postar", falha)
    cid = corte(m, pasta, 3.0)
    with pytest.raises(ErroPostagem):
        m.postar(m.banco.corte(cid), "youtube")
    pausa = m.banco.pausa("youtube")
    espera = pausa["ate"] - time.time()
    assert espera >= minimo and (maximo is None or espera <= maximo)
    assert m.banco.postagens_de(cid)[0]["tipo_erro"] == tipo
    m.retomar("youtube")
    assert m.banco.pausa("youtube") is None


def test_erro_de_midia_nao_pausa_a_rede(pronto, monkeypatch):
    m, pasta = pronto
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: (_ for _ in ()).throw(
        ErroPostagem("vídeo recusado", False, "midia")))
    cid = corte(m, pasta, 3.0)
    with pytest.raises(ErroPostagem):
        m.postar(m.banco.corte(cid), "youtube")
    assert m.banco.pausa("youtube") is None


def test_backoff_cresce_com_tentativas(pronto, monkeypatch):
    m, pasta = pronto
    monkeypatch.setattr(motormod.util, "backoff", lambda t, base, maximo, jitter=0.2: min(maximo, base * 2 ** (t - 1)))
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: (_ for _ in ()).throw(
        ErroPostagem("instável", True, "temporario")))
    esperas = []
    for _ in range(3):
        with pytest.raises(ErroPostagem):
            m.postar(m.banco.corte(corte(m, pasta, 3.0)), "youtube")
        esperas.append(round(m.banco.pausa("youtube")["ate"] - time.time()))
    assert esperas[0] < esperas[1] < esperas[2]


def test_verificacao_pos_envio(pronto, monkeypatch):
    m, pasta = pronto
    monkeypatch.setattr(motormod.tiktok, "postar", lambda cfg, c: resultado("", "pub9", "processando"))
    cid = corte(m, pasta, 3.0)
    m.postar(m.banco.corte(cid), "tiktok", origem="manual")
    respostas = iter([resultado("", "pub9", "processando", "PROCESSING_UPLOAD"),
                      resultado("https://www.tiktok.com/@eu/video/1", "pub9", "postado")])
    monkeypatch.setattr(motormod.tiktok, "verificar", lambda cfg, ext: next(respostas))
    assert m.verificar_processando() == 0
    assert m.verificar_processando() == 1
    p = m.banco.postagens_de(cid)[0]
    assert p["status"] == "postado" and p["url"].endswith("/video/1")


def test_video_invalido_nao_vai_para_a_rede(pronto, monkeypatch):
    m, pasta = pronto
    from cortesex import midia
    monkeypatch.setattr(motormod.midia, "validar", lambda *a, **k: (_ for _ in ()).throw(midia.MidiaInvalida("sem áudio")))
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: pytest.fail("enviou vídeo inválido"))
    with pytest.raises(ErroPostagem, match="inválido"):
        m.postar(m.banco.corte(corte(m, pasta, 3.0)), "youtube", origem="manual")


def test_limpeza_protege_aprovados_e_publicados(pronto):
    m, pasta = pronto
    velho = 30
    a = corte(m, pasta, 3.0, horas_atras=velho * 24)
    b = corte(m, pasta, 3.0, horas_atras=velho * 24)
    c = corte(m, pasta, 3.0, horas_atras=velho * 24)
    m.banco.aprovar(a)                                       # aguardando publicação
    pid = m.banco.nova_postagem(c, "youtube")
    m.banco.fim_postagem(pid, "postado", url="u")
    m.config.salvar("armazenamento", {"manter_publicados": True, "minimo_livre_gb": 0})
    m.limpar()
    assert m.banco.corte(a)["arquivo_removido"] == 0 and m.banco.corte(c)["arquivo_removido"] == 0
    assert m.banco.corte(b)["arquivo_removido"] == 1
    m.config.salvar("armazenamento", {"manter_publicados": False})
    m.limpar()
    assert m.banco.corte(c)["arquivo_removido"] == 1 and m.banco.corte(a)["arquivo_removido"] == 0


def test_disco_baixo_pausa_gravacao(pronto):
    m, pasta = pronto
    m.config.salvar("armazenamento", {"minimo_livre_gb": 10 ** 9})
    m.limpar()
    assert m.disco_baixo
    m.config.salvar("armazenamento", {"minimo_livre_gb": 0})
    m.limpar()
    assert not m.disco_baixo


def test_ordenacao_por_score_editorial(pronto):
    m, pasta = pronto
    x, y = corte(m, pasta, 4.0), corte(m, pasta, 2.6)
    m.banco.atualizar(x, score_editorial=40)
    m.banco.atualizar(y, score_editorial=85)
    m.banco.aprovar(x)
    m.banco.aprovar(y)
    m.config.salvar("agendador", {"modo": "semiautomatico", "ordenar_por": "editorial"})
    assert [c["id"] for c in m.candidatos("youtube")[0]] == [y, x]
    m.config.salvar("agendador", {"ordenar_por": "pico"})
    assert [c["id"] for c in m.candidatos("youtube")[0]] == [x, y]
