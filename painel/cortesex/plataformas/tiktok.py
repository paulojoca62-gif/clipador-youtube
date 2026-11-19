"""TikTok pela Content Posting API (Direct Post).

Enquanto o app do TikTok não passar pela auditoria, o TikTok só aceita posts
privados (SELF_ONLY) e só em contas privadas. Depois da auditoria, o mesmo
código posta público, sem mudanças.
"""

import os
import time
from urllib.parse import urlencode

import requests

from cortesex.plataformas import ErroPostagem, texto_post, titulo

AUTH = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
ESCOPOS = "user.info.basic,video.publish"


def link_login(cfg: dict, redirect_uri: str, estado: str) -> str:
    return AUTH + "?" + urlencode({"client_key": cfg["client_key"], "scope": ESCOPOS, "response_type": "code",
                                   "redirect_uri": redirect_uri, "state": estado})


def _guardar(cfg: dict, j: dict) -> dict:
    return {"access_token": j["access_token"], "refresh_token": j.get("refresh_token") or cfg.get("refresh_token"),
            "expira_em": time.time() + int(j.get("expires_in", 86400)) - 300}


def trocar_codigo(cfg: dict, codigo: str, redirect_uri: str) -> dict:
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": cfg["client_key"], "client_secret": cfg["client_secret"], "code": codigo,
        "grant_type": "authorization_code", "redirect_uri": redirect_uri}, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise ErroPostagem(f"O TikTok não entregou o login: {j.get('error_description') or j.get('error')}", False)
    return _guardar(cfg, j)


def renovar(cfg: dict) -> dict | None:
    """Devolve os campos novos do token se precisou renovar, ou None."""
    if not cfg.get("refresh_token"):
        raise ErroPostagem("TikTok não conectado. Abra Contas → TikTok e conecte.", False)
    if cfg.get("access_token") and time.time() < float(cfg.get("expira_em") or 0):
        return None
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": cfg["client_key"], "client_secret": cfg["client_secret"],
        "grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]}, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise ErroPostagem("O login do TikTok expirou. Conecte de novo em Contas → TikTok.", False)
    return _guardar(cfg, j)


def _api(cfg, caminho, corpo):
    r = requests.post(f"{API}{caminho}", json=corpo, timeout=60,
                      headers={"Authorization": f"Bearer {cfg['access_token']}",
                               "Content-Type": "application/json; charset=UTF-8"})
    j = r.json()
    erro = (j.get("error") or {})
    if erro.get("code") not in (None, "", "ok"):
        if erro.get("code") == "unaudited_client_can_only_post_to_private_accounts":
            raise ErroPostagem("TikTok: app sem auditoria só posta em conta PRIVADA. Deixe a conta privada "
                               "ou peça a auditoria do app.", False)
        if erro.get("code") in ("spam_risk_too_many_posts", "rate_limit_exceeded"):
            raise ErroPostagem(f"TikTok: limite de posts atingido ({erro['code']}).", False)
        raise ErroPostagem(f"TikTok: {erro.get('code')} {erro.get('message', '')}"[:300])
    return j.get("data") or {}


def info(cfg: dict) -> dict:
    return _api(cfg, "/post/publish/creator_info/query/", {})


def testar(cfg: dict) -> str:
    d = info(cfg)
    return (f"Conectado como @{d.get('creator_username', '?')}. "
            f"Privacidade permitida: {', '.join(d.get('privacy_level_options') or [])}")


def postar(cfg: dict, corte: dict) -> str:
    d = info(cfg)
    opcoes = d.get("privacy_level_options") or ["SELF_ONLY"]
    privacidade = cfg.get("privacidade") if cfg.get("privacidade") in opcoes else (
        "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opcoes else opcoes[0])
    arq = corte["arquivo"]
    tamanho = os.path.getsize(arq)
    legenda = f"{titulo(corte)}\n\n{texto_post(corte, limite=2000)}"[:2200]
    ini = _api(cfg, "/post/publish/video/init/", {
        "post_info": {"title": legenda, "privacy_level": privacidade, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho, "chunk_size": tamanho,
                        "total_chunk_count": 1}})
    with open(arq, "rb") as f:
        r = requests.put(ini["upload_url"], data=f, timeout=900, headers={
            "Content-Type": "video/mp4", "Content-Length": str(tamanho),
            "Content-Range": f"bytes 0-{tamanho - 1}/{tamanho}"})
    if r.status_code not in (200, 201):
        raise ErroPostagem(f"TikTok: falha no envio do vídeo ({r.status_code})")
    usuario = d.get("creator_username") or cfg.get("usuario") or ""
    aviso = "" if privacidade == "PUBLIC_TO_EVERYONE" else f" [{privacidade}]"
    return (f"https://www.tiktok.com/@{usuario}" if usuario else "TikTok") + aviso
