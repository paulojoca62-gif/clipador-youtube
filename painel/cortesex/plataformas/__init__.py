"""Destinos das postagens.

Cada módulo expõe:
- postar(cfg, corte) -> {"url", "ext_id", "status": "postado" | "processando", "detalhe"}
- verificar(cfg, ext_id) -> mesmo formato (status também pode ser "erro" com "erro"/"tipo")
- testar(cfg) -> texto
"""

# Tipos de erro: decidem o que o agendador faz depois.
TEMPORARIO = "temporario"        # rede/servidor: tenta de novo com espera crescente
AUTENTICACAO = "autenticacao"    # login vencido/revogado: pausa até o usuário reconectar
PERMISSAO = "permissao"          # escopo/permissão faltando: pausa até o usuário resolver
COTA = "cota"                    # limite do dia/taxa: pausa longa
PRIVACIDADE = "privacidade"      # conta/app não permitem a privacidade pedida: pausa até resolver
MIDIA = "midia"                  # o vídeo foi recusado: não tenta esse corte de novo nessa rede
PROCESSAMENTO = "processamento"  # a rede aceitou mas falhou ao processar
CONTA = "conta"                  # conta/página/canal com problema
PERMANENTE = "permanente"
EXIGEM_USUARIO = {AUTENTICACAO, PERMISSAO, PRIVACIDADE, CONTA}


class ErroPostagem(RuntimeError):
    """Falha que vale registrar no log. `tentar_de_novo` diz se um novo envio pode dar certo."""

    def __init__(self, mensagem, tentar_de_novo=True, tipo=None, retry_after=None, incerto=False):
        super().__init__(mensagem)
        self.incerto = incerto      # o vídeo pode ter sido publicado mesmo assim (resposta perdida)
        self.tentar_de_novo = tentar_de_novo
        self.tipo = tipo or (TEMPORARIO if tentar_de_novo else PERMANENTE)
        self.retry_after = retry_after


def nunca_conectou(e) -> bool:
    """A conexão nem abriu (sem internet, DNS falhou, recusada, tempo de conexão): nada chegou à rede social,
    então NÃO pode ter publicado. Diferente de cair no meio do envio (aí é "incerto")."""
    import requests
    if isinstance(e, requests.ConnectTimeout):
        return True
    atual, vistos = e, 0
    while atual is not None and vistos < 8:
        nome = type(atual).__name__
        if nome in ("NameResolutionError", "NewConnectionError", "ConnectTimeoutError") or \
                isinstance(atual, (ConnectionRefusedError, __import__("socket").gaierror)):
            return True
        atual = getattr(atual, "reason", None) or atual.__cause__ or atual.__context__ or \
            (atual.args[0] if getattr(atual, "args", None) and isinstance(atual.args[0], BaseException) else None)
        vistos += 1
    return False


def resultado(url="", ext_id="", status="postado", detalhe="") -> dict:
    return {"url": url, "ext_id": str(ext_id or ""), "status": status, "detalhe": detalhe}


def texto_post(corte: dict, canal_credito=True, limite=2200, legenda=None) -> str:
    partes = [legenda if legenda is not None else (corte.get("legenda") or ""), " ".join(corte.get("hashtags") or [])]
    if canal_credito:
        partes.append(f"Live: twitch.tv/{corte['canal']}")
    return "\n\n".join(p for p in partes if p).strip()[:limite]


def titulo(corte: dict) -> str:
    return (corte.get("titulo") or f"Corte da live de {corte['canal']}")[:100]


def retry_after(resposta) -> float | None:
    try:
        v = float(resposta.headers.get("Retry-After"))
        return v if v > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None
