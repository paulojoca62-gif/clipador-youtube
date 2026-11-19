"""Zernio (zernio.com): publica no TikTok e no Facebook pelo app aprovado deles.

Por que existe: o app próprio do TikTok só publica em público depois da auditoria do TikTok. Pelo Zernio,
contas ligadas pelo "TikTok for Business" publicam vídeos em público. No Facebook os cortes vão como Reel.

Fluxo (docs.zernio.com):
1. POST /v1/media/presign → uploadUrl + publicUrl; PUT do arquivo no uploadUrl (sem Authorization).
2. POST /v1/posts com publishNow=true e Idempotency-Key (o mesmo envio repetido devolve o post original).
3. 201 = publicado; 207 = falhou nessa rede (ler platforms[].errorCategory); TikTok pode ficar "uploading"
   por até 48 h → aqui vira "processando" e o verificador consulta GET /v1/posts/{id}.

Reels do Facebook aceitam de 3 a 60 s. Cortes maiores são recortados para os ÚLTIMOS 60 s (o pico fica
no fim do corte adaptativo); só a cópia enviada ao Facebook é recortada, o corte original não muda.
"""

import subprocess
import uuid
from pathlib import Path

import requests

from cortesex import midia, util
from cortesex.plataformas import (AUTENTICACAO, CONTA, COTA, MIDIA, PERMANENTE, PERMISSAO, TEMPORARIO,
                                  ErroPostagem, resultado, retry_after, texto_post, titulo)

BASE = "https://zernio.com/api/v1"
REEL_MAX = 60.0
REDES = ("tiktok", "facebook")
CATEGORIAS = {   # errorCategory do Zernio → (tipo, tentar_de_novo)
    "auth_expired": (AUTENTICACAO, False), "user_content": (MIDIA, False), "user_abuse": (COTA, True),
    "platform_rate_limit": (COTA, True), "quota_exhausted": (COTA, True), "account_issue": (CONTA, False),
    "platform_rejected": (MIDIA, False), "platform_error": (TEMPORARIO, True), "system_error": (TEMPORARIO, True),
}


def _cab(chave):
    return {"Authorization": f"Bearer {chave}"}


def _erro_http(r, onde) -> ErroPostagem:
    try:
        j = r.json()
    except ValueError:
        j = {}
    msg = f"Zernio ({onde}): {j.get('error') or r.status_code}"
    if r.status_code == 401:
        return ErroPostagem(msg + " — confira a chave da API do Zernio", False, AUTENTICACAO)
    if r.status_code in (402, 403):
        return ErroPostagem(msg, False, PERMISSAO)
    if r.status_code == 429:
        return ErroPostagem(msg, True, COTA, retry_after=retry_after(r))
    if r.status_code >= 500:
        return ErroPostagem(msg, True, TEMPORARIO, retry_after=retry_after(r))
    return ErroPostagem(msg, False, PERMANENTE)


def listar_contas(chave, http=requests) -> list:
    if not chave:
        raise ErroPostagem("Coloque a chave da API do Zernio.", False, AUTENTICACAO)
    r = http.get(f"{BASE}/accounts", headers=_cab(chave), timeout=30)
    if r.status_code != 200:
        raise _erro_http(r, "contas")
    return [{"id": a.get("_id"), "plataforma": a.get("platform"), "usuario": a.get("username") or "",
             "nome": a.get("displayName") or "", "ativa": a.get("isActive", True)}
            for a in r.json().get("accounts", []) if a.get("platform") in REDES]


def preparar_reel(arquivo: Path, rodar=subprocess.run) -> Path:
    """Até 60 s: o próprio arquivo. Acima: cópia com os últimos ~59,5 s, recodificada (corte exato)."""
    arquivo = Path(arquivo)
    dur = midia.validar(arquivo, largura=None)["duracao"]
    if dur <= REEL_MAX:
        return arquivo
    saida = arquivo.with_name(arquivo.stem + "_reel60.mp4")
    if saida.exists() and saida.stat().st_mtime >= arquivo.stat().st_mtime:
        return saida
    tmp = saida.with_suffix(".tmp.mp4")
    inicio = max(dur - (REEL_MAX - 0.5), 0)
    cmd = [util.ffmpeg(), "-y", "-v", "error", "-ss", f"{inicio:.2f}", "-i", str(arquivo),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(tmp)]
    r = rodar(cmd, capture_output=True, timeout=900)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise ErroPostagem("não consegui recortar o corte para 60 s (Reel)", True, TEMPORARIO)
    if midia.validar(tmp, largura=None)["duracao"] > REEL_MAX:
        tmp.unlink(missing_ok=True)
        raise ErroPostagem("o recorte para Reel ficou com mais de 60 s", False, MIDIA)
    tmp.replace(saida)
    return saida


