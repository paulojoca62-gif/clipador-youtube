"""Testes da auditoria independente (pós-2.0). Cada um reproduz um cenário de falha controlado.

Os antigos xfail (M1, M2, M3, L1) foram corrigidos na rodada CORRECAO_AUDITORIA_FINAL e viraram testes
comportamentais normais. Não há mais xfail no projeto.
"""
import json
import shutil
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
    (tmp_path / "backups").mkdir()
    boa = tmp_path / "backups" / "cortesex-antes-da-migracao-x.db"
    D.Banco(boa).con.close()
    with pytest.raises(D.BancoCorrompido) as e:
        D.Banco(arq, tmp_path / "backups")
    assert arq.read_bytes() == original                       # não mexeu no arquivo danificado
    assert e.value.backups == [boa]                           # oferece o backup para restaurar


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
    """AUD-M7: DPAPI falhando na migração → abre em modo degradado, chaves continuam no JSON e funcionando."""
    antigo = {"gemini": {"api_key": "AIza-nao-perder-123456", "modelo": "m"}}
    (tmp_path / "config.json").write_text(json.dumps(antigo))
    from cortesex import segredos
    monkeypatch.setattr(segredos.Cofre, "definir", lambda self, v: (_ for _ in ()).throw(OSError("DPAPI falhou")))
    c = cfgmod.Config(tmp_path / "config.json")
    assert c.cofre_falhou and c.get("gemini", "api_key") == "AIza-nao-perder-123456"
    assert json.loads((tmp_path / "config.json").read_text())["gemini"]["api_key"] == "AIza-nao-perder-123456"
    assert c.publico()["gemini"]["api_key"] is True and c.publico()["_cofre"]["erro"]
    c.salvar("gemini", {"modelo": "outro"})                  # salvar no modo degradado não apaga a chave
    assert cfgmod.Config(tmp_path / "config.json").get("gemini", "api_key") == "AIza-nao-perder-123456"


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


# ---------------------------------------------------------------- M1, M2, M3, L1 (antes xfail; agora corrigidos)
def _gravador_falso(tmp_path, monkeypatch):
    """Um 'streamlink' de mentira que manda vídeo MPEG-TS de verdade (ffmpeg) para a saída padrão."""
    import sys
    from cortesex import util
    script = tmp_path / "streamlink_falso.py"
    script.write_text(
        "import subprocess, sys\n"
        f"subprocess.run([{util.ffmpeg()!r}, '-v', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=15',"
        " '-f', 'lavfi', '-i', 'sine=frequency=440', '-c:v', 'libx264', '-preset', 'ultrafast', '-g', '15',"
        " '-c:a', 'aac', '-f', 'mpegts', 'pipe:1'], stdout=sys.stdout.buffer)\n")
    monkeypatch.setattr(util, "comando_streamlink", lambda: [sys.executable, str(script)])


def test_disco_baixo_interrompe_gravacao_em_andamento(tmp_path, monkeypatch):
    """AUD-M1: com a gravação rodando, o disco 'acaba' → a gravação para e os processos fecham."""
    from cortesex.gravador import Canal
    _gravador_falso(tmp_path, monkeypatch)
    cfg = cfgmod.Config(tmp_path / "config.json")
    espaco = {"ok": True}
    canal = Canal({"nome": "teste"}, cfg, tmp_path, lambda job: None, pode_gravar=lambda: espaco["ok"])
    def gravar():
        try:
            canal._gravar_uma_vez("best")
        except RuntimeError:
            pass                                               # sessão curta: o gravador avisa "sem live agora"
    t = threading.Thread(target=gravar, daemon=True)
    t.start()
    for _ in range(200):
        if len(canal.segmentos()) >= 2:
            break
        time.sleep(0.1)
    assert len(canal.segmentos()) >= 2, "a gravação de teste não começou"
    espaco["ok"] = False
    t.join(timeout=15)
    assert not t.is_alive(), "a gravação continuou com pouco disco"
    assert canal._fluxo.poll() is not None and canal._ff.poll() is not None
    n = len(canal.segmentos())
    time.sleep(5)
    assert len(canal.segmentos()) <= n + 1                     # nada mais está sendo escrito


def test_remontar_recusa_corte_em_processamento(ambiente):
    """AUD-M2: o editor não remonta um corte que o pipeline está processando."""
    m, pasta = ambiente
    cid = m.banco.novo_candidato("a", pasta / "x.mp4", time.time(), 3, "p", [], time.time(), time.time())
    m.banco.mudar_estado(cid, D.MONTANDO)
    (pasta / "x_fonte.mp4").write_bytes(b"x")
    with pytest.raises(ValueError, match="montando"):
        m.remontar(cid)
    assert m.banco.corte(cid)["estado"] == D.MONTANDO


