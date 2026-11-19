"""IA com reserva: Gemini primeiro, OpenRouter quando ele falha ou não tem chave."""
import pytest

from cortesex import db as D
from cortesex import ia
from cortesex import motor as motormod

OK = '{"titulo": "QUE JOGADA", "legenda": "Olha isso", "hashtags": ["#a"], "categoria": "reacao"}'


class R:
    def __init__(self, st=200, js=None, headers=None):
        self.status_code, self._js, self.headers = st, js, headers or {}

    def json(self):
        return self._js

    def raise_for_status(self):
        pass


def resposta(conteudo=OK, modelo="x/y:free"):
    return R(200, {"model": modelo, "choices": [{"message": {"content": conteudo}}]})


class Cfg:
    def __init__(self, **secoes):
        self.s = secoes

    def get(self, secao, chave=None, padrao=None):
        v = self.s.get(secao, {})
        return v if chave is None else v.get(chave, padrao)


MODELOS = {"data": [
    {"id": "google/gemma-4-31b-it:free", "context_length": 262144, "supported_parameters": ["response_format"]},
    {"id": "zeta/grande:free", "context_length": 1000000, "supported_parameters": ["response_format"]},
    {"id": "liquid/lfm-3-1.7b:free", "context_length": 999999, "supported_parameters": ["response_format"]},
    {"id": "sem/json:free", "context_length": 9, "supported_parameters": ["tools"]},
    {"id": "openai/pago", "context_length": 9, "supported_parameters": ["response_format"]}]}


@pytest.fixture(autouse=True)
def limpa_cache():
    ia._cache_modelos.update(quando=0.0, lista=[])


def test_escolhe_modelos_gratis_com_json_sem_os_minusculos():
    assert ia.modelos_gratis(get=lambda *a, **k: R(200, MODELOS)) == ["google/gemma-4-31b-it:free", "zeta/grande:free"]
    assert ia.modelos_gratis(get=lambda *a, **k: pytest.fail("não usou o cache"))   # 12 h de cache


def test_gemini_ok_nao_chama_o_openrouter():
    cfg = Cfg(gemini={"api_key": "g"}, openrouter={"api_key": "o"})
    usados = []

    def post(url, **k):
        usados.append(url)
        return resposta()
    r = ia.gerar(cfg, "c", "f", [], post=post, dormir=lambda s: None)
    assert r["provedor"] == "gemini" and all("openrouter" not in u for u in usados)


def test_gemini_falha_e_o_openrouter_assume():
    cfg = Cfg(gemini={"api_key": "g"}, openrouter={"api_key": "o", "modelos": "a/b:free, c/d"})
    corpos = []

    def post(url, json=None, headers=None, **k):
        if "generativelanguage" in url:
            return R(503)
        corpos.append((json, headers))
        return resposta(modelo="a/b:free")
    r = ia.gerar(cfg, "c", "f", [], post=post, dormir=lambda s: None)
    assert r["provedor"] == "openrouter" and r["titulo"] == "QUE JOGADA" and r["modelo"] == "a/b:free"
    corpo, cab = corpos[0]
    assert corpo["models"] == ["a/b:free", "c/d"] and corpo["response_format"] == {"type": "json_object"}
    assert cab["Authorization"] == "Bearer o" and cab["X-Title"] == "CortesEX-PRESS"
    assert "<<<DADOS_NAO_CONFIAVEIS>>>" in corpo["messages"][1]["content"]      # mesma proteção do Gemini


def test_so_openrouter_configurado():
    cfg = Cfg(gemini={}, openrouter={"api_key": "o"})
    r = ia.gerar(cfg, "c", "f", [], post=lambda *a, **k: resposta(), get=lambda *a, **k: R(200, MODELOS),
                 dormir=lambda s: None)
    assert r["provedor"] == "openrouter" and ia.configurada(cfg)


def test_reserva_desligada_ou_sem_chave():
    assert not ia.configurada(Cfg(gemini={}, openrouter={"api_key": "o", "ativo": False}))
    with pytest.raises(ia.SemChave):
        ia.gerar(Cfg(gemini={}, openrouter={}), "c", "f", [], post=lambda *a, **k: pytest.fail("chamou"))


def test_os_dois_falham_mensagem_tem_os_dois():
    cfg = Cfg(gemini={"api_key": "g"}, openrouter={"api_key": "o", "modelos": "a/b"})
    with pytest.raises(RuntimeError) as e:
        ia.gerar(cfg, "c", "f", [], post=lambda url, **k: R(503) if "generativelanguage" in url else R(402),
                 dormir=lambda s: None)
    assert "Gemini" in str(e.value) and "créditos (402)" in str(e.value)


def test_openrouter_ocupado_tenta_de_novo_e_respeita_o_tempo():
    respostas = [R(429, headers={"Retry-After": "2"}), R(200, {"error": {"message": "provider down"}}), resposta()]
    esperas = []
    r = ia.analisar_openrouter("c", "f", [], "o", ["a/b"], post=lambda *a, **k: respostas.pop(0),
                               dormir=esperas.append)
    assert r["titulo"] == "QUE JOGADA" and esperas == [2.0]
    agora = {"t": 0.0}

    def lento(*a, **k):
        agora["t"] += 50
        return R(503)
    with pytest.raises(RuntimeError, match="tempo máximo"):
        ia.analisar_openrouter("c", "f", [], "o", ["a/b"], post=lento, dormir=lambda s: None,
                               relogio=lambda: agora["t"])


def test_resposta_do_openrouter_passa_pela_mesma_validacao():
    ruim = '{"titulo": "<script>x</script> morreu", "hashtags": []}'
    with pytest.raises(RuntimeError, match="resposta inválida"):
        ia.analisar_openrouter("c", "f", [], "o", ["a/b"], post=lambda *a, **k: resposta(ruim), dormir=lambda s: None)


def test_pipeline_usa_a_reserva(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("gemini", {"api_key": "g"})
    m.config.salvar("openrouter", {"api_key": "o"})
    monkeypatch.setattr(motormod.ia, "analisar_e_escrever", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("503")))
    monkeypatch.setattr(motormod.ia, "analisar_openrouter",
                        lambda *a, **k: {**ia.validar({"titulo": "PELA RESERVA"}, "a"), "modelo": "a/b:free"})
    cid = m.banco.novo_candidato("a", pasta / "a.mp4", 0, 3, "p", [], 0, 0)
    m.banco.mudar_estado(cid, D.MONTADO)
    assert m.processar_proximo(etapas=m.ETAPAS_IA)
    assert m.banco.corte(cid)["titulo"] == "PELA RESERVA"
    assert "openrouter" in m.banco.historico_de(cid)[-1]["detalhe"]


def test_chave_do_openrouter_vai_para_o_cofre(ambiente):
    m, pasta = ambiente
    m.config.salvar("openrouter", {"api_key": "sk-or-v1-segredo123"})
    assert "sk-or-v1-segredo123" not in (pasta / "config.json").read_text()
    assert m.config.publico()["openrouter"]["api_key"] is True
