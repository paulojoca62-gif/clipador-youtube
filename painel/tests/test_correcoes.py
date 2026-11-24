"""Testes comportamentais da rodada de correções pós-auditoria (CORRECAO_AUDITORIA_FINAL.md)."""
import http.client
import json
import multiprocessing as mp
import os
import shutil
import sys
import socket
import subprocess
import threading
import time

import requests
from pathlib import Path

import pytest

from cortesex import db as D
from cortesex import editorial, ia, recuperacao, seguranca
from cortesex import motor as motormod
from cortesex.plataformas import facebook, resultado, tiktok
from conftest import autorizar, corte

RAIZ = Path(__file__).resolve().parents[1]


# ================================================================ painel: Host, CSRF, métodos, corpo
@pytest.fixture
def painel(ambiente):
    from cortesex.servidor import Painel
    m, pasta = ambiente
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    p = Painel(m, porta=porta)
    srv = p.iniciar()
    yield porta, p, m, pasta
    srv.shutdown()


def bruto(porta, texto: bytes) -> int:
    """Manda bytes HTTP crus (cabeçalhos duplicados, Content-Length estranho) e devolve o status."""
    with socket.create_connection(("127.0.0.1", porta), timeout=10) as s:
        s.sendall(texto)
        resp = b""
        while b"\r\n" not in resp:
            parte = s.recv(4096)
            if not parte:
                break
            resp += parte
    return int(resp.split(b" ")[1]) if resp else 0


def pedir(porta, metodo, caminho, host=None, cab=None, corpo=None, raw=None):
    con = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    h = {"Host": host if host is not None else f"127.0.0.1:{porta}", **(cab or {})}
    body = raw if raw is not None else (json.dumps(corpo) if corpo is not None else None)
    con.request(metodo, caminho, body=body, headers=h)
    r = con.getresponse()
    return r.status, dict(r.getheaders()), r.read()


@pytest.mark.parametrize("host", ["evil.com:{p}", "127.0.0.1:{p}.", "127.1:{p}", "0.0.0.0:{p}", "[::1]:{p}",
                                  "2130706433:{p}", "127.0.0.1:1", "127.0.0.1", "LOCALHOST:{p}x", "",
                                  "user@127.0.0.1:{p}", "127.0.0.1:{p} evil", "127.0.0.1:0{p}"])
def test_host_recusado(painel, host):
    porta = painel[0]
    assert pedir(porta, "GET", "/api/estado", host=host.format(p=porta))[0] == 403


def test_host_aceito_e_maiusculas(painel):
    porta = painel[0]
    assert pedir(porta, "GET", "/api/estado", host=f"localhost:{porta}")[0] == 200
    assert pedir(porta, "GET", "/api/estado", host=f"LocalHost:{porta}")[0] == 200


def test_host_duplicado_recusado(painel):
    porta = painel[0]
    pedido = (f"GET /api/estado HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\nHost: evil.com\r\n\r\n").encode()
    assert bruto(porta, pedido) == 403


def test_csrf_token_obrigatorio_e_cross_site(painel):
    porta, p, m, _ = painel
    ok = {"X-CortesEX": p.token, "Content-Type": "application/json"}
    assert pedir(porta, "POST", "/api/config/gemini", cab={"Content-Type": "application/json"}, corpo={})[0] == 403
    assert pedir(porta, "POST", "/api/config/gemini", cab={**ok, "X-CortesEX": p.token[:-1] + "x"}, corpo={})[0] == 403
    assert pedir(porta, "POST", "/api/config/gemini", cab={**ok, "Sec-Fetch-Site": "cross-site"}, corpo={})[0] == 403
    assert pedir(porta, "POST", "/api/config/gemini", cab={**ok, "Origin": "null"}, corpo={})[0] == 403
    assert pedir(porta, "POST", "/api/config/gemini", cab={**ok, "Sec-Fetch-Site": "same-origin"},
                 corpo={"modelo": "gemini-2.5-flash"})[0] == 200
    assert m.config.get("gemini", "modelo") == "gemini-2.5-flash"
    # GET nunca altera nada: estado igual antes e depois de "chamar" uma ação por GET
    assert pedir(porta, "GET", "/api/encerrar")[0] == 404