def test_editor_reserva_o_corte_enquanto_monta(real_pipeline):
    """AUD-M2: durante a remontagem o corte fica 'montando' (o pipeline não o pega) e volta ao estado de antes."""
    m, cid = real_pipeline
    m.banco.aprovar(cid)
    vistos = []
    original = m._montar

    def espiao(c, *a, **k):
        vistos.append(m.banco.corte(cid)["estado"])
        vistos.append(m.processar_proximo())                  # um worker tentando pegar algo nesse momento
        return original(c, *a, **k)
    m._montar = espiao
    m.remontar(cid, segmentos=[{"ini": 0, "fim": 2, "texto": "EDITADO"}])
    assert vistos == [D.MONTANDO, False]
    assert m.banco.corte(cid)["estado"] == D.APROVADO


def test_editor_falha_mantem_estado_e_video(real_pipeline, monkeypatch):
    m, cid = real_pipeline
    antes = m.banco.corte(cid)
    video = open(antes["arquivo"], "rb").read()
    monkeypatch.setattr(m.render, "montar", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg caiu")))
    with pytest.raises(RuntimeError):
        m.remontar(cid, segmentos=[{"ini": 0, "fim": 2, "texto": "X"}])
    assert m.banco.corte(cid)["estado"] == antes["estado"]
    assert open(antes["arquivo"], "rb").read() == video


def test_limpeza_do_buffer_protege_candidatos_pendentes(ambiente):
    """AUD-M3: blocos de um corte detectado e ainda não capturado não são apagados pela limpeza."""
    from cortesex.gravador import FORMATO_BLOCO, Canal
    m, pasta = ambiente
    canal = Canal({"nome": "alanzoka"}, m.config, pasta, m.agendar_corte)
    m.canais["alanzoka"] = canal
    velho = time.time() - 40 * 60                               # 40 min atrás (buffer = 15 min)
    for i in range(10):
        (canal.dir_seg / (time.strftime(FORMATO_BLOCO, time.localtime(velho + 4 * i)) + ".ts")).write_bytes(b"x")
    cid = m.banco.novo_candidato("alanzoka", pasta / "c.mp4", velho + 20, 3, "p", [], velho, velho + 36)
    m.config.salvar("armazenamento", {"minimo_livre_gb": 0})
    m.limpar()
    assert len(canal.segmentos()) == 10                         # protegidos
    m.banco.mudar_estado(cid, D.CAPTURADO)
    m.limpar()
    assert len(canal.segmentos()) == 0                          # capturado: podem sair


def test_permissao_editar_e_aplicada(real_pipeline):
    """AUD-L1: autorização com editar=False bloqueia o editor."""
    m, cid = real_pipeline
    autorizar(m, editar=False)
    with pytest.raises(ValueError, match="editar"):
        m.remontar(cid, segmentos=[{"ini": 0, "fim": 2, "texto": "X"}])


@pytest.fixture
def real_pipeline(tmp_path, monkeypatch):
    from test_pipeline import gravar_blocos
    from cortesex import midia
    monkeypatch.setenv("CORTESEX_DADOS", str(tmp_path))
    c = cfgmod.Config(tmp_path / "config.json")
    c.salvar("corte", {"adaptativo": True, "duracao_min": 10, "duracao_max": 30})
    m = motormod.Motor(config=c, pasta=tmp_path)
    falas = [(1.0, 1.5, "Olha"), (6.0, 7.0, "isso."), (18.0, 19.0, "Meu"), (19.1, 19.6, "Deus!"), (31.0, 31.5, "Fim.")]
    monkeypatch.setattr(m.render, "transcrever",
                        lambda fonte, nome="small": (falas, midia.perfil_audio(midia.ler_audio(fonte))))
    inicio = time.time() - 200
    gravar_blocos(tmp_path, "alanzoka", inicio, 40)
    cid = m.banco.novo_candidato("alanzoka", tmp_path / "cortes" / "alanzoka" / "e.mp4", inicio + 20, 3.0, "p",
                                 [], inicio, inicio + 36)
    while m.processar_proximo(agora=time.time()):
        pass
    assert m.banco.corte(cid)["estado"] == D.REVISAO
    return m, cid


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
