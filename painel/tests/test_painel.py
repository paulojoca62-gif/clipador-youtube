import json
import time
from datetime import datetime

import pytest

from cortesex import config as cfgmod
from cortesex import ia, motor
from cortesex.plataformas import ErroPostagem
from cortesex.servidor import extrair_codigo


from conftest import autorizar, corte


def test_segredos_nao_saem_e_vazio_nao_apaga(tmp_path):
    c = cfgmod.Config(tmp_path / "config.json")
    c.salvar("gemini", {"api_key": "AIza-secreta", "modelo": "x"})
    c.salvar("gemini", {"api_key": "", "modelo": "y"})
    assert c.get("gemini", "api_key") == "AIza-secreta"
    pub = c.publico()
    assert pub["gemini"]["api_key"] is True
    assert "AIza" not in json.dumps(pub)
    assert cfgmod.Config(tmp_path / "config.json").get("gemini", "modelo") == "y"


def test_maximo_cinco_canais(tmp_path):
    c = cfgmod.Config(tmp_path / "config.json")
    c.salvar("canais", [{"nome": f"c{i}"} for i in range(8)])
    assert len(c.get("canais")) == 5


def test_ritmo_do_agendador():
    ag = {"quantidade": 5, "periodo": "hora"}
    agora = 10_000.0
    assert motor.vez_de_postar(ag, 0, 0, agora)
    assert not motor.vez_de_postar(ag, agora - 600, 1, agora)       # 12 min de intervalo
    assert motor.vez_de_postar(ag, agora - 721, 1, agora)
    assert not motor.vez_de_postar(ag, agora - 3000, 5, agora)      # já postou 5 na hora
    assert not motor.vez_de_postar({"quantidade": 1, "periodo": "dia"}, agora - 3600 * 5, 0, agora)


def test_janela_e_dias():
    ag = {"hora_inicio": 22, "hora_fim": 2, "dias": [0]}
    seg_23h = datetime(2026, 11, 16, 23)   # segunda
    assert motor.dentro_da_janela(ag, seg_23h)
    assert not motor.dentro_da_janela(ag, datetime(2026, 11, 16, 12))
    assert not motor.dentro_da_janela(ag, datetime(2026, 11, 17, 23))   # terça


def test_agendador_posta_o_melhor_e_respeita_regras(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    # 2.0: o modo automático e a autorização do canal passaram a ser exigidos para postar sozinho
    m.config.salvar("agendador", {"ativo": True, "modo": "automatico", "quantidade": 2, "periodo": "hora",
                                  "score_minimo": 2.5, "ordenar_por": "pico",
                                  "plataformas": {"youtube": True, "tiktok": False, "facebook": False}})
    autorizar(m)
    baixo = corte(m, pasta, 2.0)
    melhor = corte(m, pasta, 3.4)
    medio = corte(m, pasta, 2.9)
    sem_texto = corte(m, pasta, 5.0, titulo="")
    velho = corte(m, pasta, 9.0, horas_atras=30)
    enviados = []
    monkeypatch.setattr(motor.youtube, "postar", lambda cfg, c: enviados.append(c["id"]) or f"https://youtu.be/{c['id']}")

    t = time.time()
    assert m.rodar_agendador(t) == [("youtube", melhor)]
    assert m.rodar_agendador(t + 60) == []                    # intervalo de 30 min
    assert m.rodar_agendador(t + 1801) == [("youtube", medio)]
    assert enviados == [melhor, medio]
    assert baixo not in enviados and sem_texto not in enviados and velho not in enviados
    log = m.banco.log()
    assert [p["status"] for p in log] == ["postado", "postado"]


def test_limite_da_plataforma_pausa_e_registra(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"ativo": True, "modo": "automatico", "plataformas": {"youtube": True}})
    autorizar(m)
    corte(m, pasta, 3.0)

    def falha(cfg, c):
        raise ErroPostagem("YouTube: limite do dia atingido", False, "cota")
    monkeypatch.setattr(motor.youtube, "postar", falha)
    assert m.rodar_agendador() == []
    pausa = m.banco.pausa("youtube")
    assert pausa["motivo"].startswith("YouTube: limite") and pausa["tipo"] == "cota"
    assert pausa["ate"] > time.time() + 3600 * 0.79   # cota: pausa longa (1 h com variação de ±20%)
    assert m.banco.log()[0]["status"] == "erro"
    assert m.estado()["plataformas"]["youtube"]["pausa"]


