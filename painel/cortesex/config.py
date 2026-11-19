"""Configuração do painel, guardada num JSON na pasta de dados do usuário.

As chaves (YouTube, TikTok, Facebook, Gemini, Telegram) ficam só neste
computador, no arquivo config.json da pasta de dados. A API do painel nunca
devolve uma chave inteira: devolve só se ela está preenchida.
"""

import copy
import json
import os
import sys
import threading
from pathlib import Path

from cortesex import MAX_CANAIS, NOME

SEGREDOS = {
    ("youtube", "client_secret"), ("youtube", "refresh_token"),
    ("tiktok", "client_secret"), ("tiktok", "refresh_token"), ("tiktok", "access_token"),
    ("facebook", "page_token"), ("facebook", "app_secret"),
    ("gemini", "api_key"), ("telegram", "bot_token"),
}

PADRAO = {
    "canais": [],  # [{"nome": "alanzoka", "plataforma": "twitch", "layout": "blur", "ativo": True}]
    "gravacao": {
        "qualidade": "720p60,720p,best",
        "buffer_minutos": 15,
        "fps_saida": 30,
        "legendas": True,
        "modelo_whisper": "small",
        "max_cortes_por_hora": 20,
        "dias_guardar_cortes": 7,
    },
    "deteccao": {
        "janela_segundos": 8,
        "multiplicador": 2.5,
        "minimo_msgs_por_seg": 1.0,
        "pedidos_clip": 4,
        "cooldown_segundos": 75,
        "antes_segundos": 40,
        "depois_segundos": 15,
        "aquecimento_segundos": 90,
    },
    "agendador": {
        "ativo": False,
        "quantidade": 1,          # quantos vídeos...
        "periodo": "hora",        # ...por "hora" ou por "dia"
        "hora_inicio": 0,         # janela em que pode postar (hora local)
        "hora_fim": 24,
        "dias": [0, 1, 2, 3, 4, 5, 6],  # 0 = segunda
        "score_minimo": 2.5,
        "horas_validade": 24,     # só posta cortes mais novos que isso
        "exigir_textos_ia": True,
        "plataformas": {"youtube": True, "tiktok": False, "facebook": False},
    },
    "youtube": {"client_id": "", "client_secret": "", "refresh_token": "", "canal": "", "redirect_uri": "",
                "privacidade": "public", "categoria": "20"},
    "tiktok": {"client_key": "", "client_secret": "", "refresh_token": "", "access_token": "",
               "expira_em": 0, "usuario": "", "redirect_uri": "", "privacidade": ""},
    "facebook": {"app_id": "", "app_secret": "", "page_id": "", "page_nome": "", "page_token": ""},
    "gemini": {"api_key": "", "modelo": "gemini-flash-latest"},
    "telegram": {"bot_token": "", "chat_id": "", "avisar_postagem": True, "avisar_erro": True},
}


def pasta_dados() -> Path:
    if os.environ.get("CORTESEX_DADOS"):
        base = Path(os.environ["CORTESEX_DADOS"])
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NOME
    else:
        base = Path.home() / ".cortesex"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _mesclar(base: dict, novo: dict) -> dict:
    for k, v in novo.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mesclar(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, arquivo: Path | None = None):
        self.arquivo = arquivo or pasta_dados() / "config.json"
        self._lock = threading.RLock()
        self.dados = copy.deepcopy(PADRAO)
        if self.arquivo.exists():
            try:
                _mesclar(self.dados, json.loads(self.arquivo.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass

    def get(self, secao: str, chave: str | None = None, padrao=None):
        with self._lock:
            valor = self.dados.get(secao, padrao)
            if chave is None:
                return copy.deepcopy(valor)
            return copy.deepcopy(valor.get(chave, padrao)) if isinstance(valor, dict) else padrao

    def salvar(self, secao: str, valores) -> None:
        """Grava uma seção. Campos secretos vazios não apagam o valor guardado."""
        with self._lock:
            if isinstance(valores, dict) and isinstance(self.dados.get(secao), dict):
                for k, v in valores.items():
                    if (secao, k) in SEGREDOS and v in ("", None):
                        continue
                    self.dados[secao][k] = v
            else:
                self.dados[secao] = valores
            if secao == "canais":
                self.dados["canais"] = self.dados["canais"][:MAX_CANAIS]
            self._gravar()

    def apagar_segredo(self, secao: str, chave: str) -> None:
        with self._lock:
            self.dados[secao][chave] = ""
            self._gravar()

    def _gravar(self) -> None:
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.dados, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.arquivo)

    def publico(self) -> dict:
        """A configuração para o navegador: segredos viram True/False."""
        with self._lock:
            d = copy.deepcopy(self.dados)
        for secao, chave in SEGREDOS:
            if secao in d and chave in d[secao]:
                d[secao][chave] = bool(d[secao][chave])
        return d
