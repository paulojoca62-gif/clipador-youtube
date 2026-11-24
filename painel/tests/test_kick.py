"""Kick: gravação pela URL da Kick e pico detectado pelo áudio (sem chat)."""
import time

import numpy as np

from cortesex import config as cfgmod
from cortesex.gravador import FORMATO_BLOCO, Canal
from cortesex.plataformas import texto_post


def canal_kick(tmp_path, cortes):
    cfg = cfgmod.Config(tmp_path / "config.json")
    return Canal({"nome": "streamerkick", "plataforma": "kick"}, cfg, tmp_path, cortes.append)


def tom(db, segundos=4, taxa=16000):
    amp = 10 ** (db / 20) * np.sqrt(2)
    t = np.arange(int(segundos * taxa)) / taxa
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


def blocos(c, n, inicio):
    for i in range(n):
        nome = time.strftime(FORMATO_BLOCO, time.localtime(inicio + 4 * i)) + ".ts"
        (c.dir_seg / nome).write_bytes(b"x")


def test_kick_grava_pela_url_da_kick_sem_chat(tmp_path):
    c = canal_kick(tmp_path, [])
    assert c.url_live() == ["https://kick.com/streamerkick"]
    assert c.estado()["detector"] == "áudio" and c.estado()["plataforma"] == "kick"
    tw = Canal({"nome": "x"}, c.config, tmp_path, lambda j: None)
    assert tw.plataforma == "twitch" and "--twitch-low-latency" in tw.url_live()


def test_reacao_alta_no_audio_dispara_corte(tmp_path):
    cortes = []
    c = canal_kick(tmp_path, cortes)
    agora = time.time()
    inicio = agora - 40 * 4                       # 40 blocos de 4 s = 160 s de live
    blocos(c, 40, inicio)
    volumes = {}
    for i, (ts, p) in enumerate(c.segmentos()):
        volumes[p.name] = -30.0 if i != 37 else -14.0     # bloco 37: grito, 16 dB acima do normal
    c.ouvir_blocos(agora=agora, ler=lambda p: tom(volumes[p.name]))
    assert c.normal_audio is not None and abs(c.normal_audio - (-30)) < 1.5
    assert len(cortes) == 1 and "reação no áudio +16 dB" in cortes[0]["motivo"]
    assert 5 < cortes[0]["score"] < 8                       # +16 dB ≈ 6,3x


def test_sem_aquecimento_ou_reacao_pequena_nao_dispara(tmp_path):
    cortes = []
    c = canal_kick(tmp_path, cortes)
    agora = time.time()
    blocos(c, 20, agora - 80)                                 # só 80 s: ainda aprendendo o normal
    c.ouvir_blocos(agora=agora, ler=lambda p: tom(-10))
    assert cortes == []
    cortes2 = []
    (tmp_path / "b").mkdir()
    c2 = canal_kick(tmp_path / "b", cortes2)
    blocos(c2, 40, agora - 160)
    vol = {p.name: (-30.0 if i != 37 else -25.0) for i, (_, p) in enumerate(c2.segmentos())}
    c2.ouvir_blocos(agora=agora, ler=lambda p: tom(vol[p.name]))
    assert cortes2 == [] and c2.normal_audio is not None                   # +5 dB: abaixo do limiar de 9


def test_blocos_antigos_so_ensinam_o_normal(tmp_path):
    cortes = []
    c = canal_kick(tmp_path, cortes)
    agora = time.time()
    blocos(c, 40, agora - 40 * 4 - 600)                       # tudo de 10 min atrás
    vol = {p.name: (-30.0 if i != 37 else -10.0) for i, (_, p) in enumerate(c.segmentos())}
    c.ouvir_blocos(agora=agora, ler=lambda p: tom(vol[p.name]))
    assert cortes == []


def test_credito_da_live_aponta_para_a_kick():
    c = {"canal": "streamerkick", "legenda": "l", "hashtags": []}
    assert "Live: kick.com/streamerkick" in texto_post({**c, "_plataforma_live": "kick"})
    assert "Live: twitch.tv/streamerkick" in texto_post(c)


def test_canal_salvo_com_plataforma(tmp_path):
    cfg = cfgmod.Config(tmp_path / "config.json")
    cfg.salvar("canais", [{"nome": "a", "plataforma": "kick"}, {"nome": "b", "plataforma": "youtube"}])
    assert [c["plataforma"] for c in cfg.get("canais")] == ["kick", "twitch"]
