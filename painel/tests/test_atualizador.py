"""Atualização automática: consulta, download conferido, quando instala e quando espera."""
import base64
import hashlib
import json
import time

import pytest

from cortesex import atualizador as A
from cortesex import db as D
from conftest import corte

CONTEUDO = b"MZ instalador de teste " * 1000
SHA = hashlib.sha256(CONTEUDO).hexdigest()


class Resp:
    def __init__(self, status=200, corpo=b"", js=None):
        self.status_code, self.content, self._js = status, corpo, js

    def json(self):
        return self._js

    def iter_content(self, n):
        for i in range(0, len(self.content), n):
            yield self.content[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def github(build=13, sha=SHA, digest=True, url_inst=None, conteudo=CONTEUDO, tamanho=None, status_api=200):
    """Um GitHub falso: API do release, versao.json e o instalador."""
    url_inst = url_inst or A.PREFIXO + A.INSTALADOR
    tamanho = len(conteudo) if tamanho is None else tamanho
    rel = {"assets": [
        {"name": A.INSTALADOR, "browser_download_url": url_inst, "size": tamanho,
         **({"digest": "sha256:" + SHA} if digest else {})},
        {"name": A.VERSAO_JSON, "browser_download_url": A.PREFIXO + A.VERSAO_JSON, "size": 100}]}
    versao = json.dumps({"build": build, "commit": "abc", "sha256": sha, "tamanho": tamanho}).encode()
    chamadas = []

    def get(url, **k):
        chamadas.append(url)
        if url == A.API:
            return Resp(status_api, js=rel)
        if url.endswith(A.VERSAO_JSON):
            return Resp(200, versao)
        return Resp(200, conteudo)
    get.chamadas = chamadas
    return get


# ----------------------------------------------------------------- consulta
def test_consulta_le_build_e_hash():
    info = A.consultar(github())
    assert info["build"] == 13 and info["sha256"] == SHA and info["tamanho"] == len(CONTEUDO)


@pytest.mark.parametrize("kw,erro", [
    ({"url_inst": "https://evil.example/CortesEX-PRESS-Instalador.exe"}, "fora do repositório"),
    ({"sha": "0" * 64}, "não confere"),
    ({"sha": "xyz"}, "SHA-256"),
    ({"status_api": 404}, "nenhuma versão"),
    ({"status_api": 500}, "500"),
    ({"tamanho": 10**12}, "tamanho"),
])
def test_consulta_recusa_release_suspeito(kw, erro):
    with pytest.raises(A.ErroAtualizacao, match=erro):
        A.consultar(github(**kw))


# ----------------------------------------------------------------- download
def test_download_conferido(tmp_path):
    info = A.consultar(github())
    arq = A.baixar(info, tmp_path, github())
    assert arq.read_bytes() == CONTEUDO and not list(tmp_path.glob("*.parcial"))


def test_download_adulterado_e_descartado(tmp_path):
    info = A.consultar(github(digest=False))
    adulterado = CONTEUDO[:-1] + b"X"
    with pytest.raises(A.ErroAtualizacao, match="não confere"):
        A.baixar(info, tmp_path, github(conteudo=adulterado, digest=False, tamanho=len(CONTEUDO)))
    assert list(tmp_path.iterdir()) == []                     # nada executável fica na pasta


def test_download_maior_que_o_anunciado_e_cortado(tmp_path):
    info = A.consultar(github(digest=False))
    with pytest.raises(A.ErroAtualizacao, match="maior"):
        A.baixar(info, tmp_path, github(conteudo=CONTEUDO + b"extra", digest=False, tamanho=len(CONTEUDO)))
    assert list(tmp_path.iterdir()) == []


def test_comando_de_instalacao_aguenta_acentos_e_aspas(tmp_path):
    inst = tmp_path / "João d'Ávila" / "inst.exe"
    cmd = A.comando_instalacao(inst, tmp_path / "Programas" / "CortesEX-PRESS.exe", tmp_path / "r.txt")
    assert cmd[0] == "powershell.exe" and "-EncodedCommand" in cmd
    script = base64.b64decode(cmd[-1]).decode("utf-16-le")
    assert "João d''Ávila" in script                         # aspas simples dobradas (PowerShell)
    assert "/VERYSILENT" in script and "WaitForExit" in script and "--sem-navegador" in script
    assert "-Wait " not in script                                # não espera a árvore inteira de processos
    assert script.index("WaitForExit") < script.index("CortesEX-PRESS.exe")   # reabre só depois do instalador


# ----------------------------------------------------------------- quando instala
@pytest.fixture
def atu(ambiente, monkeypatch):
    m, pasta = ambiente
    monkeypatch.setattr(A, "build_atual", lambda: (12, "old"))
    monkeypatch.setattr(A, "pode_aplicar", lambda: True)
    rodou, fechou = [], []
    a = A.Atualizador(m, pasta, encerrar=lambda: fechou.append(1), get=github(),
                      popen=lambda cmd, **k: rodou.append(cmd))
    a.rodou, a.fechou = rodou, fechou
    monkeypatch.setattr(A.threading, "Timer", lambda s, f: type("T", (), {"start": lambda self: f()})())
    return a, m, pasta


def test_instala_quando_esta_livre(atu):
    a, m, pasta = atu
    e = a.passo()
    assert e["situacao"] == "instalando" and len(a.rodou) == 1 and a.fechou == [1]
    assert json.loads((pasta / "atualizacoes" / "estado.json").read_text())["build"] == 13


def test_nao_instala_versao_igual_ou_mais_velha(atu, monkeypatch):
    a, *_ = atu
    a.get = github(build=12)
    assert a.passo()["situacao"] == "atualizado" and a.rodou == []


def test_espera_envio_e_processamento(atu):
    a, m, pasta = atu
    cid = corte(m, pasta, 3.0)
    pid = m.banco.nova_postagem(cid, "youtube")                 # envio em andamento
    assert a.passo()["situacao"] == "aguardando" and a.rodou == []
    assert "envio" in a.estado["mensagem"]
    m.banco.fim_postagem(pid, "postado", url="u")
    c2 = m.banco.novo_candidato("x", pasta / "x.mp4", time.time(), 3, "p", [], 0, 0)
    m.banco.mudar_estado(c2, D.MONTANDO)
    assert a.passo()["situacao"] == "aguardando" and "process" in a.estado["mensagem"]
    m.banco.mudar_estado(c2, D.REVISAO)
    assert a.passo()["situacao"] == "instalando"


def test_live_gravando_espera_ate_o_limite(atu):
    a, m, _ = atu
    m.canais["alanzoka"] = type("C", (), {"gravando": True})()
    agora = {"t": 1_000_000.0}
    a.relogio = lambda: agora["t"]
    assert a.passo()["situacao"] == "aguardando" and "live" in a.estado["mensagem"]
    agora["t"] += 23 * 3600
    assert a.passo()["situacao"] == "aguardando"
    agora["t"] += 2 * 3600                                       # passou de 24 h: atualiza mesmo gravando
    assert a.passo()["situacao"] == "instalando"


def test_modos_desligado_e_perguntar(atu):
    a, m, _ = atu
    m.config.salvar("atualizacoes", {"modo": "desligado"})
    assert a.passo()["situacao"] == "disponivel" and a.rodou == []
    assert not list((a.dir).glob("*.exe"))                       # nem baixa
    m.config.salvar("atualizacoes", {"modo": "perguntar"})
    assert a.passo()["situacao"] == "disponivel" and a.rodou == []
    assert a.passo(forcar=True)["situacao"] == "instalando"     # clique em "Atualizar agora"


def test_desiste_depois_de_3_tentativas_do_mesmo_build(atu):
    a, *_ = atu
    for _ in range(3):
        a.passo()
        a.instalador = None
    assert len(a.rodou) == 3
    assert a.passo()["situacao"] == "erro" and len(a.rodou) == 3


def test_reabrir_na_versao_nova_limpa_o_estado(ambiente, monkeypatch):
    m, pasta = ambiente
    d = pasta / "atualizacoes"
    d.mkdir()
    (d / "estado.json").write_text(json.dumps({"build": 13, "tentativas": 1}))
    (d / "CortesEX-PRESS-build13.exe").write_bytes(b"x")
    monkeypatch.setattr(A, "build_atual", lambda: (13, "novo"))
    A.Atualizador(m, pasta, get=github())
    assert json.loads((d / "estado.json").read_text()) == {} and not list(d.glob("*.exe"))


def test_codigo_fonte_so_avisa(ambiente, monkeypatch):
    m, pasta = ambiente
    monkeypatch.setattr(A, "build_atual", lambda: (0, ""))
    rodou = []
    a = A.Atualizador(m, pasta, get=github(), popen=lambda *x, **k: rodou.append(1))
    assert a.passo()["situacao"] == "disponivel" and rodou == []


def test_rede_fora_do_ar_nao_derruba(atu):
    a, *_ = atu

    def caiu(*x, **k):
        raise A.requests.ConnectionError("sem rede")
    a.get = caiu
    assert a.passo()["situacao"] == "erro" and a.rodou == []