def test_corpo_e_metodos(painel):
    porta, p, _, _ = painel
    ok = {"X-CortesEX": p.token, "Content-Type": "application/json"}
    assert pedir(porta, "POST", "/api/config/gemini", cab=ok, raw="não é json")[0] == 400
    assert pedir(porta, "POST", "/api/config/gemini", cab=ok, raw="[1, 2]")[0] == 400
    assert pedir(porta, "POST", "/api/config/gemini", cab={**ok, "Content-Type": "text/plain"}, corpo={})[0] == 415
    for metodo in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"):
        assert pedir(porta, metodo, "/api/estado")[0] == 405
    base = f"POST /api/config/gemini HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\nX-CortesEX: {p.token}\r\n"
    assert bruto(porta, (base + "Content-Length: -5\r\n\r\n").encode()) == 400
    assert bruto(porta, (base + "Content-Length: abc\r\n\r\n").encode()) == 400
    assert bruto(porta, (base + "Content-Length: 99999999\r\n\r\n").encode()) == 413
    assert bruto(porta, (base + "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n").encode()) == 411
    st, cab, _ = pedir(porta, "GET", "/api/estado")
    assert st == 200 and "camera=()" in cab["Permissions-Policy"] and cab["Cache-Control"] == "no-store"


def test_symlink_para_fora_nao_e_servido(painel):
    porta, _, m, pasta = painel
    fora = pasta.parent / f"segredo_{time.time_ns()}.mp4"
    fora.write_bytes(b"x" * 200)
    link = pasta / "cortes" / "link.mp4"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(fora)
    except (OSError, NotImplementedError):
        pytest.skip("este sistema não deixa criar symlink sem privilégio")
    cid = m.banco.novo_corte("a", link, time.time(), 1, 1, "p", [], "", "t")
    assert pedir(porta, "GET", f"/video/{cid}")[0] == 404
    for ruim in ("/video/../config.json", "/video/1%2F..%2F..", "/video/-1", "/video/1.mp4"):
        assert pedir(porta, "GET", ruim)[0] == 404
    fora.unlink()


# ================================================================ XSS no navegador de verdade
def _node_playwright():
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not _node_playwright(), reason="precisa de node + playwright (roda no Linux de desenvolvimento)")
def test_xss_nao_executa_no_painel(painel, tmp_path):
    """Títulos, legendas, chat, fala, erros, hashtags e URLs maliciosos são exibidos como texto, nunca executados."""
    porta, _, m, pasta = painel
    payloads = ['<img src=x onerror="window.__xss=1">', '"><script>window.__xss=2</script>',
                "<svg onload=window.__xss=3>", "';window.__xss=4;'", "</textarea><img src=x onerror=window.__xss=5>",
                "javascript:window.__xss=6"]
    for i, p in enumerate(payloads):
        cid = m.banco.novo_corte("canal" + str(i), pasta / f"x{i}.mp4", time.time(), 30, 3.0, p, [p, p], p,
                                 titulo=p, legenda=p, hashtags=[p])
        m.banco.atualizar(cid, erro=p, analise={"resumo": p}, flags=[p], categoria=p)
        pid = m.banco.nova_postagem(cid, "youtube")
        m.banco.fim_postagem(pid, "postado", url="javascript:window.__xss=7", erro=p)
    m.banco.evento("error", payloads[0])
    script = tmp_path / "xss.js"
    script.write_text("""
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  const dialogos = []; p.on('dialog', d => { dialogos.push(d.message()); d.dismiss(); });
  const url = 'http://127.0.0.1:%d/';
  for (const aba of ['cortes', 'fila', 'publicacoes', 'diagnostico']) {
    await p.goto(url + '#' + aba); await p.waitForTimeout(1500);
    if (aba === 'cortes') { await p.selectOption('#f-estado', ''); await p.waitForTimeout(1200);
      const n = await p.locator('#lista-cortes .corte').count(); if (n < 6) throw new Error('cortes não apareceram: ' + n);
      const btn = p.locator('text=Histórico').first(); await btn.click(); await p.waitForTimeout(500); }
  }
  const xss = await p.evaluate(() => window.__xss);
  const links = await p.$$eval('a[href^="javascript"]', a => a.length);
  const texto = await p.content();
  console.log(JSON.stringify({xss: xss || null, dialogos, links, aparece: texto.includes('&lt;img src=x onerror')}));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
""" % porta)
    env = {**os.environ, "NO_PROXY": "*", "no_proxy": "*"}
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=120, env=env, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-800:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["xss"] is None and res["dialogos"] == [] and res["links"] == 0
    assert res["aparece"]                       # o texto malicioso aparece escapado, como texto


