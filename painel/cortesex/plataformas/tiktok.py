"""TikTok pela Content Posting API (Direct Post).

Enquanto o app do TikTok não passar pela auditoria, o TikTok só aceita posts
privados (SELF_ONLY) e só em contas privadas. Depois da auditoria, o mesmo
código posta público, sem mudanças.
"""

import hashlib
import os
import secrets
import time
from urllib.parse import urlencode

import requests

from cortesex.plataformas import (AUTENTICACAO, COTA, MIDIA, PERMISSAO, PRIVACIDADE, PROCESSAMENTO, TEMPORARIO,
                                  ErroPostagem, resultado, retry_after, texto_post, titulo)

AUTH = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2"
ESCOPOS = "user.info.basic,video.publish"


def novo_verificador() -> str:
    """PKCE: o app de computador do TikTok exige code_verifier/code_challenge."""
    return secrets.token_urlsafe(48)[:64]


def desafio(verificador: str) -> str:
    # O TikTok usa o SHA-256 em hexadecimal (não em base64url como o padrão OAuth).
    return hashlib.sha256(verificador.encode()).hexdigest()


def link_login(cfg: dict, redirect_uri: str, estado: str, verificador: str = "") -> str:
    params = {"client_key": cfg["client_key"], "scope": ESCOPOS, "response_type": "code",
              "redirect_uri": redirect_uri, "state": estado}
    if verificador:
        params.update(code_challenge=desafio(verificador), code_challenge_method="S256")
    return AUTH + "?" + urlencode(params)


def _guardar(cfg: dict, j: dict) -> dict:
    return {"access_token": j["access_token"], "refresh_token": j.get("refresh_token") or cfg.get("refresh_token"),
            "expira_em": time.time() + int(j.get("expires_in", 86400)) - 300}


