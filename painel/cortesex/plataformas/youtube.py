"""YouTube Shorts pela YouTube Data API, com login que se renova sozinho (refresh token)."""

import os
import time
from urllib.parse import urlencode

import requests

from cortesex.plataformas import (AUTENTICACAO, COTA, MIDIA, PERMISSAO, PROCESSAMENTO, TEMPORARIO, ErroPostagem,
                                  resultado, retry_after, texto_post, titulo)

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
        raise ErroPostagem("YouTube não conectado. Abra Configurações → YouTube e clique em Conectar.", False,
                           AUTENTICACAO)
    if _cache["token"] and _cache["refresh"] == cfg["refresh_token"] and time.time() < _cache["expira"]:
        return _cache["token"]
    r = requests.post(TOKEN, data={"client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
                                   "refresh_token": cfg["refresh_token"], "grant_type": "refresh_token"},
                      timeout=30)
    j = r.json()
    if not r.ok:
        raise ErroPostagem(f"Login do YouTube expirou ou foi revogado ({j.get('error')}). Conecte de novo.", False,
                           AUTENTICACAO)
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


def postar(cfg: dict, corte: dict) -> dict:
    arq = corte["arquivo"]
    tags = [t.lstrip("#") for t in corte.get("hashtags") or []][:15]
    privacidade = cfg.get("privacidade") or "public"
    corpo = {"snippet": {"title": titulo(corte), "description": texto_post(corte, limite=4900),
                         "tags": tags, "categoryId": cfg.get("categoria") or "20"},
             "status": {"privacyStatus": privacidade, "selfDeclaredMadeForKids": False}}
    cab = {"Authorization": f"Bearer {token(cfg)}", "X-Upload-Content-Type": "video/mp4",
           "X-Upload-Content-Length": str(os.path.getsize(arq))}
    try:
        r = requests.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                          json=corpo, headers=cab, timeout=60)
    except requests.RequestException as e:
        raise ErroPostagem(f"YouTube: falha de rede ao iniciar o envio ({type(e).__name__})", True, TEMPORARIO) from e
    if r.status_code != 200:
        raise _erro(r)
    try:
        with open(arq, "rb") as f:
            r2 = requests.put(r.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=900)
    except requests.RequestException as e:
        # o envio pode ter chegado: quem chama marca como "incerto", não repete sozinho
        raise ErroPostagem(f"YouTube: a conexão caiu durante o envio ({type(e).__name__})", True, TEMPORARIO) from e
    if r2.status_code not in (200, 201):
        raise _erro(r2)
    j = r2.json()
    vid = j["id"]
    obtida = (j.get("status") or {}).get("privacyStatus", privacidade)
    detalhe = "" if obtida == privacidade else f"pedido {privacidade}, YouTube deixou {obtida}"
    return resultado(f"https://youtu.be/{vid}", vid, "processando", detalhe)


def verificar(cfg: dict, ext_id: str) -> dict:
    r = requests.get(f"{API}/videos", params={"part": "status,processingDetails", "id": ext_id},
                     headers={"Authorization": f"Bearer {token(cfg)}"}, timeout=30)
    if r.status_code != 200:
        raise _erro(r)
    itens = r.json().get("items") or []
    if not itens:
        return {**resultado(f"https://youtu.be/{ext_id}", ext_id, "erro"), "erro": "vídeo não encontrado no canal",
                "tipo": MIDIA}
    st = itens[0].get("status") or {}
    up = st.get("uploadStatus")
    detalhe = f"privacidade: {st.get('privacyStatus')}"
    if up == "processed":
        return resultado(f"https://youtu.be/{ext_id}", ext_id, "postado", detalhe)
    if up in ("rejected", "failed", "deleted"):
        motivo = st.get("rejectionReason") or st.get("failureReason") or up
        return {**resultado(f"https://youtu.be/{ext_id}", ext_id, "erro", detalhe), "erro": f"YouTube: {motivo}",
                "tipo": MIDIA}
    return resultado(f"https://youtu.be/{ext_id}", ext_id, "processando", detalhe)


def estatisticas(cfg: dict, ids: list) -> dict:
    """{id: {"views", "likes", "comentarios"}} pela API oficial (escopo youtube.readonly)."""
    out = {}
    for i in range(0, len(ids), 50):
        r = requests.get(f"{API}/videos", params={"part": "statistics", "id": ",".join(ids[i:i + 50])},
                         headers={"Authorization": f"Bearer {token(cfg)}"}, timeout=30)
        if r.status_code != 200:
            raise _erro(r)
        for it in r.json().get("items") or []:
            s = it.get("statistics") or {}
            out[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s["likeCount"]) if "likeCount" in s else None,
                             "comentarios": int(s["commentCount"]) if "commentCount" in s else None}
    return out


def _erro(r) -> ErroPostagem:
    try:
        e = r.json().get("error", {})
        motivo = (e.get("errors") or [{}])[0].get("reason", "")
        msg = e.get("message", "")
    except ValueError:
        motivo, msg = "", r.text[:200]
    if motivo in ("uploadLimitExceeded", "quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
        return ErroPostagem(f"YouTube: limite atingido ({motivo}). Volta a postar mais tarde.", False, COTA,
                            retry_after(r))
    if r.status_code == 401:
        return ErroPostagem("YouTube recusou o login. Conecte de novo em Configurações → YouTube.", False, AUTENTICACAO)
    if r.status_code == 403:
        return ErroPostagem(f"YouTube: sem permissão ({motivo or msg})"[:300], False, PERMISSAO)
    if r.status_code in (400, 413, 415):
        return ErroPostagem(f"YouTube recusou o vídeo/dados ({motivo or msg})"[:300], False, MIDIA)
    if r.status_code >= 500 or r.status_code == 429:
        return ErroPostagem(f"YouTube instável ({r.status_code})", True, TEMPORARIO, retry_after(r))
    return ErroPostagem(f"YouTube {r.status_code}: {motivo or msg}"[:300], True, PROCESSAMENTO)
