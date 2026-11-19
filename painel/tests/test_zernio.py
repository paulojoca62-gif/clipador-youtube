"""Zernio: TikTok público e Reels do Facebook pela API deles (rede simulada) + recorte real para 60 s."""
import json

import pytest
import requests

from cortesex import midia
from cortesex import motor as motormod
from cortesex.plataformas import AUTENTICACAO, COTA, MIDIA, ErroPostagem, zernio
from conftest import autorizar, corte, video_teste


class Resp:
    def __init__(self, status=200, js=None):
        self.status_code, self._js, self.headers = status, js or {}, {}

    def json(self):
        return self._js


class FakeZernio:
    """Simula a API: presign, PUT do arquivo, criação e leitura do post."""
    def __init__(self, status_post=201, entrada=None, falha_rede=False):
        self.chamadas = []
        self.status_post, self.falha_rede = status_post, falha_rede
        self.entrada = entrada or {"status": "published", "platformPostUrl": "https://www.tiktok.com/@x/video/1"}
        self.posts = {}

    def post(self, url, headers=None, json=None, **k):
        self.chamadas.append(("POST", url, headers, json))
        if url.endswith("/media/presign"):
            return Resp(200, {"uploadUrl": "https://bucket.r2.cloudflarestorage.com/temp/x?sig=1",
                              "publicUrl": "https://media.zernio.com/temp/x.mp4"})
        if self.falha_rede:
            raise requests.ConnectionError("caiu")
        chave = headers["Idempotency-Key"]
        if chave in self.posts:
            return Resp(200, {"post": self.posts[chave]})
        plat = json["platforms"][0]["platform"]
        post = {"_id": f"post{len(self.posts) + 1}", "status": "x",
                "platforms": [{"platform": plat, **self.entrada}]}
        self.posts[chave] = post
        return Resp(self.status_post, {"post": post})

    def put(self, url, data=None, headers=None, **k):
        self.chamadas.append(("PUT", url, headers, None))
        assert "Authorization" not in headers                      # upload vai direto ao storage
        return Resp(200)

    def get(self, url, headers=None, **k):
        self.chamadas.append(("GET", url, headers, None))
        if url.endswith("/accounts"):
            return Resp(200, {"accounts": [
                {"_id": "acc_tt", "platform": "tiktok", "username": "@cortes", "isActive": True},
                {"_id": "acc_fb", "platform": "facebook", "displayName": "Cortes EX", "isActive": True},
                {"_id": "acc_x", "platform": "twitter", "username": "@x"}]})
        pid = url.rsplit("/", 1)[1]
        post = next(p for p in self.posts.values() if p["_id"] == pid)
        return Resp(200, {"post": post})

    def criados(self):
        return [c for c in self.chamadas if c[0] == "POST" and c[1].endswith("/posts")]


@pytest.fixture
def zn(ambiente):
    m, pasta = ambiente
    m.config.salvar("zernio", {"api_key": "zk_teste_123", "contas": {"tiktok": "acc_tt", "facebook": "acc_fb"}})
    m.config.salvar("tiktok", {"via": "zernio"})
    m.config.salvar("facebook", {"via": "zernio"})
    autorizar(m)
    return m, pasta


def rede(m, plat, fake):
    return zernio.Rede(plat, m.config, http=fake, preparar=lambda a: a)


def test_tiktok_publico_com_consentimento(zn):
    m, pasta = zn
    fake = FakeZernio()
    cid = corte(m, pasta, 3.0)
    res = rede(m, "tiktok", fake).postar({}, {**m.banco.corte(cid), "_postagem": 1})
    assert res["status"] == "postado" and res["url"].startswith("https://www.tiktok.com/") and res["ext_id"] == "post1"
    _, url, cab, corpo = fake.criados()[0]
    assert url == "https://zernio.com/api/v1/posts" and cab["Authorization"] == "Bearer zk_teste_123"
    assert corpo["publishNow"] is True and corpo["mediaItems"][0]["url"].startswith("https://media.zernio.com/")
    ts = corpo["tiktokSettings"]
    assert ts["privacy_level"] == "PUBLIC_TO_EVERYONE" and ts["content_preview_confirmed"] and ts["express_consent_given"]
    assert {"allow_comment", "allow_duet", "allow_stitch"} <= set(ts)
    assert corpo["platforms"] == [{"platform": "tiktok", "accountId": "acc_tt"}]


def test_facebook_vai_como_reel(zn):
    m, pasta = zn
    fake = FakeZernio(entrada={"status": "published", "platformPostUrl": "https://www.facebook.com/reel/9"})
    cid = corte(m, pasta, 3.0, titulo="QUE JOGADA")
    res = rede(m, "facebook", fake).postar({}, {**m.banco.corte(cid), "_postagem": 1})
    corpo = fake.criados()[0][3]
    assert corpo["platforms"][0]["platformSpecificData"] == {"contentType": "reel", "title": "QUE JOGADA"}
    assert res["url"] == "https://www.facebook.com/reel/9"


