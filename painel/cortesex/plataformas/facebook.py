"""Reels numa Página do Facebook (Graph API). O token de Página pode ser permanente."""

import os

import requests

from cortesex.plataformas import (AUTENTICACAO, CONTA, COTA, MIDIA, PERMISSAO, PROCESSAMENTO, TEMPORARIO,
                                  ErroPostagem, resultado, texto_post, titulo)

V = "v26.0"          # v21.0 é removida pela Meta em 21/01/2027; v26.0 é a atual (jul/2026)
GRAPH = f"https://graph.facebook.com/{V}"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{V}"


def _get(caminho, **params):
    try:
        r = requests.get(f"{GRAPH}/{caminho}", params=params, timeout=30)
    except requests.RequestException as e:
        raise ErroPostagem(f"Facebook: falha de rede ({type(e).__name__})", True, TEMPORARIO) from e
    j = _json(r)
    if "error" in j:
        raise _erro(j)
    return j


def paginas_do_token(app_id: str, app_secret: str, token_usuario: str) -> list:
    """Troca o token curto do usuário por um longo e lista as Páginas com token permanente."""
    longo = _get("oauth/access_token", grant_type="fb_exchange_token", client_id=app_id,
                 client_secret=app_secret, fb_exchange_token=token_usuario)["access_token"]
    contas = _get("me/accounts", access_token=longo, fields="id,name,access_token,tasks")
    return [{"id": p["id"], "nome": p["name"], "token": p["access_token"]} for p in contas.get("data", [])]


def testar(cfg: dict) -> str:
    if not cfg.get("page_token") or not cfg.get("page_id"):
        raise ErroPostagem("Escolha a Página em Configurações → Facebook.", False, CONTA)
    p = _get(cfg["page_id"], fields="name", access_token=cfg["page_token"])
    return f"Conectado à Página: {p.get('name')}"


def _json(r):
    try:
        return r.json()
    except ValueError:
        return {"error": {"message": f"resposta sem JSON ({r.status_code})", "code": -1}}


def postar(cfg: dict, corte: dict) -> dict:
    if not cfg.get("page_token") or not cfg.get("page_id"):
        raise ErroPostagem("Facebook não conectado. Abra Configurações → Facebook.", False, AUTENTICACAO)
    tok, pid = cfg["page_token"], cfg["page_id"]
    try:
        j = _json(requests.post(f"{GRAPH}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok},
                                timeout=60))
    except requests.RequestException as e:
        raise ErroPostagem(f"Facebook: falha de rede ({type(e).__name__})", True, TEMPORARIO) from e
    if "video_id" not in j:
        raise _erro(j)
    vid = j["video_id"]
    tamanho = os.path.getsize(corte["arquivo"])
    try:
        with open(corte["arquivo"], "rb") as f:
            r = requests.post(f"{RUPLOAD}/{vid}", data=f, timeout=900, headers={
                "Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(tamanho)})
    except requests.RequestException as e:
        raise ErroPostagem(f"Facebook: a conexão caiu durante o envio ({type(e).__name__})", True, TEMPORARIO) from e
    if not r.ok or not _json(r).get("success"):
        raise ErroPostagem(f"Facebook: falha no envio do vídeo ({r.status_code})", True, TEMPORARIO)
    try:
        j = _json(requests.post(f"{GRAPH}/{pid}/video_reels", timeout=60, data={
            "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED", "access_token": tok,
            "description": f"{titulo(corte)}\n\n{texto_post(corte)}"}))
    except requests.RequestException as e:
        raise ErroPostagem(f"Facebook: a conexão caiu ao publicar ({type(e).__name__})", True, TEMPORARIO,
                           incerto=True) from e
    if not j.get("success"):
        raise _erro(j)
    return resultado(f"https://www.facebook.com/reel/{vid}", vid, "processando")


def verificar(cfg: dict, ext_id: str) -> dict:
    j = _get(ext_id, fields="status,permalink_url", access_token=cfg["page_token"])
    st = j.get("status") or {}
    url = ("https://www.facebook.com" + j["permalink_url"]) if str(j.get("permalink_url", "")).startswith("/") else \
        (j.get("permalink_url") or f"https://www.facebook.com/reel/{ext_id}")
    vs = st.get("video_status")
    pub = (st.get("publishing_phase") or {}).get("status")
    if vs == "error" or pub == "error":
        return {**resultado(url, ext_id, "erro"), "erro": "Facebook: erro ao processar o vídeo", "tipo": MIDIA}
    if vs == "ready" and pub in (None, "complete"):
        return resultado(url, ext_id, "postado")
    return resultado(url, ext_id, "processando", f"{vs}/{pub}")


def _erro(j) -> ErroPostagem:
    e = j.get("error") or {}
    codigo = e.get("code")
    if codigo in (190, 102):
        return ErroPostagem("Facebook recusou o token da Página. Conecte de novo em Configurações → Facebook.", False,
                            AUTENTICACAO)
    if codigo in (10, 200, 283) or (isinstance(codigo, int) and 200 <= codigo < 300):
        return ErroPostagem(f"Facebook: falta permissão ({e.get('message', '')})"[:300], False, PERMISSAO)
    if codigo in (4, 17, 32, 613, 368):
        return ErroPostagem("Facebook: limite atingido (até 30 Reels por dia). Volta mais tarde.", False, COTA)
    if codigo in (1, 2, -1):
        return ErroPostagem(f"Facebook instável: {e.get('message', '')}"[:300], True, TEMPORARIO)
    if codigo == 100 and "video" in str(e.get("message", "")).lower():
        return ErroPostagem(f"Facebook recusou o vídeo: {e.get('message', '')}"[:300], False, MIDIA)
    return ErroPostagem(f"Facebook: {e.get('message') or j}"[:300], True, PROCESSAMENTO)
