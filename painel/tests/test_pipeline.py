"""Pipeline real com ffmpeg: blocos gravados → fonte → montagem 9:16 → validação → retomada após queda."""
import json
import time
import subprocess

import pytest

from cortesex import config as cfgmod
from cortesex import db as D
from cortesex import midia
from cortesex import motor as motormod
from cortesex.gravador import FORMATO_BLOCO
from conftest import video_teste


def gravar_blocos(pasta, canal, inicio, segundos=40):
    """Simula a gravação: blocos .ts de 4 s com nome = horário, como o gravador faz."""
    dest = pasta / "buffer" / canal
    dest.mkdir(parents=True, exist_ok=True)
    src = video_teste(pasta / "src.mp4", segundos=segundos)
    tmp = pasta / "tmpseg"
    tmp.mkdir()
    from cortesex import util
    subprocess.run([util.ffmpeg(), "-v", "error", "-i", str(src), "-map", "0", "-c", "copy", "-f", "segment",
                    "-segment_time", "4", "-reset_timestamps", "1", str(tmp / "%03d.ts")], check=True)
    for i, p in enumerate(sorted(tmp.glob("*.ts"))):
        p.rename(dest / (time.strftime(FORMATO_BLOCO, time.localtime(inicio + 4 * i)) + ".ts"))
    return dest


@pytest.fixture
def real(tmp_path, monkeypatch):
    monkeypatch.setenv("CORTESEX_DADOS", str(tmp_path))
    c = cfgmod.Config(tmp_path / "config.json")
    c.salvar("corte", {"adaptativo": True, "duracao_min": 10, "duracao_max": 30})
    c.salvar("gravacao", {"legendas": True})
    m = motormod.Motor(config=c, pasta=tmp_path)
    # Whisper real é pesado demais para CI: devolve frases com tempo, o resto (ffmpeg, áudio, ffprobe) é real
    falas = [(1.0, 1.4, "Olha"), (1.5, 2.0, "isso."), (6.0, 6.5, "Agora"), (6.6, 7.0, "vai."),
             (12.0, 12.4, "Que"), (12.5, 13.0, "jogada!"), (18.0, 18.5, "Meu"), (18.6, 19.1, "Deus!"),
             (24.0, 24.5, "Acabou"), (24.6, 25.2, "tudo."), (31.0, 31.5, "Fim.")]
    monkeypatch.setattr(m.render, "transcrever",
                        lambda fonte, nome="small": (falas, midia.perfil_audio(midia.ler_audio(fonte))))
    return m, tmp_path


def rodar_tudo(m, agora):
    n = 0
    while m.processar_proximo(agora=agora):
        n += 1
        assert n < 20
    return n


def test_pipeline_completo_com_ffmpeg(real):
    m, pasta = real
    inicio = time.time() - 200
    gravar_blocos(pasta, "alanzoka", inicio, 40)
    cid = m.banco.novo_candidato("alanzoka", pasta / "cortes" / "alanzoka" / "teste.mp4", inicio + 20, 3.2,
                                 "chat 3.2x", ["KKKK", "QUE ISSO"], inicio, inicio + 36)
    assert rodar_tudo(m, time.time()) == 4
    c = m.banco.corte(cid)
    assert c["estado"] == D.REVISAO                    # sem chave do Gemini: fica para revisão
    assert c["validacao"]["ok"] and c["validacao"]["largura"] == 1080 and c["validacao"]["altura"] == 1920
    assert 10 <= c["duracao"] <= 30
    assert c["validacao"]["limites"]["metodo"] == "adaptativo"
    assert c["score_editorial"] is not None and 0 < c["confianca"] < 1   # sem IA: confiança parcial
    info = midia.validar(c["arquivo"])
    assert info["audio"] and abs(info["duracao"] - c["duracao"]) < 1.5
    estados = [h["estado"] for h in m.banco.historico_de(cid)]
    for e in (D.DETECTADO, D.CAPTURANDO, D.CAPTURADO, D.TRANSCREVENDO, D.TRANSCRITO, D.MONTANDO, D.MONTADO,
              D.GERANDO_TEXTOS, D.REVISAO):
        assert e in estados
    leg = json.loads((pasta / "cortes" / "alanzoka" / "teste_legendas.json").read_text())
    assert leg["segmentos"] and all(s["fim"] > s["ini"] for s in leg["segmentos"])

    # editor: muda o texto da legenda e monta de novo a partir da fonte; o original é guardado
    segs = leg["segmentos"]
    segs[0]["texto"] = "TEXTO CORRIGIDO"
    m.banco.aprovar(cid)
    c2 = m.remontar(cid, segmentos=segs)
    assert c2["versao_render"] == 2 and c2["estado"] == D.APROVADO
    assert (pasta / "cortes" / "alanzoka" / "teste_original.mp4").exists()
    midia.validar(c2["arquivo"])