def trocar_codigo(cfg: dict, codigo: str, redirect_uri: str, verificador: str = "") -> dict:
    dados = {"client_key": cfg["client_key"], "client_secret": cfg["client_secret"], "code": codigo,
             "grant_type": "authorization_code", "redirect_uri": redirect_uri}
    if verificador:
        dados["code_verifier"] = verificador
    r = requests.post(f"{API}/oauth/token/", data=dados, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise ErroPostagem(f"O TikTok não entregou o login: {j.get('error_description') or j.get('error')}", False,
                           AUTENTICACAO)
    return _guardar(cfg, j)


def renovar(cfg: dict) -> dict | None:
    """Devolve os campos novos do token se precisou renovar, ou None."""
    if not cfg.get("refresh_token"):
        raise ErroPostagem("TikTok não conectado. Abra Configurações → TikTok e conecte.", False, AUTENTICACAO)
    if cfg.get("access_token") and time.time() < float(cfg.get("expira_em") or 0):
        return None
    r = requests.post(f"{API}/oauth/token/", data={
        "client_key": cfg["client_key"], "client_secret": cfg["client_secret"],
        "grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]}, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise ErroPostagem("O login do TikTok expirou. Conecte de novo em Configurações → TikTok.", False, AUTENTICACAO)
    return _guardar(cfg, j)


def _api(cfg, caminho, corpo):
    try:
        r = requests.post(f"{API}{caminho}", json=corpo, timeout=60,
                          headers={"Authorization": f"Bearer {cfg['access_token']}",
                                   "Content-Type": "application/json; charset=UTF-8"})
    except requests.RequestException as e:
        raise ErroPostagem(f"TikTok: falha de rede ({type(e).__name__})", True, TEMPORARIO) from e
    try:
        j = r.json()
    except ValueError:
        raise ErroPostagem(f"TikTok respondeu sem JSON ({r.status_code})", True, TEMPORARIO, retry_after(r)) from None
    erro = (j.get("error") or {})
    codigo = erro.get("code")
    if codigo not in (None, "", "ok"):
        if codigo == "unaudited_client_can_only_post_to_private_accounts":
            raise ErroPostagem("TikTok: app sem auditoria só posta em conta PRIVADA. Deixe a conta privada "
                               "ou peça a auditoria do app.", False, PRIVACIDADE)
        if codigo in ("spam_risk_too_many_posts", "spam_risk_user_banned_from_posting", "rate_limit_exceeded",
                      "spam_risk_too_many_pending_share"):
            raise ErroPostagem(f"TikTok: limite de posts atingido ({codigo}).", False, COTA, retry_after(r))
        if codigo in ("access_token_invalid", "token_not_authorized_for_specified_endpoint"):
            raise ErroPostagem(f"TikTok recusou o login ({codigo}). Conecte de novo.", False, AUTENTICACAO)
        if codigo in ("scope_not_authorized", "scope_permission_missed"):
            raise ErroPostagem(f"TikTok: falta permissão ({codigo}).", False, PERMISSAO)
        if codigo in ("invalid_file_upload", "file_format_check_failed", "duration_check_failed",
                      "frame_rate_check_failed", "picture_size_check_failed", "video_pull_failed"):
            raise ErroPostagem(f"TikTok recusou o vídeo ({codigo}).", False, MIDIA)
        if codigo == "privacy_level_option_mismatch":
            raise ErroPostagem("TikTok: a privacidade pedida não é permitida para essa conta.", False, PRIVACIDADE)
        if r.status_code >= 500:
            raise ErroPostagem(f"TikTok instável ({codigo})", True, TEMPORARIO, retry_after(r))
        raise ErroPostagem(f"TikTok: {codigo} {erro.get('message', '')}"[:300], True, PROCESSAMENTO)
    return j.get("data") or {}


def info(cfg: dict) -> dict:
    return _api(cfg, "/post/publish/creator_info/query/", {})


def testar(cfg: dict) -> str:
    d = info(cfg)
    return (f"Conectado como @{d.get('creator_username', '?')}. "
            f"Privacidade permitida: {', '.join(d.get('privacy_level_options') or [])}")


PARTE = 10_000_000            # bytes por parte quando o vídeo é grande
LIMITE_UMA_PARTE = 64_000_000


def plano_de_partes(tamanho: int) -> dict:
    """Media Transfer Guide: total_chunk_count = tamanho // chunk_size (para baixo); a última parte leva o resto.
    Arquivo até 64 MB vai numa parte só (chunk_size = tamanho)."""
    if tamanho <= LIMITE_UMA_PARTE:
        return {"chunk_size": tamanho, "total": 1, "faixas": [(0, tamanho - 1)]}
    total = tamanho // PARTE
    faixas = [(i * PARTE, (i + 1) * PARTE - 1) for i in range(total - 1)] + [((total - 1) * PARTE, tamanho - 1)]
    return {"chunk_size": PARTE, "total": total, "faixas": faixas}


def postar(cfg: dict, corte: dict) -> dict:
    d = info(cfg)
    opcoes = d.get("privacy_level_options") or ["SELF_ONLY"]
    privacidade = cfg.get("privacidade") if cfg.get("privacidade") in opcoes else (
        "PUBLIC_TO_EVERYONE" if "PUBLIC_TO_EVERYONE" in opcoes else opcoes[0])
    arq = corte["arquivo"]
    tamanho = os.path.getsize(arq)
    partes = plano_de_partes(tamanho)
    legenda_tt = (corte.get("textos_plataforma") or {}).get("tiktok")
    legenda = f"{titulo(corte)}\n\n{texto_post(corte, limite=2000, legenda=legenda_tt)}"[:2200]
    ini = _api(cfg, "/post/publish/video/init/", {
        "post_info": {"title": legenda, "privacy_level": privacidade, "disable_comment": False,
                      "disable_duet": False, "disable_stitch": False},
        "source_info": {"source": "FILE_UPLOAD", "video_size": tamanho, "chunk_size": partes["chunk_size"],
                        "total_chunk_count": partes["total"]}})
    publish_id = ini.get("publish_id", "")
    with open(arq, "rb") as f:
        for ini_b, fim_b in partes["faixas"]:
            f.seek(ini_b)
            bloco = f.read(fim_b - ini_b + 1)
            try:
                r = requests.put(ini["upload_url"], data=bloco, timeout=900, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(bloco)),
                    "Content-Range": f"bytes {ini_b}-{fim_b}/{tamanho}"})
            except requests.RequestException as e:
                ultima = fim_b == tamanho - 1     # só a última parte pode ter completado o envio
                raise ErroPostagem(f"TikTok: a conexão caiu durante o envio ({type(e).__name__})", True, TEMPORARIO,
                                   incerto=ultima) from e
            if r.status_code not in (200, 201, 206):
                raise ErroPostagem(f"TikTok: falha no envio do vídeo ({r.status_code})", True, TEMPORARIO,
                                   retry_after(r))
    usuario = d.get("creator_username") or cfg.get("usuario") or ""
    detalhe = "" if privacidade == "PUBLIC_TO_EVERYONE" else f"privacidade {privacidade}"
    return resultado(f"https://www.tiktok.com/@{usuario}" if usuario else "", publish_id, "processando", detalhe)


def verificar(cfg: dict, ext_id: str) -> dict:
    d = _api(cfg, "/post/publish/status/fetch/", {"publish_id": ext_id})
    st = d.get("status", "")
    usuario = cfg.get("usuario") or ""
    ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
    url = (f"https://www.tiktok.com/@{usuario}/video/{ids[0]}" if ids and usuario
           else (f"https://www.tiktok.com/@{usuario}" if usuario else ""))
    if st == "PUBLISH_COMPLETE":
        return resultado(url, ext_id, "postado")
    if st == "FAILED":
        return {**resultado(url, ext_id, "erro"), "erro": f"TikTok: {d.get('fail_reason') or 'falhou'}",
                "tipo": MIDIA}
    return resultado(url, ext_id, "processando", st)
