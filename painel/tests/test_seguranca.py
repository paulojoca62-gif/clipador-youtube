import http.client
import json
import socket

import pytest

from cortesex import config as cfgmod
from cortesex import seguranca
from cortesex.segredos import Cofre


def test_redige_tokens_e_segredos_conhecidos():
    texto = ("erro https://api.telegram.org/bot123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjj/sendMessage "
             "key=AIzaSyA1234567890abcdefghijk Authorization: Bearer ya29.a0AbCdEfGh code=4/0AUn6Hd_abc "
             "GOCSPX-5j_TKlwW8MdJ EAAGabcdefghijklmnopqrstuvwxyz \"refresh_token\": \"1//0abcdefghijklmnopqrstuvwx\"")
    seguranca.registrar_segredo("minha-senha-muito-secreta")
    out = seguranca.redigir(texto + " minha-senha-muito-secreta")
    for vaz in ("AAAbbbCCC", "AIzaSyA123", "a0AbCdEf", "4/0AUn6", "5j_TKlwW", "EAAGabcdef", "0abcdefghij",
                "minha-senha"):
        assert vaz not in out


def test_filtro_de_log_redige_mensagem_e_excecao(caplog):
    import logging
    lg = logging.getLogger("teste-redacao")
    lg.addFilter(seguranca.RedigirLog())
    try:
        raise ValueError("falhou com token=SEGREDO123456")
    except ValueError:
        lg.exception("chave AIzaSyA1234567890abcdefghijk")
    texto = caplog.text
    assert "AIzaSyA123" not in texto and "SEGREDO123456" not in texto


def test_host_origem_e_caminho():
    assert seguranca.host_permitido("127.0.0.1:8777", 8777) and seguranca.host_permitido("localhost:8777", 8777)
    assert not seguranca.host_permitido("evil.com:8777", 8777) and not seguranca.host_permitido(None, 8777)
    assert seguranca.origem_permitida(None, 8777) and seguranca.origem_permitida("http://127.0.0.1:8777", 8777)
    assert not seguranca.origem_permitida("http://evil.com", 8777)
    assert not seguranca.origem_permitida("null", 8777)


def test_dentro_de(tmp_path):
    assert seguranca.dentro_de(tmp_path / "a" / "b.mp4", tmp_path)
    assert not seguranca.dentro_de(tmp_path / ".." / "x", tmp_path)


def test_migra_chaves_do_config_antigo_para_o_cofre(tmp_path):
    antigo = {"gemini": {"api_key": "AIza-antiga-1234567890", "modelo": "x"},
              "youtube": {"client_id": "id", "client_secret": "GOCSPX-abc", "refresh_token": "1//tok"},
              "agendador": {"ativo": True}}
    (tmp_path / "config.json").write_text(json.dumps(antigo))
    c = cfgmod.Config(tmp_path / "config.json")
    disco = (tmp_path / "config.json").read_text()
    assert "AIza-antiga" not in disco and "GOCSPX" not in disco and "1//tok" not in disco
    assert c.get("gemini", "api_key") == "AIza-antiga-1234567890"     # continua funcionando
    assert c.get("youtube")["refresh_token"] == "1//tok"
    assert c.get("agendador", "modo") == "automatico"                  # quem postava sozinho continua
    assert any("cofre" in m for m in c.migracoes)
    c2 = cfgmod.Config(tmp_path / "config.json")                       # reabrir: nada se perde
    assert c2.get("gemini", "api_key") == "AIza-antiga-1234567890"
    pub = json.dumps(c2.publico())
    assert "AIza" not in pub and "GOCSPX" not in pub


def test_cofre_cifrado_no_windows(tmp_path):
    import sys
    c = Cofre(tmp_path / "segredos.dat")
    c.definir({"gemini.api_key": "AIza-valor-de-teste-123"})
    bruto = (tmp_path / "segredos.dat").read_bytes()
    assert b"AIza-valor" not in bruto or sys.platform != "win32"
    assert bruto.startswith(b"CXS1D:" if sys.platform == "win32" else b"CXS1P:")
    assert Cofre(tmp_path / "segredos.dat").get("gemini", "api_key") == "AIza-valor-de-teste-123"


def test_cofre_ilegivel_nao_e_sobrescrito(tmp_path):
    (tmp_path / "segredos.dat").write_bytes(b"lixo que nao abre")
    c = Cofre(tmp_path / "segredos.dat")
    assert c.erro and (tmp_path / "segredos.ilegivel").exists()


@pytest.fixture
def servidor(ambiente):
    from cortesex.servidor import Painel
    m, _ = ambiente
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    p = Painel(m, porta=porta)
    srv = p.iniciar()
    yield porta
    srv.shutdown()


def token_da_pagina(porta):
    """Pega o token anti-CSRF do jeito que o navegador pega: lendo a página servida pelo painel."""
    import re
    _, _, html = pedir(porta, "GET", "/")
    return re.search(rb'const TOKEN = "([^"]+)"', html).group(1).decode()


def pedir(porta, metodo, caminho, host=None, cab=None, corpo=None):
    con = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    h = {"Host": host or f"127.0.0.1:{porta}", **(cab or {})}
    con.request(metodo, caminho, body=json.dumps(corpo) if corpo is not None else None, headers=h)
    r = con.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_painel_bloqueia_dns_rebinding_e_csrf(servidor):
    porta = servidor
    st, cab, _ = pedir(porta, "GET", "/api/estado")
    assert st == 200 and cab["X-Frame-Options"] == "DENY" and cab["Referrer-Policy"] == "no-referrer"
    assert pedir(porta, "GET", "/api/config", host="evil.example:8777")[0] == 403
    assert pedir(porta, "POST", "/api/encerrar", corpo={})[0] == 403                     # sem o cabeçalho
    tk = token_da_pagina(porta)
    assert len(tk) >= 32 and tk != "__CORTESEX_TOKEN__"
    assert pedir(porta, "POST", "/api/config/gemini", cab={"X-CortesEX": "1"}, corpo={"modelo": "x"})[0] == 403
    assert pedir(porta, "POST", "/api/config/gemini", cab={"X-CortesEX": tk, "Origin": "http://evil.com"},
                 corpo={"modelo": "x"})[0] == 403
    assert pedir(porta, "POST", "/api/config/gemini", cab={"X-CortesEX": tk, "Origin": f"http://127.0.0.1:{porta}"},
                 corpo={"modelo": "gemini-2.5-flash"})[0] == 200
    assert pedir(porta, "POST", "/api/config/naoexiste", cab={"X-CortesEX": tk}, corpo={})[0] == 404
    assert pedir(porta, "DELETE", "/api/estado")[0] == 405
    assert pedir(porta, "GET", "/video/999")[0] == 404


def test_api_nao_devolve_chaves(servidor, ambiente):
    m, _ = ambiente
    m.config.salvar("gemini", {"api_key": "AIzaSyA1234567890abcdefghijk"})
    _, _, corpo = pedir(servidor, "GET", "/api/config")
    assert b"AIzaSy" not in corpo and json.loads(corpo)["gemini"]["api_key"] is True