def test_ranking_ordena_por_nota(ambiente):
    m, pasta = ambiente
    for s in (2.6, 3.8, 3.1):
        corte(m, pasta, s)
    assert [c["score"] for c in m.banco.ranking()] == [3.8, 3.1, 2.6]


def test_textos_do_gemini_e_reserva(monkeypatch):
    chamadas = []

    class R:
        def __init__(self, status, conteudo=""):
            self.status_code, self._c = status, conteudo

        def json(self):
            return {"choices": [{"message": {"content": self._c}}]}

        def raise_for_status(self):
            pass

    respostas = [R(503), R(200, '```json\n{"titulo": "QUE ISSO! 😱", "legenda": "Ele ganhou.", '
                                 '"hashtags": ["alanzoka", "#gta", "#gta"]}\n```')]

    def post(url, headers, json, timeout):
        chamadas.append(json["model"])
        return respostas.pop(0)
    t = ia.gerar_textos("alanzoka", "fala", ["kkk"], "chave", post=post, dormir=lambda s: None)
    assert t["titulo"] == "QUE ISSO! 😱"
    assert t["hashtags"] == ["#alanzoka", "#gta", "#shorts"]
    assert chamadas[1] != chamadas[0]   # tentou outro modelo depois do 503
    with pytest.raises(ia.SemChave):
        ia.gerar_textos("x", "", [], "")


def test_codigo_do_login():
    assert extrair_codigo("http://127.0.0.1:8777/?code=4%2F0Ab&state=youtube-xyz") == ("4/0Ab", "youtube-xyz")
    assert extrair_codigo("  abc  ") == ("abc", "")


def test_tiktok_pkce():
    from cortesex.plataformas import tiktok
    v = tiktok.novo_verificador()
    url = tiktok.link_login({"client_key": "k"}, "http://127.0.0.1:8777/", "tiktok-x", v)
    assert "code_challenge=" + tiktok.desafio(v) in url and "code_challenge_method=S256" in url
    assert len(tiktok.desafio(v)) == 64


def test_completa_textos_que_faltaram(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("gemini", {"api_key": "k"})
    sem = corte(m, pasta, 3.0, titulo="")
    com = corte(m, pasta, 2.0)
    falhas = iter([RuntimeError("lento")])

    def gerar(canal, fala, chat, chave, modelo=None, **_):
        erro = next(falhas, None)
        if erro:
            raise erro
        return {"titulo": "NOVO! 🔥", "legenda": "l", "hashtags": ["#x"],
                "analise": {"categoria": "engracado", "notas": {"contexto": 8}, "riscos": []}}
    monkeypatch.setattr(motor.ia, "analisar_e_escrever", gerar)
    assert m.completar_textos() == 0          # falhou uma vez
    assert m.tentativas_ia[sem] == 1
    assert m.completar_textos() == 1
    assert m.banco.corte(sem)["titulo"] == "NOVO! 🔥"
    assert m.banco.corte(sem)["categoria"] == "engracado"
    assert m.banco.corte(com)["titulo"] == "TÍTULO"


def test_gemini_modelo_inexistente_ou_vazio_usa_outro():
    class R:
        def __init__(self, status):
            self.status_code = status

        def json(self):
            return {"choices": [{"message": {"content": '{"titulo": "OK", "legenda": "", "hashtags": []}'}}]}

        def raise_for_status(self):
            pass
    usados = []

    def post(url, headers, json, timeout):
        usados.append(json["model"])
        return R(404 if len(usados) == 1 else 200)
    assert ia.gerar_textos("c", "f", [], "k", "", post=post, dormir=lambda s: None)["titulo"] == "OK"
    assert usados[0] == "gemini-flash-latest" and usados[1] != usados[0]