# ================================================================ segredos e redação
@pytest.mark.parametrize("texto,vazamento", [
    ('POST /token client_id=abc.apps.googleusercontent.com&client_secret=GOCSPX-AbCdEf123456&code=4/0AeanS0Zsecret', ["GOCSPX-AbCdEf", "4/0AeanS0Z"]),
    ('{"access_token": "ya29.a0AfB_byC123", "refresh_token": "1//09abcdefghijklmnopqrstuvwxyz", "id_token": "eyJhbGciOi.payload.sig"}', ["ya29.a0AfB", "1//09abcdef", "eyJhbGciOi"]),
    ("Authorization: Bearer ya29.a0AfB_byC123456789", ["ya29.a0AfB"]),
    ("x-goog-api-key: AIzaSyD-1234567890abcdefghijklmno", ["AIzaSyD-123"]),
    ("https://generativelanguage.googleapis.com/v1beta/models?key=AIzaSyD-1234567890abcdefghijk", ["AIzaSyD-123"]),
    ("HTTPSConnectionPool: /bot1234567890:AAH-abcdefghijklmnopqrstuvwxyz12/sendMessage", ["AAH-abcdefgh"]),
    ("graph.facebook.com/v26.0/me/accounts?access_token=EAAGm0PX4ZCpsBAKZCZBabcdefghij&fields=id", ["EAAGm0PX4"]),
    ('{"access_token":"act.example12345abcdefghijklmn!4321","refresh_token":"rft.example98765zyxw"}', ["act.example123", "rft.example987"]),
    ("Authorization: OAuth EAAGm0PX4ZCpsBAKZCZBabcdefghij", ["EAAGm0PX4"]),
    ("streamlink https://usher.ttvnw.net/api/channel/hls/x.m3u8?sig=0123456789abcdef&token=%7B%22a%22%7D", ["0123456789abcdef", "%7B%22a"]),
])
def test_redacao_de_formatos_reais(texto, vazamento):
    out = seguranca.redigir(texto)
    for v in vazamento:
        assert v not in out, out


def test_eventos_antigos_sao_redigidos_na_migracao(tmp_path):
    b = D.Banco(tmp_path / "x.db")
    b.con.execute("DELETE FROM meta WHERE chave='eventos_redigidos'")
    b.con.execute("INSERT INTO eventos (quando, nivel, texto) VALUES (1, 'warning', "
                  "'Telegram: /bot1234567890:AAH-abcdefghijklmnopqrstuvwxyz12/send')")
    b.con.commit()
    b.con.close()
    b2 = D.Banco(tmp_path / "x.db")
    assert "AAH-abcdef" not in json.dumps(b2.eventos())


# ================================================================ publicação: reserva entre processos
def _reservar(arquivo, fila):
    b = D.Banco(Path(arquivo))
    try:
        b.nova_postagem(1, "youtube")
        fila.put("ok")
    except D.PostagemDuplicada:
        fila.put("duplicada")


def test_reserva_atomica_entre_dois_processos(tmp_path):
    b = D.Banco(tmp_path / "c.db")
    b.novo_corte("a", tmp_path / "a.mp4", time.time(), 1, 3, "p", [], "", "T")
    b.con.close()
    ctx = mp.get_context("spawn")
    fila = ctx.Queue()
    ps = [ctx.Process(target=_reservar, args=(str(tmp_path / "c.db"), fila)) for _ in range(4)]
    [p.start() for p in ps]
    [p.join(60) for p in ps]
    res = sorted(fila.get(timeout=5) for _ in ps)
    assert res.count("ok") == 1 and res.count("duplicada") == 3


def test_repostar_de_proposito_continua_possivel(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    autorizar(m)
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: resultado("https://youtu.be/a", "a"))
    cid = corte(m, pasta, 3.0)
    m.postar(m.banco.corte(cid), "youtube", origem="manual")
    m.postar(m.banco.corte(cid), "youtube", origem="manual", forcar=True)
    assert [p["status"] for p in m.banco.postagens_de(cid)] == ["postado", "postado"]


