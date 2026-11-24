"""Configuração do painel (config.json) e chaves (cofre segredos.dat), na pasta de dados do usuário.

As chaves (YouTube, TikTok, Facebook, Gemini, Telegram) ficam no cofre: cifradas com a DPAPI no
Windows. O config.json guarda só o resto. A API do painel nunca devolve uma chave: só se ela existe.

Compatibilidade: um config.json antigo (até a 1.x), que tinha as chaves dentro, é migrado sozinho
na primeira abertura: as chaves vão para o cofre, o cofre é conferido, e só então saem do JSON.
"""

import copy
import json
import os
import sys
import threading
from pathlib import Path

from cortesex import MAX_CANAIS, NOME, seguranca
from cortesex.segredos import Cofre, protegido

SEGREDOS = {
    ("youtube", "client_secret"), ("youtube", "refresh_token"),
    ("tiktok", "client_secret"), ("tiktok", "refresh_token"), ("tiktok", "access_token"),
    ("facebook", "page_token"), ("facebook", "app_secret"),
    ("gemini", "api_key"), ("telegram", "bot_token"), ("zernio", "api_key"),
    ("openrouter", "api_key"),
}

MODOS = ("manual", "semiautomatico", "automatico")
PLATAFORMAS_LIVE = ("twitch", "kick")      # kick: sem chat (pico detectado pelo áudio)
STATUS_AUTORIZACAO = ("nao_registrada", "pendente", "autorizado", "negado", "revogada")

AUTORIZACAO_PADRAO = {
    "status": "nao_registrada",      # o usuário registra; o programa não presume nada
    "data_registro": "",
    "data_autorizacao": "",
    "validade": "",                  # AAAA-MM-DD; vazio = sem validade
    "observacoes": "",
    "gravar": True,
    "editar": True,
    "publicar": False,
    "redes": [],                     # ["youtube", "tiktok", "facebook"]
}

PADRAO = {
    "canais": [],  # [{"nome", "plataforma", "layout", "ativo", "cam", "autorizacao"}]
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
        "agrupar_segundos": 30,       # picos a menos disso do anterior (ainda não capturado) viram um evento só
        "audio_limiar_db": 9,         # Kick (sem chat): reação = volume X dB acima do normal do streamer
        "audio_minimo_db": -35,       # e acima deste volume absoluto (ignora silêncio que "pula")
    },
    "corte": {
        "adaptativo": True,           # início/fim em pausas da fala, dentro dos limites abaixo
        "duracao_min": 20,
        "duracao_max": 90,
        "contexto_antes_max": 60,     # quanto antes do pico a fonte guarda
        "contexto_depois_max": 30,
    },
    "legendas": {
        "estilo": "classico",         # classico | impacto | narrativo
        "fonte": "DejaVu Sans",
        "tamanho": 74,
        "cor": "#FFFFFF",
        "cor_destaque": "#FFD23F",
        "contorno": 6,
        "posicao": "baixo",           # baixo | meio | alto
        "margem_inferior": 420,       # px a partir de baixo (área segura dos botões das redes)
        "margem_lateral": 90,
        "max_caracteres": 22,
        "animacao": True,
    },
    "pontuacao": {
        "versao": 1,
        "pesos": {"chat": 20, "reacao": 20, "contexto": 20, "entretenimento": 15,
                  "audio": 10, "inicio_fim": 10, "retencao": 5},
    },
    "processamento": {
        "workers_montagem": 1,        # cortes montados ao mesmo tempo (FFmpeg + Whisper)
        "timeout_ffmpeg": 900,
        "tentativas_etapa": 3,
    },
    "armazenamento": {
        "dias_guardar_fontes": 3,     # janela larga usada para re-render e editor de legendas
        "manter_publicados": False,
        "apagar_ao_descartar": False, # True: o vídeo descartado sai do disco na hora (sem "recuperar" depois)
        "dias_guardar_descartados": 1,
        "minimo_livre_gb": 5,
    },
    "agendador": {
        "ativo": False,
        "modo": "semiautomatico",     # manual | semiautomatico | automatico
        "quantidade": 1,
        "periodo": "hora",
        "hora_inicio": 0,
        "hora_fim": 24,
        "dias": [0, 1, 2, 3, 4, 5, 6],
        "score_minimo": 2.5,          # nota do pico (x acima do normal)
        "score_editorial_minimo": 0,  # 0–100; 0 = não filtra
        "ordenar_por": "editorial",   # editorial | pico | recente
        "evitar_mesmo_canal_seguido": False,
        "horas_validade": 24,
        "exigir_textos_ia": True,
        "plataformas": {"youtube": True, "tiktok": False, "facebook": False},
    },
    "analytics": {"ativo": True, "horas_entre_coletas": 6, "dias_acompanhar": 30},
    "atualizacoes": {
        "modo": "automatico",          # automatico | perguntar | desligado
        "horas_entre_checagens": 6,
        "espera_maxima_horas": 24,     # se as lives não param, atualiza assim que nada estiver em processamento
    },
    "youtube": {"client_id": "", "client_secret": "", "refresh_token": "", "canal": "", "redirect_uri": "",
                "privacidade": "public", "categoria": "20"},
    "zernio": {"api_key": "", "contas": {"tiktok": "", "facebook": ""}, "nomes": {"tiktok": "", "facebook": ""}},
    "tiktok": {"via": "direto",        # direto | zernio
               "client_key": "", "client_secret": "", "refresh_token": "", "access_token": "",
               "expira_em": 0, "usuario": "", "redirect_uri": "", "privacidade": ""},
    "facebook": {"via": "direto", "app_id": "", "app_secret": "", "page_id": "", "page_nome": "", "page_token": ""},
    "gemini": {"api_key": "", "modelo": "gemini-flash-latest"},
    "openrouter": {"api_key": "", "ativo": True,
                   "modelos": ""},     # vazio = automático (os grátis do momento que respondem em JSON)
    "telegram": {"bot_token": "", "chat_id": "", "avisar_postagem": True, "avisar_erro": True,
                 "revisar_pelo_telegram": True,   # corte pronto chega com vídeo e botões Aprovar/Descartar/Postar
                 "enviar_video": True},
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


