"""Testes da auditoria independente (pós-2.0). Cada um reproduz um cenário de falha controlado.

Os marcados xfail(strict=True) documentam problemas MÉDIOS/BAIXOS registrados e ainda não corrigidos:
se alguém corrigir, o teste passa a falhar por "XPASS" e precisa virar teste normal.
"""
import json
import shutil
import sqlite3
import threading
import time

import pytest

from cortesex import config as cfgmod
from cortesex import db as D
from cortesex import motor as motormod
from cortesex.plataformas import resultado
from conftest import autorizar, corte


# ---------------------------------------------------------------- publicação / idempotência
def test_corrida_nao_publica_duas_vezes(ambiente, monkeypatch):
    """Agendador e 'Postar agora' (ou dois cliques) no mesmo corte e rede ao mesmo tempo."""
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    autorizar(m)
    cid = corte(m, pasta, 3.0)
    chamadas = []

    def lento(cfg, c):
        chamadas.append(1)
        time.sleep(0.5)
        return resultado("https://youtu.be/x", "x")
    monkeypatch.setattr(motormod.youtube, "postar", lento)
    original = motormod.autorizado_para

    def autorizacao_lenta(*a, **k):          # alarga a janela entre "já postei?" e "registrar o envio"
        time.sleep(0.2)
        return original(*a, **k)
    monkeypatch.setattr(motormod, "autorizado_para", autorizacao_lenta)
    erros = []

    def tentar():
        try:
            m.postar(m.banco.corte(cid), "youtube", origem="manual")
        except Exception as e:  # noqa: BLE001
            erros.append(str(e))
    ts = [threading.Thread(target=tentar) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(chamadas) == 1, f"publicou {len(chamadas)} vezes"
    assert len(erros) == 2 and all("já está" in e for e in erros)


def test_dois_workers_nao_processam_o_mesmo_corte(ambiente, monkeypatch):
    m, pasta = ambiente
    cid = m.banco.novo_candidato("a", pasta / "cortes" / "a" / "x.mp4", time.time() - 300, 3.0, "p", [],
                                 time.time() - 360, time.time() - 270)
    vezes = []

    def captura_lenta(*a, **k):
        vezes.append(1)
        time.sleep(0.4)
        raise motormod.ErroEtapa("sem buffer", definitivo=True)
    monkeypatch.setattr(motormod, "capturar", captura_lenta)
    res = []
    ts = [threading.Thread(target=lambda: res.append(m.processar_proximo())) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(vezes) == 1 and res.count(True) == 1
    assert m.banco.corte(cid)["estado"] == D.FALHOU


def test_pausa_e_incerto_sobrevivem_ao_reinicio(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"ativo": True, "modo": "automatico", "plataformas": {"youtube": True}})
    autorizar(m)
    a, b = corte(m, pasta, 3.0), corte(m, pasta, 3.5)
    m.banco.nova_postagem(a, "youtube")                       # interrompido
    m.banco.pausar("youtube", time.time() + 3600, "limite", "cota", 1)
    m2 = motormod.Motor(config=m.config, pasta=pasta)
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: pytest.fail("publicou após reinício"))
    assert m2.rodar_agendador() == []                          # pausa persistida
    m2.retomar("youtube")
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: resultado("u", "v"))
    assert m2.rodar_agendador() == [("youtube", b)]            # o incerto (a) não volta sozinho


# ---------------------------------------------------------------- autorização
def test_mudanca_de_autorizacao_vale_para_cortes_ja_aprovados(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"ativo": True, "modo": "semiautomatico", "plataformas": {"youtube": True}})
    autorizar(m)
    cid = corte(m, pasta, 3.0)
    m.banco.aprovar(cid)
    assert m.candidatos("youtube")[0]
    autorizar(m, status="revogada")
    bons, fora = m.candidatos("youtube")
    assert not bons and "revogada" in fora[0]["_fora"]


def test_autorizacao_de_gravar_nao_e_de_publicar(ambiente):
    m, pasta = ambiente
    canais = [{"nome": "alanzoka", "autorizacao": {"status": "autorizado", "gravar": True, "publicar": False,
                                                    "redes": ["youtube"]}}]
    m.config.salvar("canais", canais)
    ok, motivo = motormod.autorizado_para(m.config.canal("alanzoka"), "youtube")
    assert not ok and "publicar" in motivo