def test_reenvio_da_mesma_postagem_nao_duplica(zn):
    m, pasta = zn
    fake = FakeZernio()
    c = {**m.banco.corte(corte(m, pasta, 3.0)), "_postagem": 7}
    r = rede(m, "tiktok", fake)
    a, b = r.postar({}, c), r.postar({}, c)
    assert a["ext_id"] == b["ext_id"] and len(fake.posts) == 1          # mesma Idempotency-Key
    r.postar({}, {**c, "_postagem": 8})                                  # repost forçado = postagem nova
    assert len(fake.posts) == 2


def test_tiktok_processando_e_verificacao(zn):
    m, pasta = zn
    fake = FakeZernio(entrada={"status": "uploading"})
    r = rede(m, "tiktok", fake)
    res = r.postar({}, {**m.banco.corte(corte(m, pasta, 3.0)), "_postagem": 1})
    assert res["status"] == "processando"
    fake.posts[next(iter(fake.posts))]["platforms"][0].update(status="published", platformPostUrl="https://t/1")
    assert r.verificar({}, res["ext_id"]) == {"url": "https://t/1", "ext_id": "post1", "status": "postado", "detalhe": ""}


@pytest.mark.parametrize("cat,tipo,de_novo", [("user_content", MIDIA, False), ("auth_expired", AUTENTICACAO, False),
                                               ("quota_exhausted", COTA, True), ("platform_error", "temporario", True)])
def test_falha_na_rede_vira_erro_classificado(zn, cat, tipo, de_novo):
    m, pasta = zn
    fake = FakeZernio(status_post=207, entrada={"status": "failed", "errorMessage": "motivo", "errorCategory": cat})
    with pytest.raises(ErroPostagem) as e:
        rede(m, "tiktok", fake).postar({}, {**m.banco.corte(corte(m, pasta, 3.0)), "_postagem": 1})
    assert e.value.tipo == tipo and e.value.tentar_de_novo is de_novo and "motivo" in str(e.value)


def test_conexao_caida_ao_publicar_e_incerto(zn):
    m, pasta = zn
    with pytest.raises(ErroPostagem) as e:
        rede(m, "tiktok", FakeZernio(falha_rede=True)).postar({}, {**m.banco.corte(corte(m, pasta, 3.0)), "_postagem": 1})
    assert e.value.incerto


def test_motor_usa_o_zernio_e_nao_o_app_proprio(zn, monkeypatch):
    m, pasta = zn
    fake = FakeZernio()
    monkeypatch.setattr(zernio, "requests", type("R", (), {"post": fake.post, "get": fake.get, "put": fake.put,
                                                          "RequestException": requests.RequestException}))
    monkeypatch.setattr(motormod.tiktok, "renovar", lambda cfg: pytest.fail("usou o app próprio do TikTok"))
    monkeypatch.setattr(motormod.tiktok, "postar", lambda cfg, c: pytest.fail("usou o app próprio do TikTok"))
    assert motormod.conectado(m.config, "tiktok") and motormod.conectado(m.config, "facebook")
    cid = corte(m, pasta, 3.0)
    url = m.postar(m.banco.corte(cid), "tiktok", origem="manual")
    assert url.startswith("https://www.tiktok.com/")
    with pytest.raises(ErroPostagem, match="já está postado"):
        m.postar(m.banco.corte(cid), "tiktok", origem="manual")


def test_sem_conta_escolhida_nao_conta_como_conectado(ambiente):
    m, _ = ambiente
    m.config.salvar("tiktok", {"via": "zernio"})
    m.config.salvar("zernio", {"api_key": "k"})
    assert not motormod.conectado(m.config, "tiktok")


def test_chave_do_zernio_vai_para_o_cofre(ambiente):
    m, pasta = ambiente
    m.config.salvar("zernio", {"api_key": "zk_secreta_987"})
    assert "zk_secreta_987" not in (pasta / "config.json").read_text()
    assert m.config.publico()["zernio"]["api_key"] is True
    assert m.config.get("zernio", "api_key") == "zk_secreta_987"


def test_listar_contas_so_tiktok_e_facebook():
    contas = zernio.listar_contas("k", FakeZernio())
    assert [(c["id"], c["plataforma"]) for c in contas] == [("acc_tt", "tiktok"), ("acc_fb", "facebook")]
    with pytest.raises(ErroPostagem):
        zernio.listar_contas("", FakeZernio())


def test_reel_recorta_os_ultimos_60s_de_verdade(tmp_path):
    longo = video_teste(tmp_path / "corte.mp4", segundos=75, w=270, h=480)
    curto = video_teste(tmp_path / "curto.mp4", segundos=20, w=270, h=480)
    assert zernio.preparar_reel(curto) == curto
    reel = zernio.preparar_reel(longo)
    info = midia.validar(reel, largura=None)
    assert reel != longo and 58.5 <= info["duracao"] <= 60.0 and info["audio"]
    assert midia.validar(longo, largura=None)["duracao"] > 74             # o corte original não muda
    assert zernio.preparar_reel(longo) == reel                            # reaproveita a cópia
    json.dumps(info)
