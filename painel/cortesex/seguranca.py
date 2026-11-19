"""Proteções do painel e dos logs.

- `redigir`: tira chaves, tokens e códigos de qualquer texto antes de ir para log, banco ou tela.
- `RedigirLog`: filtro de logging que aplica `redigir` em toda mensagem.
- `host_permitido` / `origem_permitida`: o painel só responde a 127.0.0.1/localhost na porta dele
  (bloqueia DNS rebinding: um site que faz o próprio domínio apontar para 127.0.0.1).
- `dentro_de`: garante que um caminho fica dentro de uma pasta autorizada.
"""

import logging
import re
import threading
from pathlib import Path

_PADROES = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "AIza***"),
    (re.compile(r"ya29\.[0-9A-Za-z_\-\.]+"), "ya29.***"),
    (re.compile(r"1//[0-9A-Za-z_\-]{20,}"), "1//***"),                 # refresh token do Google
    (re.compile(r"GOCSPX-[0-9A-Za-z_\-]+"), "GOCSPX-***"),
    (re.compile(r"EAA[0-9A-Za-z]{20,}"), "EAA***"),                     # tokens da Meta
    (re.compile(r"\b(act|rft)\.[0-9A-Za-z_\-\.!*]{10,}"), r"\1.***"),   # tokens do TikTok
    (re.compile(r"bot\d{5,}:[0-9A-Za-z_\-]{20,}"), "bot***"),            # token do bot do Telegram
    (re.compile(r"(?i)\b(bearer|oauth)\s+[0-9A-Za-z_\-\.~+/=]{12,}"), r"\1 ***"),
    (re.compile(r"(?i)((?:access_token|refresh_token|client_secret|api_key|key|code|code_verifier|"
                r"fb_exchange_token|token)=)[^&\s\"']+"), r"\1***"),
    (re.compile(r"(?i)(\"(?:access_token|refresh_token|client_secret|api_key|page_token|bot_token)\"\s*:\s*\")"
                r"[^\"]+"), r"\1***"),
]

_conhecidos: set[str] = set()
_lock = threading.Lock()


def registrar_segredo(valor: str) -> None:
    """Valores exatos que nunca podem aparecer em log (as chaves salvas pelo usuário)."""
    if valor and len(valor) >= 8:
        with _lock:
            _conhecidos.add(valor)


def redigir(texto) -> str:
    s = str(texto)
    with _lock:
        conhecidos = sorted(_conhecidos, key=len, reverse=True)
    for v in conhecidos:
        if v in s:
            s = s.replace(v, "***")
    for padrao, troca in _PADROES:
        s = padrao.sub(troca, s)
    return s


class RedigirLog(logging.Filter):
    def filter(self, rec: logging.LogRecord) -> bool:
        try:
            msg = rec.getMessage()
        except Exception:  # noqa: BLE001
            return True
        limpo = redigir(msg)
        if rec.exc_info:
            import traceback
            limpo += "\n" + redigir("".join(traceback.format_exception(*rec.exc_info)))
            rec.exc_info = None
            rec.exc_text = None
        rec.msg, rec.args = limpo, ()
        return True


def hosts_validos(porta: int) -> set[str]:
    return {f"127.0.0.1:{porta}", f"localhost:{porta}"}


def host_permitido(host: str | None, porta: int) -> bool:
    return (host or "").strip().lower() in hosts_validos(porta)


def origem_permitida(origem: str | None, porta: int) -> bool:
    """Sem Origin (navegação direta, alguns navegadores em GET) é aceito; com Origin, só o próprio painel."""
    if not origem:
        return True
    return origem.strip().lower() in {f"http://{h}" for h in hosts_validos(porta)}


def dentro_de(caminho, pasta) -> bool:
    try:
        Path(caminho).resolve().relative_to(Path(pasta).resolve())
        return True
    except (ValueError, OSError):
        return False


CABECALHOS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; "
                                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
}


def limpar_dado_externo(texto, limite=300) -> str:
    """Chat/transcrição/nome de canal: tira controles e marcadores que imitam instruções; corta o tamanho."""
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(texto or ""))
    s = s.replace("<<<", "‹‹‹").replace(">>>", "›››").replace("```", "'''")
    return s[:limite].strip()