def test_retoma_depois_de_queda_sem_repetir_etapas(real):
    m, pasta = real
    inicio = time.time() - 200
    gravar_blocos(pasta, "alanzoka", inicio, 40)
    cid = m.banco.novo_candidato("alanzoka", pasta / "cortes" / "alanzoka" / "q.mp4", inicio + 20, 3.0, "pico",
                                 [], inicio, inicio + 36)
    agora = time.time()
    assert m.processar_proximo(agora=agora)            # capturou
    assert m.processar_proximo(agora=agora)            # transcreveu
    # simula o programa fechando no meio da montagem
    m.banco.mudar_estado(cid, D.MONTANDO, "simulação de queda")
    m2 = motormod.Motor(config=m.config, pasta=pasta)   # “reabrir o programa”
    assert m2.banco.corte(cid)["estado"] == D.TRANSCRITO   # voltou ao último checkpoint
    m2.render.transcrever = m.render.transcrever
    capturas = [h for h in m2.banco.historico_de(cid) if h["estado"] == D.CAPTURANDO]
    rodar_tudo(m2, agora)
    assert m2.banco.corte(cid)["estado"] == D.REVISAO
    # não capturou nem transcreveu de novo
    assert len([h for h in m2.banco.historico_de(cid) if h["estado"] == D.CAPTURANDO]) == len(capturas)


def test_buffer_expirado_falha_definitivo(real):
    m, pasta = real
    cid = m.banco.novo_candidato("ninguem", pasta / "cortes" / "ninguem" / "x.mp4", time.time() - 500, 3.0, "pico",
                                 [], time.time() - 560, time.time() - 470)
    assert m.processar_proximo()
    c = m.banco.corte(cid)
    assert c["estado"] == D.FALHOU and "buffer" in c["erro"]


def test_falha_temporaria_tenta_de_novo_com_espera(real, monkeypatch):
    m, pasta = real
    inicio = time.time() - 200
    gravar_blocos(pasta, "alanzoka", inicio, 40)
    cid = m.banco.novo_candidato("alanzoka", pasta / "cortes" / "alanzoka" / "t.mp4", inicio + 20, 3.0, "pico",
                                 [], inicio, inicio + 36)
    original = motormod.capturar
    chamadas = []

    def instavel(*a, **k):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise RuntimeError("disco ocupado")
        return original(*a, **k)
    monkeypatch.setattr(motormod, "capturar", instavel)
    agora = time.time()
    assert m.processar_proximo(agora=agora)
    c = m.banco.corte(cid)
    assert c["estado"] == D.DETECTADO and c["etapa_tentativas"] == 1 and "disco ocupado" in c["erro"]
    assert not m.processar_proximo(agora=agora)        # respeita a espera (backoff)
    assert m.processar_proximo(agora=agora + 60)
    assert m.banco.corte(cid)["estado"] == D.CAPTURADO


def test_picos_do_mesmo_evento_sao_agrupados(ambiente):
    m, pasta = ambiente

    class C:
        nome = "alanzoka"
        layout = "blur"
        dir_cortes = pasta / "cortes" / "alanzoka"

        def nome_corte(self, momento):
            return f"x{int(momento)}"
    t = time.time()
    a = m.agendar_corte({"canal": C(), "momento": t, "score": 3.0, "motivo": "chat 3x", "chat": ["a"]})
    b = m.agendar_corte({"canal": C(), "momento": t + 10, "score": 4.5, "motivo": "chat 4.5x", "chat": ["b"]})
    c = m.agendar_corte({"canal": C(), "momento": t + 120, "score": 3.1, "motivo": "outro", "chat": []})
    assert a == b != c
    junto = m.banco.corte(a)
    assert junto["score"] == 4.5 and junto["janela_fim"] >= t + 10 + 30 and junto["chat"] == ["a", "b"]
