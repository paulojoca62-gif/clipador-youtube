"""Destinos das postagens. Cada um expõe postar(config, corte) -> url e testar(config) -> texto."""


class ErroPostagem(RuntimeError):
    """Falha que vale registrar no log. `tentar_de_novo` diz se um novo envio pode dar certo."""

    def __init__(self, mensagem, tentar_de_novo=True):
        super().__init__(mensagem)
        self.tentar_de_novo = tentar_de_novo


def texto_post(corte: dict, canal_credito=True, limite=2200) -> str:
    partes = [corte.get("legenda") or "", " ".join(corte.get("hashtags") or [])]
    if canal_credito:
        partes.append(f"Live: twitch.tv/{corte['canal']}")
    return "\n\n".join(p for p in partes if p).strip()[:limite]


def titulo(corte: dict) -> str:
    return (corte.get("titulo") or f"Corte da live de {corte['canal']}")[:100]