def test_banco_falha_depois_da_confirmacao_vira_incerto(ambiente, monkeypatch):
    """A rede confirmou, mas gravar o resultado falhou (queda): ao reabrir, o envio é 'incerto', nunca repetido."""
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    m.config.salvar("agendador", {"ativo": True, "modo": "automatico", "quantidade": 60, "plataformas": {"youtube": True}})
    autorizar(m)
    cid = corte(m, pasta, 3.0)
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: resultado("https://youtu.be/a", "a"))
    original = m.banco.fim_postagem
    monkeypatch.setattr(m.banco, "fim_postagem", lambda *a, **k: (_ for _ in ()).throw(OSError("disco cheio")))
    with pytest.raises(OSError):
        m.postar(m.banco.corte(cid), "youtube")
    monkeypatch.setattr(m.banco, "fim_postagem", original)
    m2 = motormod.Motor(config=m.config, pasta=pasta)
    assert m2.banco.postagens_de(cid)[0]["status"] == "incerto"
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: pytest.fail("repostou"))
    assert m2.rodar_agendador(time.time() + 7200) == []


def _reivindicar(arquivo, fila):
    b = D.Banco(Path(arquivo))
    fila.put(b.reivindicar(1, D.DETECTADO, D.CAPTURANDO))


def test_reivindicacao_atomica_entre_processos(tmp_path):
    b = D.Banco(tmp_path / "r.db")
    b.novo_candidato("a", tmp_path / "a.mp4", time.time(), 3, "p", [], 0, 0)
    b.con.close()
    ctx = mp.get_context("spawn")
    fila = ctx.Queue()
    ps = [ctx.Process(target=_reivindicar, args=(str(tmp_path / "r.db"), fila)) for _ in range(4)]
    [p.start() for p in ps]
    [p.join(60) for p in ps]
    assert sorted(fila.get(timeout=5) for _ in ps) == [False, False, False, True]


def test_verificacao_do_tiktok_renova_token(ambiente, monkeypatch):
    """AUD-M8."""
    m, pasta = ambiente
    m.config.salvar("tiktok", {"refresh_token": "r", "access_token": "velho", "expira_em": 0})
    autorizar(m)
    cid = corte(m, pasta, 3.0)
    pid = m.banco.nova_postagem(cid, "tiktok")
    m.banco.fim_postagem(pid, "processando", ext_id="pub1")
    monkeypatch.setattr(motormod.tiktok, "renovar", lambda cfg: {"access_token": "novo", "expira_em": time.time() + 999})
    usados = []
    monkeypatch.setattr(motormod.tiktok, "verificar",
                        lambda cfg, ext: usados.append(cfg["access_token"]) or resultado("u", ext, "postado"))
    assert m.verificar_processando() == 1 and usados == ["novo"]


# ================================================================ pipeline: IA fora dos workers de mídia
def test_worker_de_midia_nao_pega_etapa_da_ia(ambiente):
    """AUD-M4: worker de mídia ignora cortes 'montado'; o worker da IA ignora as etapas de mídia."""
    m, pasta = ambiente
    a = m.banco.novo_candidato("a", pasta / "a.mp4", time.time() - 999, 3, "p", [], time.time() - 999, time.time() - 999)
    m.banco.mudar_estado(a, D.MONTADO)
    assert m.processar_proximo(etapas=m.ETAPAS_MIDIA) is False
    assert m.banco.corte(a)["estado"] == D.MONTADO
    assert m.processar_proximo(etapas=m.ETAPAS_IA) is True
    assert m.banco.corte(a)["estado"] == D.REVISAO


def test_ia_respeita_orcamento_total_de_tempo():
    agora = {"t": 0.0}

    def lento(*a, **k):
        agora["t"] += 40                                     # cada tentativa "demora" 40 s
        raise ia.requests.Timeout()
    with pytest.raises(RuntimeError, match="tempo máximo"):
        ia.analisar_e_escrever("c", "f", [], "k", post=lento, dormir=lambda s: agora.__setitem__("t", agora["t"] + s),
                               relogio=lambda: agora["t"])
    assert agora["t"] <= ia.ORCAMENTO_TOTAL + 40 + 30         # no máximo uma tentativa além do limite