# ---------------------------------------------------------------- pipeline / recuperação
def test_falha_do_whisper_nao_impede_o_corte(tmp_path, monkeypatch):
    """1.x: sem Whisper o corte saía sem legenda. A 2.0 não pode perder o corte inteiro."""
    from test_pipeline import gravar_blocos
    monkeypatch.setenv("CORTESEX_DADOS", str(tmp_path))
    c = cfgmod.Config(tmp_path / "config.json")
    m = motormod.Motor(config=c, pasta=tmp_path)

    def quebrado(fonte, nome="small"):
        raise RuntimeError("não consegui baixar o modelo do Whisper")
    monkeypatch.setattr(m.render, "transcrever", quebrado)
    inicio = time.time() - 200
    gravar_blocos(tmp_path, "alanzoka", inicio, 40)
    cid = m.banco.novo_candidato("alanzoka", tmp_path / "cortes" / "alanzoka" / "w.mp4", inicio + 20, 3.0, "p",
                                 [], inicio, inicio + 36)
    agora = time.time()
    for _ in range(12):
        if not m.processar_proximo(agora=agora):
            agora += 700
    cfinal = m.banco.corte(cid)
    assert cfinal["estado"] == D.REVISAO, cfinal["erro"]
    assert "sem_transcricao" in cfinal["flags"]


def test_banco_corrompido_nao_e_sobrescrito(tmp_path):
    arq = tmp_path / "cortesex.db"
    arq.write_bytes(b"isto nao e um banco sqlite" * 100)
    original = arq.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        D.Banco(arq, tmp_path / "backups")
    assert arq.read_bytes() == original


def test_backup_de_atualizacao_restaura(tmp_path, monkeypatch):
    from cortesex import atualizacao
    c = cfgmod.Config(tmp_path / "config.json")
    c.salvar("gemini", {"api_key": "AIza-restaurar-123456789"})
    c.salvar("canais", [{"nome": "alanzoka"}])
    b = D.Banco(tmp_path / "cortesex.db")
    b.novo_corte("alanzoka", tmp_path / "a.mp4", time.time(), 30, 3.0, "p", [], "f", "T")
    b.con.close()
    monkeypatch.setattr(atualizacao, "VERSAO", "9.0.0")
    copia = atualizacao.backup_se_mudou(tmp_path)
    for f in ("config.json", "segredos.dat", "cortesex.db"):         # "perde" tudo
        (tmp_path / f).unlink()
    for f in copia.iterdir():                                        # restaura copiando de volta
        shutil.copy2(f, tmp_path / f.name)
    c2 = cfgmod.Config(tmp_path / "config.json")
    assert c2.get("gemini", "api_key") == "AIza-restaurar-123456789"
    assert c2.canal("alanzoka") and D.Banco(tmp_path / "cortesex.db").corte(1)["titulo"] == "T"


def test_falha_no_cofre_durante_migracao_nao_perde_chaves(tmp_path, monkeypatch):
    antigo = {"gemini": {"api_key": "AIza-nao-perder-123456", "modelo": "m"}}
    (tmp_path / "config.json").write_text(json.dumps(antigo))
    from cortesex import segredos
    monkeypatch.setattr(segredos.Cofre, "definir", lambda self, v: (_ for _ in ()).throw(OSError("DPAPI falhou")))
    with pytest.raises(OSError):
        cfgmod.Config(tmp_path / "config.json")
    assert json.loads((tmp_path / "config.json").read_text()) == antigo


# ---------------------------------------------------------------- segurança
def test_video_fora_da_pasta_nao_e_servido(servidor_e_motor):
    porta, m, pasta = servidor_e_motor
    fora = pasta.parent / f"segredo_{time.time_ns()}.mp4"
    fora.write_bytes(b"x" * 100)
    cid = m.banco.novo_corte("a", fora, time.time(), 1, 1, "p", [], "", "t")
    from test_seguranca import pedir
    assert pedir(porta, "GET", f"/video/{cid}")[0] == 404
    fora.unlink()


def test_callback_oauth_sem_state_valido_nao_troca_codigo(servidor_e_motor, monkeypatch):
    porta, m, _ = servidor_e_motor
    monkeypatch.setattr(motormod.youtube if False else __import__("cortesex.plataformas.youtube", fromlist=["x"]),
                        "trocar_codigo", lambda *a: pytest.fail("trocou código de login forjado"))
    from test_seguranca import pedir
    for estado in ("", "youtube", "youtube-forjado", "tiktok-forjado"):
        st, cab, _ = pedir(porta, "GET", f"/?code=CODIGO&state={estado}")
        assert st == 303 and "erro=" in cab["Location"]
    assert not m.config.get("youtube", "refresh_token")


