"""Reels numa Página do Facebook (Graph API). O token de Página pode ser permanente."""

import os

import requests

from cortesex.plataformas import ErroPostagem, texto_post, titulo

V = "v21.0"
GRAPH = f"https://graph.facebook.com/{V}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{V}"


def _get(caminho, **params):
    r = requests.get(f"{GRAPH}/{caminho}", params=params, timeout=30)
    j = r.json()
    if "error" in j:
        raise ErroPostagem(f"Facebook: {j['error'].get('message', '')}"[:300], False)
    return j


def paginas_do_token(app_id: str, app_secret: str, token_usuario: str) -> list:
    """Troca o token curto do usuário por um longo e lista as Páginas com token permanente."""
    longo = _get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                 client_secret=app_secret, fb_exchange_token=token_usuario)["access_token"]
    contas = _get("me/accounts", access_token=longo, fields="id,name,access_token,tasks")
    return [{"id": p["id"], "nome": p["name"], "token": p["access_token"]} for p in contas.get("data", [])]


def testar(cfg: dict) -> str:
    if not cfg.get("page_token") or not cfg.get("page_id"):
        raise ErroPostagem("Escolha a Página em Contas → Facebook.", False)
    p = _get(cfg["page_id"], fields="name", access_token=cfg["page_token"])
    return f"Conectado à Página: {p.get('name')}"


def postar(cfg: dict, corte: dict) -> str:
    if not cfg.get("page_token") or not cfg.get("page_id"):
        raise ErroPostagem("Facebook não conectado. Abra Contas → Facebook.", False)
    tok, pid = cfg["page_token"], cfg["page_id"]
    r = requests.post(f"{GRAPH}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok}, timeout=60)
    j = r.json()
    if "video_id" not in j:
        raise _erro(j)
    vid = j["video_id"]
    tamanho = os.path.getsize(corte["arquivo"])
    with open(corte["arquivo"], "rb") as f:
        r = requests.post(f"{RUPLOAD}/{vid}", data=f, timeout=900, headers={
            "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(tamanho)})
    if not r.ok or not r.json().get("success"):
        raise ErroPostagem(f"Facebook: falha no envio do vídeo ({r.status_code} {r.text[:150]})")
    r = requests.post(f"{GRAPH}/{pid}/video_reels", timeout=60, data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED", "access_token": tok,
        "description": f"{titulo(corte)}\n\n{texto_post(corte)}"})
    j = r.json()
    if not j.get("success"):
        raise _erro(j)
    return f"https://www.facebook.com/reel/{vid}"


def _erro(j) -> ErroPostagem:
    e = j.get("error") or {}
    if e.get("code") in (190, 102):
        return ErroPostagem("Facebook recusou o token da Página. Conecte de novo em Contas → Facebook.", False)
    if e.get("code") in (4, 17, 32, 613):
        return ErroPostagem("Facebook: limite de Reels atingido (30 por dia). Volta mais tarde.", False)
    return ErroPostagem(f"Facebook: {e.get('message') or j}"[:300])