def test_editor_nao_perde_textos_editados_se_cair(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("gemini", {"api_key": "k"})
    cid = m.banco.novo_candidato("a", pasta / "a.mp4", time.time(), 3, "p", [], 0, 0)
    m.banco.atualizar_textos(cid, "MEU TÍTULO", "minha legenda", ["#meu"])
    m.banco.mudar_estado(cid, D.MONTADO)                     # voltou ao pipeline depois de uma queda
    monkeypatch.setattr(motormod.ia, "analisar_e_escrever", lambda *a, **k: pytest.fail("chamou a IA"))
    assert m.processar_proximo(etapas=m.ETAPAS_IA)
    assert m.banco.corte(cid)["titulo"] == "MEU TÍTULO"


def test_pico_durante_intervalo_minimo_estende_o_corte_aberto(ambiente):
    """AUD-L2: o agrupamento agora age também nos picos que caem no intervalo mínimo."""
    from cortesex.gravador import Canal
    m, pasta = ambiente
    canal = Canal({"nome": "alanzoka"}, m.config, pasta, m.agendar_corte)
    t0 = time.time()
    canal.inicio_chat = t0 - 600
    canal.disparar("chat 3x", 3.0, agora=t0)
    canal.disparar("chat 5x", 5.0, agora=t0 + 10)            # dentro dos 75 s de intervalo
    canal.disparar("chat 4x", 4.0, agora=t0 + 200)           # longe: corte novo
    cortes = m.banco.biblioteca(estado="detectado", ordenar="recente")
    assert len(cortes) == 2
    agrupado = [c for c in cortes if abs(c["momento"] - t0) < 1][0]
    assert agrupado["score"] == 5.0 and "chat 5x" in agrupado["motivo"]


# ================================================================ nota editorial
def test_nota_com_valores_invalidos_e_extremos():
    todos = {k: 1.0 for k in editorial.PESOS_PADRAO}
    assert editorial.pontuar(todos) == {"score": 100.0, "confianca": 1.0}
    assert editorial.pontuar({k: 0.0 for k in editorial.PESOS_PADRAO}) == {"score": 0.0, "confianca": 1.0}
    assert editorial.pontuar({}) == {"score": None, "confianca": 0.0}
    r = editorial.pontuar({"chat": 7.0, "audio": -3, "contexto": float("nan"), "reacao": "abc", "outro": 1.0})
    assert r["score"] == pytest.approx((20 * 1.0 + 10 * 0.0) / 30 * 100, abs=0.1)   # limitado a 0–1; NaN/texto = sem dado
    assert r["confianca"] == 0.3
    assert editorial.pontuar(todos, {"chat": -5, "audio": "x", "reacao": 0}) == {"score": None, "confianca": 0.0}
    assert editorial.pontuar({"chat": 1.0}, {"chat": 10, "inventado": 90})["confianca"] == 1.0


def test_ranking_nao_deixa_nota_parcial_furar_a_fila(ambiente):
    """AUD-M5: nota 95 com 20% de cobertura não passa à frente de nota 70 com 90%."""
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    autorizar(m)
    parcial, completo = corte(m, pasta, 3.0), corte(m, pasta, 3.0)
    m.banco.atualizar(parcial, score_editorial=95, confianca=0.2)
    m.banco.atualizar(completo, score_editorial=70, confianca=0.9)
    for c in (parcial, completo):
        m.banco.aprovar(c)
    m.config.salvar("agendador", {"modo": "semiautomatico", "ordenar_por": "editorial", "cobertura_minima": 0.5})
    assert [c["id"] for c in m.candidatos("youtube")[0]] == [completo, parcial]
    # no automático com nota mínima, cobertura baixa não passa sozinha
    m.config.salvar("agendador", {"modo": "automatico", "score_editorial_minimo": 60})
    m.banco.aprovar(parcial, False)
    m.banco.aprovar(completo, False)
    bons, fora = m.candidatos("youtube")
    assert [c["id"] for c in bons] == [completo] and fora[0]["id"] == parcial


# ================================================================ plataformas
def test_partes_do_tiktok_seguem_a_regra_oficial():
    pequeno = tiktok.plano_de_partes(50_000_000)
    assert pequeno == {"chunk_size": 50_000_000, "total": 1, "faixas": [(0, 49_999_999)]}
    grande = tiktok.plano_de_partes(150_000_123)
    assert grande["total"] == 150_000_123 // tiktok.PARTE == 15
    faixas = grande["faixas"]
    assert faixas[0] == (0, 9_999_999) and faixas[-1][1] == 150_000_122
    assert all(b[0] == a[1] + 1 for a, b in zip(faixas, faixas[1:]))      # contíguas, sem buraco
    assert faixas[-1][1] - faixas[-1][0] + 1 < 2 * tiktok.PARTE            # a última leva o resto


def test_versao_da_graph_api_nao_e_removida_em_2027():
    assert facebook.V not in ("v20.0", "v21.0") and int(facebook.V[1:].split(".")[0]) >= 24


# ================================================================ banco corrompido: recuperação guiada
def test_restaurar_guarda_o_danificado_e_copia_o_backup(tmp_path):
    arq = tmp_path / "cortesex.db"
    arq.write_bytes(b"lixo" * 100)
    (tmp_path / "cortesex.db-wal").write_bytes(b"wal")
    bk = tmp_path / "bk.db"
    b = D.Banco(bk)
    b.novo_corte("a", tmp_path / "a.mp4", time.time(), 1, 3, "p", [], "", "SALVO")
    b.con.close()
    guardado = recuperacao.restaurar(arq, bk)
    assert guardado.read_bytes() == b"lixo" * 100 and Path(str(guardado) + "-wal").exists()
    assert D.Banco(arq).corte(1)["titulo"] == "SALVO"


def test_pagina_de_recuperacao_protegida(tmp_path):
    arq = tmp_path / "cortesex.db"
    arq.write_bytes(b"lixo" * 100)
    with pytest.raises(D.BancoCorrompido) as e:
        D.Banco(arq, tmp_path / "backups")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    srv = recuperacao.servir(e.value, porta)
    try:
        st, _, html = pedir(porta, "GET", "/")
        assert st == 200 and b"modo de recupera" in html and b"Nenhum backup" in html
        assert pedir(porta, "GET", "/", host="evil.com")[0] == 403
        assert pedir(porta, "POST", "/restaurar", cab={"X-CortesEX": "x"}, corpo={"indice": 0})[0] == 403
    finally:
        srv.shutdown()
    assert arq.read_bytes() == b"lixo" * 100


@pytest.mark.skipif(os.name == "nt", reason="no Windows o cofre é cifrado com DPAPI; permissão POSIX não se aplica")
def test_backup_da_atualizacao_so_o_dono_le(tmp_path):
    """AUD-L5: fora do Windows o cofre é texto; a cópia de backup fica 600 e a pasta 700."""
    from cortesex import atualizacao
    (tmp_path / "segredos.dat").write_bytes(b'CXS1P:{"gemini.api_key": "x"}')
    (tmp_path / "config.json").write_text("{}")
    destino = atualizacao.backup_se_mudou(tmp_path)
    assert destino and (destino.stat().st_mode & 0o777) == 0o700
    assert all((f.stat().st_mode & 0o777) == 0o600 for f in destino.iterdir())
    assert (destino / "segredos.dat").exists()


# ================================================================ rodada pós-uso (logs do usuário)
def test_gemini_pula_modelo_sem_acesso_e_explica_cada_um():
    tentados = []

    class R:
        def __init__(self, st, js=None):
            self.status_code, self._js, self.headers = st, js, {}

        def json(self):
            return self._js

    def post(url, json=None, **k):
        tentados.append(json["model"])
        return R(404) if json["model"] == "gemini-2.5-flash" else R(503)
    with pytest.raises(RuntimeError) as e:
        ia.analisar_e_escrever("c", "f", [], "k", modelo="gemini-2.5-flash", post=post, dormir=lambda s: None)
    assert tentados.count("gemini-2.5-flash") == 1                      # 404 não é tentado de novo
    msg = str(e.value)
    assert "gemini-2.5-flash: indisponível para essa chave" in msg and "gemini-flash-latest: ocupado (503)" in msg

    def post_ok(url, json=None, **k):
        if json["model"] == "gemini-2.5-flash":
            return R(404)
        return R(200, {"choices": [{"message": {"content": '{"titulo": "OK"}'}}]})
    assert ia.analisar_e_escrever("c", "f", [], "k", modelo="gemini-2.5-flash", post=post_ok,
                                  dormir=lambda s: None)["titulo"] == "OK"


def test_https_usa_os_certificados_do_sistema(monkeypatch):
    from cortesex import app
    chamado = []
    monkeypatch.setitem(sys.modules, "truststore", type("T", (), {"inject_into_ssl": lambda: chamado.append(1)}))
    app.usar_certificados_do_sistema()
    assert chamado == [1]

    def quebra():
        raise RuntimeError("sem loja")
    monkeypatch.setitem(sys.modules, "truststore", type("T", (), {"inject_into_ssl": quebra}))
    app.usar_certificados_do_sistema()                                     # não derruba o programa


def test_navegador_fechando_o_video_nao_vira_erro(painel, monkeypatch):
    porta, p, m, pasta = painel
    arq = pasta / "v.mp4"
    arq.write_bytes(b"x" * 5000)
    cid = m.banco.novo_corte("a", arq, time.time(), 1, 1, "p", [], "", "t")
    from cortesex import servidor
    erros = []
    monkeypatch.setattr(servidor.log, "exception", lambda *a, **k: erros.append(a))
    import socketserver
    monkeypatch.setattr(socketserver._SocketWriter, "write",
                        lambda self, b: (_ for _ in ()).throw(ConnectionAbortedError(10053, "anulada")))
    try:
        pedir(porta, "GET", f"/video/{cid}")
    except Exception:  # noqa: BLE001
        pass
    assert erros == []


def _erro_real(url, servidor=None):
    """Faz uma requisição de verdade (sem proxy) e devolve a exceção que o requests levantou."""
    req = requests.Request("GET", url).prepare()
    try:
        requests.adapters.HTTPAdapter(max_retries=0).send(req, timeout=5)
    except requests.RequestException as e:
        return e
    raise AssertionError("não falhou")


def test_sem_internet_nao_vira_incerto():
    """DNS falhou / conexão recusada: nada chegou à rede social → erro temporário, não 'incerto'."""
    from cortesex.plataformas import nunca_conectou
    assert nunca_conectou(_erro_real("http://cortesex-nao-existe.invalid/"))      # DNS (o 11001 do Windows)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]                                                 # porta fechada
    assert nunca_conectou(_erro_real(f"http://127.0.0.1:{porta}/"))
    # conexão aberta e derrubada no meio: pode ter chegado → continua incerto
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def derruba():
        c, _ = srv.accept()
        c.recv(100)
        c.close()
    threading.Thread(target=derruba, daemon=True).start()
    e = _erro_real(f"http://127.0.0.1:{srv.getsockname()[1]}/")
    srv.close()
    assert not nunca_conectou(e)


def test_post_sem_internet_volta_para_a_fila(ambiente, monkeypatch):
    m, pasta = ambiente
    m.config.salvar("youtube", {"refresh_token": "r"})
    autorizar(m)
    cid = corte(m, pasta, 3.0)
    erro = _erro_real("http://cortesex-nao-existe.invalid/")
    from cortesex.plataformas import ErroPostagem
    monkeypatch.setattr(motormod.youtube, "postar", lambda cfg, c: (_ for _ in ()).throw(
        ErroPostagem("YouTube: a conexão caiu durante o envio", True, "temporario",
                     incerto=not __import__("cortesex.plataformas", fromlist=["x"]).nunca_conectou(erro))))
    with pytest.raises(ErroPostagem):
        m.postar(m.banco.corte(cid), "youtube")
    assert m.banco.postagens_de(cid)[0]["status"] == "erro"


# ================================================================ descartar e espaço em disco
def _corte_com_arquivos(m, pasta):
    cid = corte(m, pasta, 3.0)
    c = m.banco.corte(cid)
    m.banco.mudar_estado(cid, D.REVISAO)
    arq = motormod.arquivos(c)
    for p in (arq["fonte"], arq["legendas"], arq["original"]):
        p.write_bytes(b"x")
    return cid, c, arq


def test_descartar_padrao_nao_apaga_e_recupera(ambiente):
    m, pasta = ambiente
    cid, c, arq = _corte_com_arquivos(m, pasta)
    assert m.descartar(cid, True, "ruim") == {"ok": True, "apagado": False}
    assert arq["video"].exists() and arq["fonte"].exists()
    m.descartar(cid, False)
    assert m.banco.corte(cid)["estado"] == D.REVISAO


def test_apagar_ao_descartar_tira_do_disco_e_bloqueia_recuperar(ambiente):
    m, pasta = ambiente
    m.config.salvar("armazenamento", {"apagar_ao_descartar": True})
    cid, c, arq = _corte_com_arquivos(m, pasta)
    reel = arq["video"].with_name(arq["video"].stem + "_reel60.mp4")
    reel.write_bytes(b"x")
    assert m.descartar(cid, True)["apagado"] is True
    assert not any(p.exists() for p in (arq["video"], arq["fonte"], arq["legendas"], arq["original"], reel))
    assert m.banco.corte(cid)["arquivo_removido"] == 1 and m.banco.corte(cid)["estado"] == D.DESCARTADO
    with pytest.raises(ValueError, match="já foi apagado"):
        m.descartar(cid, False)


def test_apagar_ao_descartar_respeita_envio_em_andamento(ambiente):
    m, pasta = ambiente
    m.config.salvar("armazenamento", {"apagar_ao_descartar": True})
    cid, c, arq = _corte_com_arquivos(m, pasta)
    m.banco.nova_postagem(cid, "youtube")                     # enviando
    assert m.descartar(cid, True)["apagado"] is False and arq["video"].exists()


def test_limpeza_apaga_descartados_antes_dos_outros(ambiente):
    m, pasta = ambiente
    descartado, _, arq_d = _corte_com_arquivos(m, pasta)
    normal, _, arq_n = _corte_com_arquivos(m, pasta)
    m.descartar(descartado, True)
    m.limpar(agora=time.time() + 2 * 86400)                  # 2 dias depois: descartados (1 dia) saem
    assert not arq_d["video"].exists() and m.banco.corte(descartado)["arquivo_removido"] == 1
    assert arq_n["video"].exists()                            # os outros só depois de 7 dias


def test_telegram_descartar_usa_a_mesma_regra(ambiente):
    from cortesex import telegram_revisao as T
    m, pasta = ambiente
    m.config.salvar("telegram", {"bot_token": "1:AAA", "chat_id": "5"})
    m.config.salvar("armazenamento", {"apagar_ao_descartar": True})
    cid, c, arq = _corte_com_arquivos(m, pasta)
    enviados = []
    ok = type("R", (), {"status_code": 200, "json": lambda s: {"ok": True, "result": {}}})()
    r = T.RevisaoTelegram(m, post=lambda url, **k: enviados.append(k.get("json")) or ok, get=None, assincrono=False)
    r.tratar({"update_id": 1, "callback_query": {"id": "q", "data": T.dados_botao("1:AAA", "d", cid),
                                                 "message": {"message_id": 3, "chat": {"id": 5}}}})
    assert not arq["video"].exists() and any("apagado do disco" in (e or {}).get("text", "") for e in enviados)


# ================================================================ login do YouTube embutido
def test_youtube_usa_cadastro_embutido_e_o_proprio_tem_prioridade(monkeypatch):
    from cortesex.plataformas import youtube
    import types
    monkeypatch.setitem(sys.modules, "cortesex._build",
                        types.SimpleNamespace(YT_CLIENT_ID="123-abc.apps.googleusercontent.com",
                                              YT_CLIENT_SECRET="GOCSPX-embutido123"))
    assert youtube.login_pronto({})
    url = youtube.link_login({}, "http://127.0.0.1:8777/", "estado1")
    assert "client_id=123-abc.apps.googleusercontent.com" in url and "GOCSPX" not in url
    assert "GOCSPX-embutido123" not in seguranca.redigir("erro GOCSPX-embutido123")   # registrado para redação
    proprio = {"client_id": "meu.apps.googleusercontent.com", "client_secret": "GOCSPX-meu"}
    assert youtube.credenciais(proprio)["client_id"] == "meu.apps.googleusercontent.com"
    monkeypatch.setitem(sys.modules, "cortesex._build", types.SimpleNamespace(BUILD=1))
    assert not youtube.login_pronto({})


def test_botao_entrar_sem_cadastro_explica(painel, monkeypatch):
    porta, p, m, _ = painel
    import types
    monkeypatch.setitem(sys.modules, "cortesex._build", types.SimpleNamespace(BUILD=1))
    st, _, corpo = pedir(porta, "POST", "/api/youtube/login", cab={"X-CortesEX": p.token, "Content-Type": "application/json"},
                         corpo={})
    assert st == 400 and "Avançado" in corpo.decode()