class Rede:
    """Mesma interface dos outros destinos (postar/verificar/testar), para uma rede via Zernio."""

    def __init__(self, plataforma, config, http=None, preparar=None):
        self.plataforma, self.config, self.preparar = plataforma, config, preparar
        self.http = http or requests

    def _z(self):
        z = self.config.get("zernio") or {}
        chave, conta = z.get("api_key"), (z.get("contas") or {}).get(self.plataforma)
        if not chave:
            raise ErroPostagem("Zernio sem chave da API (Configurações → Zernio).", False, AUTENTICACAO)
        if not conta:
            raise ErroPostagem(f"Escolha a conta do {self.plataforma} no Zernio (Configurações → Zernio).",
                               False, CONTA)
        return chave, conta

    def _enviar_midia(self, chave, arquivo: Path) -> str:
        r = self.http.post(f"{BASE}/media/presign", headers=_cab(chave), timeout=30,
                           json={"filename": arquivo.name, "contentType": "video/mp4", "size": arquivo.stat().st_size})
        if r.status_code != 200:
            raise _erro_http(r, "upload")
        p = r.json()
        with open(arquivo, "rb") as f:
            up = self.http.put(p["uploadUrl"], data=f, headers={"Content-Type": "video/mp4"}, timeout=900)
        if up.status_code not in (200, 201):
            raise ErroPostagem(f"Zernio: o envio do arquivo falhou ({up.status_code})", True, TEMPORARIO)
        return p["publicUrl"]

    def _corpo(self, corte, conta, url_video):
        entrada = {"platform": self.plataforma, "accountId": conta}
        if self.plataforma == "tiktok":
            leg = (corte.get("textos_plataforma") or {}).get("tiktok") or None
            texto = texto_post(corte, limite=2200, legenda=leg)
            extra = {"tiktokSettings": {"privacy_level": "PUBLIC_TO_EVERYONE", "allow_comment": True,
                                        "allow_duet": False, "allow_stitch": False,
                                        # o usuário aprova cada corte antes (painel/Telegram/modo do agendador)
                                        "content_preview_confirmed": True, "express_consent_given": True}}
        else:
            texto = texto_post(corte, limite=2200)
            entrada["platformSpecificData"] = {"contentType": "reel", "title": titulo(corte)}
            extra = {}
        return {"content": texto, "mediaItems": [{"type": "video", "url": url_video}],
                "platforms": [entrada], "publishNow": True, **extra}

    def _ler_post(self, post) -> dict:
        entrada = next((p for p in post.get("platforms", []) if p.get("platform") == self.plataforma), {})
        st = entrada.get("status") or post.get("status")
        pid = post.get("_id", "")
        if st == "published":
            return resultado(entrada.get("platformPostUrl", ""), pid, "postado")
        if st in ("pending", "processing", "uploading", "publishing", "scheduled"):
            return resultado(entrada.get("platformPostUrl", ""), pid, "processando",
                             "a rede ainda está processando (o TikTok pode levar horas)")
        cat = entrada.get("errorCategory") or "unknown"
        tipo, de_novo = CATEGORIAS.get(cat, (PERMANENTE, False))
        raise ErroPostagem(f"Zernio/{self.plataforma}: {entrada.get('errorMessage') or st or 'falhou'}", de_novo, tipo)

    def postar(self, cfg, corte):
        chave, conta = self._z()
        arquivo = Path(corte["arquivo"])
        if self.plataforma == "facebook":
            arquivo = (self.preparar or preparar_reel)(arquivo)
        url_video = self._enviar_midia(chave, arquivo)
        # mesma postagem (id do registro de envio) → mesma chave: um reenvio devolve o post original
        chave_idem = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                    f"cortesex:{corte['id']}:{self.plataforma}:{corte.get('_postagem', '')}"))
        try:
            r = self.http.post(f"{BASE}/posts", headers={**_cab(chave), "Idempotency-Key": chave_idem},
                               json=self._corpo(corte, conta, url_video), timeout=600)
        except requests.RequestException as e:
            raise ErroPostagem(f"Zernio: conexão perdida ao publicar ({type(e).__name__})", False,
                               TEMPORARIO, incerto=True) from e
        if r.status_code not in (200, 201, 207):
            raise _erro_http(r, "publicar")
        res = self._ler_post(r.json().get("post") or {})
        if res["status"] == "postado" and arquivo.name.endswith("_reel60.mp4"):
            arquivo.unlink(missing_ok=True)
        return res

    def verificar(self, cfg, ext_id):
        chave, _ = self._z()
        r = self.http.get(f"{BASE}/posts/{ext_id}", headers=_cab(chave), timeout=30)
        if r.status_code != 200:
            raise _erro_http(r, "verificar")
        try:
            return self._ler_post(r.json().get("post") or {})
        except ErroPostagem as e:
            return {**resultado("", ext_id, "erro"), "erro": str(e), "tipo": e.tipo}

    def testar(self, cfg=None):
        chave, conta = self._z()
        contas = {c["id"]: c for c in listar_contas(chave, self.http)}
        if conta not in contas:
            raise ErroPostagem("a conta escolhida não está mais conectada no Zernio", False, CONTA)
        c = contas[conta]
        return f"Zernio OK: {self.plataforma} {c['usuario'] or c['nome']}"