@pytest.fixture
def servidor_e_motor(ambiente):
    import socket
    from cortesex.servidor import Painel
    m, pasta = ambiente
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    srv = Painel(m, porta=porta).iniciar()
    yield porta, m, pasta
    srv.shutdown()


# ---------------------------------------------------------------- problemas registrados (não corrigidos)
@pytest.mark.xfail(strict=True, reason="AUD-M1: disco baixo só impede NOVAS gravações; a gravação em andamento continua")
def test_disco_baixo_interrompe_gravacao_em_andamento():
    import inspect
    from cortesex import gravador
    fonte = inspect.getsource(gravador.Canal._gravar_uma_vez)
    assert "pode_gravar" in fonte


@pytest.mark.xfail(strict=True, reason="AUD-M2: o editor aceita remontar um corte que o pipeline ainda está processando")
def test_remontar_recusa_corte_em_processamento(ambiente):
    m, pasta = ambiente
    cid = m.banco.novo_candidato("a", pasta / "x.mp4", time.time(), 3, "p", [], time.time(), time.time())
    m.banco.mudar_estado(cid, D.MONTANDO)
    (pasta / "x_fonte.mp4").write_bytes(b"x")
    with pytest.raises(ValueError):
        m.remontar(cid)


@pytest.mark.xfail(strict=True, reason="AUD-M3: a limpeza do buffer não protege blocos de cortes detectados ainda não capturados")
def test_limpeza_do_buffer_protege_candidatos_pendentes(ambiente):
    import inspect
    from cortesex import gravador
    assert "protegido" in inspect.getsource(gravador.Canal.limpar)


@pytest.mark.xfail(strict=True, reason="AUD-L1: a permissão 'editar' é registrada mas não bloqueia o editor")
def test_permissao_editar_e_aplicada(ambiente):
    import inspect
    assert "editar" in inspect.getsource(motormod.Motor.remontar)


def test_processo_de_gravacao_morre_junto_com_o_programa(tmp_path):
    """AUD-H3: matar o CortesEX-PRESS à força não pode deixar streamlink/ffmpeg gravando sozinhos."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    script = tmp_path / "pai.py"
    script.write_text(
        "import sys, time, threading\n"
        f"sys.path.insert(0, {str(raiz)!r})\n"
        "from cortesex import util\n"
        "def gravar():\n"
        "    p = util.popen_vinculado([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "    print(p.pid, flush=True)\n"
        "    time.sleep(60)\n"
        "threading.Thread(target=gravar, daemon=True).start()\n"
        "time.sleep(60)\n")
    pai = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    filho = int(pai.stdout.readline())

    def vivo(pid):
        if sys.platform == "win32":
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
            return str(pid) in r.stdout
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        try:   # zumbi ainda não recolhido conta como morto
            return open(f"/proc/{pid}/stat").read().split()[2] != "Z"
        except OSError:
            return False
    assert vivo(filho)
    pai.kill()                                  # “Finalizar tarefa” / queda
    pai.wait()
    for _ in range(50):
        if not vivo(filho):
            break
        time.sleep(0.1)
    morto = not vivo(filho)
    if not morto:
        os.kill(filho, 9)
    assert morto, "o processo filho continuou vivo (órfão)"


@pytest.mark.parametrize("como", ["erro_marcado", "excecao_de_rede"])
def test_conexao_perdida_no_envio_vira_incerto_e_nao_repete(ambiente, monkeypatch, como):
    """AUD-H4: resposta perdida depois do upload completo não pode levar a uma segunda publicação."""
    import requests
    from cortesex.plataformas import ErroPostagem
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"ativo": True, "modo": "automatico", "quantidade": 60,
                                  "plataformas": {"youtube": True}})
    autorizar(m)
    cid = corte(m, pasta, 3.0)

    def cai(cfg, c):
        if como == "erro_marcado":
            raise ErroPostagem("YouTube: a conexão caiu durante o envio", True, "temporario", incerto=True)
        raise requests.ConnectionError("reset")
    monkeypatch.setattr(motormod.youtube, "postar", cai)
    assert m.rodar_agendador() == []
    assert m.banco.postagens_de(cid)[0]["status"] == "incerto"
    m.retomar("youtube")
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: pytest.fail("repostou após envio incerto"))
    assert m.rodar_agendador(time.time() + 3600) == []
