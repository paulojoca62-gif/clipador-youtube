"""YouTube Shorts pela YouTube Data API, com login que se renova sozinho (refresh token)."""

import os
import time
from urllib.parse import urlencode

import requests

from cortesex.plataformas import ErroPostagem, texto_post, titulo

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
API = "https://www.googleapis.com/youtube/v3"
ESCOPOS = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

_cache = {"token": "", "expira": 0.0, "refresh": ""}


def link_login(cfg: dict, redirect_uri: str, estado: str) -> str:
    return AUTH + "?" + urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": ESCOPOS, "access_type": "offline", "prompt": "consent", "state": estado})


def trocar_codigo(cfg: dict, codigo: str, redirect_uri: str) -> str:
    r = requests.post(TOKEN, data={"code": codigo, "client_id": cfg["client_id"],
                                   "client_secret": cfg["client_secret"], "redirect_uri": redirect_uri,
                                   "grant_type": "authorization_code"}, timeout=30)
    j = r.json()
    if not r.ok or "refresh_token" not in j:
        raise ErroPostagem(f"O Google não entregou o login: {j.get('error_description') or j.get('error') or r.status_code}")
    _cache.update(token=j["access_token"], expira=time.time() + j.get("expires_in", 3600) - 120,
                  refresh=j["refresh_token"])
    return j["refresh_token"]


def token(cfg: dict) -> str:
    if not cfg.get("refresh_token"):
        raise ErroPostagem("YouTube não conectado. Abra Contas → YouTube e clique em Conectar.", False)
    if _cache["token"] and _cache["refresh"] == cfg["refresh_token"] and time.time() < _cache["expira"]:
        return _cache["token"]
    r = requests.post(TOKEN, data={"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                   "refresh_token": cfg["refresh_token"], "grant_type": "refresh_token"},
                      timeout=30)
    j = r.json()
    if not r.ok:
        raise ErroPostagem(f"Login do YouTube expirou ou foi revogado ({j.get('error')}). Conecte de novo.", False)
    _cache.update(token=j["access_token"], expira=time.time() + j.get("expires_in", 3600) - 120,
                  refresh=cfg["refresh_token"])
    return _cache["token"]


def nome_canal(cfg: dict) -> str:
    r = requests.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {token(cfg)}"}, timeout=30)
    if not r.ok:
        raise ErroPostagem(f"Não consegui ler o canal ({r.status_code}).")
    itens = r.json().get("items") or []
    return itens[0]["snippet"]["title"] if itens else "(conta sem canal)"


def testar(cfg: dict) -> str:
    return f"Conectado ao canal: {nome_canal(cfg)}"


def postar(cfg: dict, corte: dict) -> str:
    arq = corte["arquivo"]
    tags = [t.lstrip("#") for t in corte.get("hashtags") or []][:15]
    corpo = {"snippet": {"title": titulo(corte), "description": texto_post(corte, limite=4900),
                         "tags": tags, "categoryId": cfg.get("categoria") or "20"},
             "status": {"privacyStatus": cfg.get("privacidade") or "public", "selfDeclaredMadeForKids": False}}
    cab = {"Authorization": f"Bearer {token(cfg)}", "X-Upload-Content-Type": "video/mp4",
           "X-Upload-Content-Length": str(os.path.getsize(arq))}
    r = requests.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                      json=corpo, headers=cab, timeout=60)
    if r.status_code != 200:
        raise _erro(r)
    with open(arq, "rb") as f:
        r2 = requests.put(r.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=900)
    if r2.status_code not in (200, 201):
        raise _erro(r2)
    return f"https://youtu.be/{r2.json()['id']}"


def _erro(r) -> ErroPostagem:
    try:
        e = r.json().get("error", {})
        motivo = (e.get("errors") or [{}])[0].get("reason", "")
        msg = e.get("message", "")
    except ValueError:
        motivo, msg = "", r.text[:200]
    if motivo in ("uploadLimitExceeded", "quotaExceeded", "dailyLimitExceeded"):
        return ErroPostagem(f"YouTube: limite do dia atingido ({motivo}). Volta a postar amanhã.", False)
    if r.status_code == 401:
        return ErroPostagem("YouTube recusou o login. Conecte de novo em Contas → YouTube.", False)
    return ErroPostagem(f"YouTube {r.status_code}: {motivo or msg}"[:300])
