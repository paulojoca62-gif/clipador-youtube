import json
import sqlite3

import pytest

from cortesex import analytics, atualizacao, diagnostico, midia
from cortesex.db import Banco
from conftest import corte, video_teste


def test_valida_video_bom_e_recusa_os_ruins(tmp_path):
    bom = video_teste(tmp_path / "bom.mp4", 5, 1080, 1920)
    info = midia.validar(bom)
    assert info["ok"] and info["audio"] and 4.5 < info["duracao"] < 5.5 and info["fps"] == 30
    with pytest.raises(midia.MidiaInvalida, match="resolução"):
        midia.validar(video_teste(tmp_path / "h.mp4", 5, 1280, 720))
    with pytest.raises(midia.MidiaInvalida, match="áudio"):
        midia.validar(video_teste(tmp_path / "mudo.mp4", 5, 1080, 1920, audio=False))
    with pytest.raises(midia.MidiaInvalida, match="esperada"):
        midia.validar(bom, duracao_esperada=40)
    (tmp_path / "lixo.mp4").write_bytes(b"0" * 20000)
    with pytest.raises(midia.MidiaInvalida):
        midia.validar(tmp_path / "lixo.mp4")
    with pytest.raises(midia.MidiaInvalida, match="não existe"):
        midia.validar(tmp_path / "nada.mp4")


def test_perfil_de_audio(tmp_path):
    v = video_teste(tmp_path / "a.mp4", 4)
    p = midia.perfil_audio(midia.ler_audio(v))
    assert len(p["db"]) == 8 and p["silencio"] == 0 and p["mediana_db"] > -30


def test_migra_banco_da_versao_1(tmp_path):
    arq = tmp_path / "cortesex.db"
    con = sqlite3.connect(arq)
    con.executescript("""CREATE TABLE cortes (id INTEGER PRIMARY KEY AUTOINCREMENT, canal TEXT NOT NULL,
        arquivo TEXT NOT NULL UNIQUE, criado REAL NOT NULL, duracao REAL DEFAULT 0, score REAL DEFAULT 0,
        motivo TEXT DEFAULT '', chat TEXT DEFAULT '[]', fala TEXT DEFAULT '', titulo TEXT DEFAULT '',
        legenda TEXT DEFAULT '', hashtags TEXT DEFAULT '[]', descartado INTEGER DEFAULT 0);
        CREATE TABLE postagens (id INTEGER PRIMARY KEY AUTOINCREMENT, corte_id INTEGER NOT NULL, plataforma TEXT NOT NULL,
        status TEXT NOT NULL, url TEXT DEFAULT '', erro TEXT DEFAULT '', quando REAL NOT NULL, origem TEXT DEFAULT 'agendador');
        INSERT INTO cortes (canal, arquivo, criado, score, titulo) VALUES ('a', '/x/1.mp4', 1, 3.2, 'T');
        INSERT INTO cortes (canal, arquivo, criado, score, descartado) VALUES ('a', '/x/2.mp4', 2, 2.0, 1);
        INSERT INTO postagens (corte_id, plataforma, status, url, quando) VALUES (1, 'youtube', 'postado', 'u', 3);""")
    con.commit()
    con.close()
    b = Banco(arq, tmp_path / "backups")
    assert list((tmp_path / "backups").glob("cortesex-antes-da-migracao-*.db"))
    assert b.corte(1)["estado"] == "aguardando_revisao" and b.corte(1)["titulo"] == "T"
    assert b.corte(2)["estado"] == "descartado"
    assert b.postagens_de(1)[0]["status"] == "postado"
    assert b.verificar() == "ok"
    Banco(arq, tmp_path / "backups")          # abrir de novo não migra nem faz backup outra vez
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_backup_ao_mudar_de_versao(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(json.dumps({"gemini": {"api_key": "AIza-texto-puro", "modelo": "m"}}))
    Banco(tmp_path / "cortesex.db")
    d = atualizacao.backup_se_mudou(tmp_path)
    assert d and (d / "config.json").exists() and (d / "cortesex.db").exists()
    copia = json.loads((d / "config.json").read_text())
    assert copia["gemini"] == {"api_key": "", "modelo": "m"}      # backup sem a chave em texto puro
    assert atualizacao.backup_se_mudou(tmp_path) is None        # mesma versão: não repete
    monkeypatch.setattr(atualizacao, "VERSAO", "9.9.9")
    assert atualizacao.backup_se_mudou(tmp_path)


def test_diagnostico_sem_segredos(ambiente):
    m, _ = ambiente
    m.config.salvar("gemini", {"api_key": "AIzaSyA1234567890abcdefghijk"})
    d = diagnostico.coletar(m)
    assert d["geral"] in ("operacional", "atencao", "falha")
    assert d["banco"]["integridade"] == "ok" and "ffmpeg" in d["dependencias"]
    assert d["saude"]["youtube"]["estado"] == "nao_configurado"
    assert d["saude"]["gemini"]["estado"] == "nao_validado"
    assert "AIzaSy" not in json.dumps(d, default=str)


def test_analytics_nao_inventa_e_agrupa(ambiente):
    m, pasta = ambiente
    r = analytics.relatorio(m.banco)
    assert r["total_videos_medidos"] == 0 and "não há dados suficientes" in r["recomendacoes"][0]
    assert "indisponível" in r["disponibilidade"]["tiktok"]
    for i, (cat, views) in enumerate([("engracado", 900), ("engracado", 1100), ("engracado", 1000),
                                      ("gameplay", 100), ("gameplay", 200), ("gameplay", 150)]):
        cid = corte(m, pasta, 3.0)
        m.banco.atualizar(cid, categoria=cat)
        pid = m.banco.nova_postagem(cid, "youtube")
        m.banco.fim_postagem(pid, "postado", url="u", ext_id=f"v{i}")
        m.banco.nova_metrica(pid, views=views, likes=10)
    r = analytics.relatorio(m.banco)
    assert r["total_videos_medidos"] == 6
    assert r["tabelas"]["categoria"][0]["grupo"] == "Engraçado" and r["tabelas"]["categoria"][0]["media_views"] == 1000
    assert any("Engraçado" in x for x in r["recomendacoes"])


def test_coleta_de_metricas_do_youtube(ambiente, monkeypatch):
    m, pasta = ambiente
    from cortesex import motor as motormod
    m.config.salvar("youtube", {"refresh_token": "r"})
    cid = corte(m, pasta, 3.0)
    pid = m.banco.nova_postagem(cid, "youtube")
    m.banco.fim_postagem(pid, "postado", url="https://youtu.be/abc", ext_id="abc")
    monkeypatch.setattr(motormod.youtube, "estatisticas", lambda cfg, ids: {"abc": {"views": 42, "likes": 3,
                                                                                    "comentarios": 1}})
    assert m.coletar_metricas() == 1
    assert m.coletar_metricas() == 0              # respeita o intervalo entre coletas
    assert m.banco.metricas_ultimas()[0]["views"] == 42