def normalizar_canal(c: dict) -> dict:
    c = dict(c)
    aut = copy.deepcopy(AUTORIZACAO_PADRAO)
    aut.update(c.get("autorizacao") or {})
    if aut["status"] not in STATUS_AUTORIZACAO:
        aut["status"] = "nao_registrada"
    aut["redes"] = [r for r in aut.get("redes") or [] if r in ("youtube", "tiktok", "facebook")]
    c["autorizacao"] = aut
    if c.get("plataforma") not in PLATAFORMAS_LIVE:
        c["plataforma"] = "twitch"
    c.setdefault("layout", "blur")
    c.setdefault("ativo", True)
    return c


class Config:
    def __init__(self, arquivo: Path | None = None, cofre: Cofre | None = None):
        self.arquivo = arquivo or pasta_dados() / "config.json"
        self.cofre = cofre or Cofre(self.arquivo.with_name("segredos.dat"))
        self._lock = threading.RLock()
        self.dados = copy.deepcopy(PADRAO)
        self.migracoes: list[str] = []
        existia = self.arquivo.exists()
        lido = {}
        if existia:
            try:
                lido = json.loads(self.arquivo.read_text(encoding="utf-8"))
                _mesclar(self.dados, lido)
            except (ValueError, OSError):
                lido = {}
        self.dados["canais"] = [normalizar_canal(c) for c in self.dados.get("canais", [])][:MAX_CANAIS]
        if existia and "modo" not in (lido.get("agendador") or {}):
            # 1.x não tinha modos: quem já postava automático continua automático.
            self.dados["agendador"]["modo"] = "automatico" if self.dados["agendador"].get("ativo") else "semiautomatico"
            self.migracoes.append(f"modo do agendador definido como {self.dados['agendador']['modo']}")
        self.cofre_falhou = ""
        try:
            self._migrar_segredos()
        except Exception as e:  # noqa: BLE001
            # AUD-M7: cofre indisponível (DPAPI falhou). As chaves continuam no config.json, sem perda;
            # o programa abre em modo degradado e avisa. Nada é apagado.
            self.cofre_falhou = f"O cofre de chaves falhou ({type(e).__name__}); as chaves continuam no config.json."
            self.migracoes.append("cofre indisponível: chaves mantidas no config.json (modo degradado)")
        for secao, chave in SEGREDOS:
            seguranca.registrar_segredo(self.cofre.get(secao, chave))
            seguranca.registrar_segredo((self.dados.get(secao) or {}).get(chave) or "")
        if existia and self.migracoes:
            self._gravar()

    def _migrar_segredos(self):
        achados = {}
        for secao, chave in SEGREDOS:
            v = (self.dados.get(secao) or {}).get(chave)
            if v:
                achados[f"{secao}.{chave}"] = v
        if not achados:
            return
        self.cofre.definir(achados)      # grava e confere antes de tirar do JSON
        for k in achados:
            secao, chave = k.split(".")
            self.dados[secao][chave] = ""
        self.migracoes.append(f"{len(achados)} chave(s) movidas do config.json para o cofre")

    def get(self, secao: str, chave: str | None = None, padrao=None):
        with self._lock:
            valor = copy.deepcopy(self.dados.get(secao, padrao))
        if isinstance(valor, dict):
            for s, k in SEGREDOS:
                if s == secao:
                    valor[k] = self.cofre.get(s, k) or (valor.get(k) if self.cofre_falhou else "")
        if chave is None:
            return valor
        return valor.get(chave, padrao) if isinstance(valor, dict) else padrao

    def salvar(self, secao: str, valores) -> None:
        """Grava uma seção. Campos secretos vazios não apagam o valor guardado."""
        with self._lock:
            if isinstance(valores, dict) and isinstance(self.dados.get(secao), dict):
                novos_segredos = {}
                for k, v in valores.items():
                    if (secao, k) in SEGREDOS:
                        if v not in ("", None, True, False):
                            novos_segredos[f"{secao}.{k}"] = v
                            seguranca.registrar_segredo(str(v))
                        continue
                    if isinstance(v, dict) and isinstance(self.dados[secao].get(k), dict):
                        _mesclar(self.dados[secao][k], v)
                    else:
                        self.dados[secao][k] = v
                if novos_segredos:
                    try:
                        self.cofre.definir(novos_segredos)
                    except Exception:  # noqa: BLE001
                        if not self.cofre_falhou:
                            raise
                        for k, v in novos_segredos.items():   # modo degradado: guarda no JSON, avisando
                            self.dados[secao][k.split(".")[1]] = v
            else:
                self.dados[secao] = valores
            if secao == "canais":
                self.dados["canais"] = [normalizar_canal(c) for c in self.dados["canais"]][:MAX_CANAIS]
            if secao == "agendador" and self.dados["agendador"].get("modo") not in MODOS:
                self.dados["agendador"]["modo"] = "semiautomatico"
            self._gravar()

    def apagar_segredo(self, secao: str, chave: str) -> None:
        self.cofre.definir({f"{secao}.{chave}": ""})

    def _gravar(self) -> None:
        with self._lock:
            dados = copy.deepcopy(self.dados)
        if not self.cofre_falhou:
            for secao, chave in SEGREDOS:     # nunca escreve chave no JSON (salvo no modo degradado)
                if secao in dados and chave in dados[secao]:
                    dados[secao][chave] = ""
        tmp = self.arquivo.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.arquivo)

    def publico(self) -> dict:
        """A configuração para o navegador: segredos viram True/False."""
        with self._lock:
            d = copy.deepcopy(self.dados)
        for secao, chave in SEGREDOS:
            if secao in d and chave in d[secao]:
                d[secao][chave] = bool(self.cofre.get(secao, chave) or (self.cofre_falhou and d[secao][chave]))
        d["_cofre"] = {"cifrado": protegido() and not self.cofre_falhou, "erro": self.cofre.erro or self.cofre_falhou}
        return d

    def canal(self, nome: str) -> dict | None:
        for c in self.get("canais"):
            if c.get("nome") == nome:
                return c
        return None
