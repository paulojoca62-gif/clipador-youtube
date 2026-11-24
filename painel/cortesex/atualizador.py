"""Atualização automática pelo GitHub (release "cortesex-latest").

Fluxo:
1. A cada N horas consulta a API pública do GitHub e lê `versao.json` do release (build, commit, sha256).
2. Se o build publicado for maior que o instalado, baixa o instalador para a pasta de dados e confere
   o SHA-256 (o do `versao.json` E o que o próprio GitHub calcula do arquivo). Arquivo diferente = descartado.
3. Espera o programa ficar parado: nenhum envio em andamento, nada sendo processado e nenhuma live
   gravando. Se as lives não pararem em `espera_maxima_horas`, atualiza assim que o processamento e os
   envios estiverem livres (a gravação perde ~1 min de buffer e volta sozinha).
4. Agenda uma tarefa do Windows que reabre o programa (rede de segurança), roda o próprio instalador
   em modo silencioso (sem PowerShell: antivírus costumam barrar PowerShell oculto) e fecha o programa.
   O instalador reabre o CortesEX-PRESS no fim; se ele for bloqueado, a tarefa reabre assim mesmo.
   Ao abrir, o programa apaga a tarefa.

Segurança: só baixa do repositório e da tag fixos abaixo, por HTTPS, com limite de tamanho; não executa
nada que não tenha o hash conferido. Cada build é tentado no máximo 3 vezes (evita laço de reinstalação).
Só aplica no executável do Windows; rodando pelo código-fonte, apenas informa.
"""

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

from cortesex import db as D

log = logging.getLogger("cortesex")

REPO = "paulojoca62-gif/clipador-youtube"
TAG = "cortesex-latest"
API = f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}"
PREFIXO = f"https://github.com/{REPO}/releases/download/{TAG}/"
INSTALADOR = "CortesEX-PRESS-Instalador.exe"
VERSAO_JSON = "versao.json"
TAMANHO_MAXIMO = 600 * 1024 * 1024
MAX_TENTATIVAS = 3
MODOS = ("automatico", "perguntar", "desligado")


def build_atual() -> tuple[int, str]:
    try:
        from cortesex._build import BUILD, COMMIT
        return int(BUILD), str(COMMIT)
    except Exception:  # noqa: BLE001  (rodando pelo código-fonte)
        return 0, ""


def pode_aplicar() -> bool:
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False)) and build_atual()[0] > 0


class ErroAtualizacao(RuntimeError):
    pass


def consultar(get=requests.get) -> dict:
    """Lê o release no GitHub. Devolve {build, commit, sha256, tamanho, url}."""
    r = get(API, headers={"Accept": "application/vnd.github+json"}, timeout=20)
    if r.status_code == 404:
        raise ErroAtualizacao("nenhuma versão publicada agora (o release pode estar sendo atualizado)")
    if r.status_code != 200:
        raise ErroAtualizacao(f"o GitHub respondeu {r.status_code}")
    assets = {a.get("name"): a for a in (r.json().get("assets") or [])}
    inst, ver = assets.get(INSTALADOR), assets.get(VERSAO_JSON)
    if not inst or not ver:
        raise ErroAtualizacao("o release não tem o instalador e o versao.json")
    for a in (inst, ver):
        if not str(a.get("browser_download_url", "")).startswith(PREFIXO):
            raise ErroAtualizacao("endereço de download fora do repositório oficial")
    rv = get(ver["browser_download_url"], timeout=20)
    if rv.status_code != 200 or len(rv.content) > 10_000:
        raise ErroAtualizacao("não consegui ler o versao.json")
    try:
        v = json.loads(rv.content)
        info = {"build": int(v["build"]), "commit": str(v.get("commit", ""))[:40],
                "sha256": str(v["sha256"]).lower(), "tamanho": int(v["tamanho"]), "url": inst["browser_download_url"]}
    except (ValueError, KeyError, TypeError) as e:
        raise ErroAtualizacao(f"versao.json inválido ({type(e).__name__})") from None
    if len(info["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in info["sha256"]):
        raise ErroAtualizacao("versao.json sem SHA-256 válido")
    digest = str(inst.get("digest") or "")
    if digest and digest.lower() != "sha256:" + info["sha256"]:
        raise ErroAtualizacao("o hash informado pelo GitHub não confere com o versao.json")
    if int(inst.get("size") or info["tamanho"]) != info["tamanho"] or not 0 < info["tamanho"] <= TAMANHO_MAXIMO:
        raise ErroAtualizacao("tamanho do instalador não confere")
    return info


def baixar(info: dict, pasta: Path, get=requests.get) -> Path:
    """Baixa o instalador e confere tamanho e SHA-256. Só deixa o arquivo final se tudo bater."""
    pasta.mkdir(parents=True, exist_ok=True)
    final = pasta / f"CortesEX-PRESS-build{info['build']}.exe"
    if final.exists() and _sha256(final) == info["sha256"]:
        return final
    tmp = final.with_suffix(".parcial")
    h, total = hashlib.sha256(), 0
    try:
        with get(info["url"], stream=True, timeout=60) as r:
            if r.status_code != 200:
                raise ErroAtualizacao(f"download respondeu {r.status_code}")
            with open(tmp, "wb") as f:
                for parte in r.iter_content(1024 * 1024):
                    total += len(parte)
                    if total > info["tamanho"]:
                        raise ErroAtualizacao("o download veio maior que o esperado")
                    h.update(parte)
                    f.write(parte)
        if total != info["tamanho"] or h.hexdigest() != info["sha256"]:
            raise ErroAtualizacao("o arquivo baixado não confere (hash/tamanho); descartado")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    for antigo in pasta.glob("CortesEX-PRESS-build*.exe"):
        if antigo != final:
            antigo.unlink(missing_ok=True)
    return final


def _sha256(arq: Path) -> str:
    h = hashlib.sha256()
    with open(arq, "rb") as f:
        for parte in iter(lambda: f.read(1024 * 1024), b""):
            h.update(parte)
    return h.hexdigest()


TAREFA = "CortesEX-PRESS reabrir"


def comando_instalacao(instalador: Path, registro: Path) -> list:
    """O próprio instalador (Inno Setup), sem PowerShell: fecha o programa, instala em silêncio e,
    com /REABRIR, abre o CortesEX-PRESS de novo no fim. O log vai para `registro` com extensão .log."""
    return [str(instalador), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS", f"/LOG={registro.with_suffix('.log')}", "/REABRIR"]


def comando_rede_de_seguranca(exe: Path) -> list:
    """Tarefa agendada do Windows que abre o programa a cada 2 min até ele iniciar (ele apaga a tarefa
    ao abrir; se já estiver aberto, a nova cópia só sai). Cobre o caso de o instalador ser bloqueado."""
    return ["schtasks", "/Create", "/TN", TAREFA, "/SC", "MINUTE", "/MO", "2", "/F",
            "/TR", f'"{exe}" --sem-navegador']


def apagar_rede_de_seguranca(rodar=subprocess.run):
    if sys.platform != "win32":
        return
    try:
        rodar(["schtasks", "/Delete", "/TN", TAREFA, "/F"], capture_output=True, timeout=20,
              creationflags=0x08000000)
    except Exception:  # noqa: BLE001
        pass


class Atualizador:
    def __init__(self, motor, pasta: Path, encerrar=None, get=requests.get, popen=subprocess.Popen,
                 relogio=time.time, rodar=subprocess.run):
        self.motor, self.pasta, self.encerrar = motor, Path(pasta), encerrar
        self.get, self.popen, self.relogio, self.rodar = get, popen, relogio, rodar
        self.dir = self.pasta / "atualizacoes"
        self.arq_estado = self.dir / "estado.json"
        self.build, self.commit = build_atual()
        self._lock = threading.Lock()
        self.parar = threading.Event()
        self.info = None
        self.instalador = None
        self.pendente_desde = 0.0
        self.estado = {"build_atual": self.build, "commit": self.commit, "build_disponivel": None,
                       "situacao": "atualizado", "mensagem": "", "ultima_checagem": 0, "pode_aplicar": pode_aplicar()}
        self._conferir_ultima_instalacao()

    # ----- configuração e estado persistente
    def cfg(self) -> dict:
        c = dict(self.motor.config.get("atualizacoes") or {})
        if c.get("modo") not in MODOS:
            c["modo"] = "automatico"
        return c

    def _ler(self) -> dict:
        try:
            return json.loads(self.arq_estado.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _gravar(self, d: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.arq_estado.write_text(json.dumps(d), encoding="utf-8")

    def _conferir_ultima_instalacao(self):
        d = self._ler()
        if not d.get("build"):
            return
        codigo = ""
        try:
            codigo = (self.dir / "ultima_instalacao.txt").read_text(encoding="utf-8").strip()
        except OSError:
            pass
        if self.build >= int(d["build"]):
            log.info("atualizado para o build %s", self.build)
            self._gravar({})
            for f in self.dir.glob("CortesEX-PRESS-build*.exe"):
                f.unlink(missing_ok=True)
        else:
            log.warning("a atualização para o build %s não concluiu%s; veja %s", d["build"],
                        f" (código {codigo})" if codigo else "", self.dir / "ultima_instalacao.log")

    def _situacao(self, situacao, mensagem=""):
        self.estado.update(situacao=situacao, mensagem=mensagem)

    # ----- quando dá para atualizar sem atrapalhar
    def motivo_ocupado(self, ignorar_gravacao=False) -> str:
        m = self.motor
        if m.banco.postagens_status("enviando"):
            return "há um envio em andamento"
        estados = m.banco.contagem_estados()
        em_andamento = (D.CAPTURANDO, D.TRANSCREVENDO, D.MONTANDO, D.GERANDO_TEXTOS, D.DETECTADO, D.CAPTURADO,
                        D.TRANSCRITO, D.MONTADO)
        if sum(n for e, n in estados.items() if e in em_andamento):
            return "há cortes sendo processados"
        if not ignorar_gravacao and any(getattr(c, "gravando", False) for c in list(m.canais.values())):
            return "há live sendo gravada"
        return ""

    # ----- ciclo
    def verificar(self) -> dict:
        """Consulta o GitHub (sem instalar nada)."""
        with self._lock:
            self.estado["ultima_checagem"] = self.relogio()
            try:
                info = consultar(self.get)
            except (ErroAtualizacao, requests.RequestException, ValueError) as e:
                self._situacao("erro", f"não consegui consultar atualizações: {e}")
                return self.estado
            self.estado["build_disponivel"] = info["build"]
            if info["build"] <= self.build:
                self.info = None
                self._situacao("atualizado")
            else:
                self.info = info
                if self.estado["situacao"] not in ("baixado", "aguardando"):
                    self._situacao("disponivel", f"build {info['build']} disponível")
            return self.estado

    def passo(self, forcar=False) -> dict:
        """Uma rodada: confere, baixa e, se for a hora, instala. `forcar` = clique em "Atualizar agora"."""
        c = self.cfg()
        agora = self.relogio()
        horas = max(float(c.get("horas_entre_checagens", 6) or 6), 1)
        if forcar or agora - self.estado["ultima_checagem"] >= horas * 3600:
            self.verificar()
        if not self.info or (c["modo"] == "desligado" and not forcar):
            return self.estado
        if c["modo"] == "perguntar" and not forcar:
            return self.estado
        if not pode_aplicar():
            self._situacao("disponivel", f"build {self.info['build']} disponível (atualização automática só "
                                         "no programa instalado no Windows)")
            return self.estado
        tent = self._ler()
        if tent.get("build") == self.info["build"] and tent.get("tentativas", 0) >= MAX_TENTATIVAS:
            self._situacao("erro", f"a atualização para o build {self.info['build']} falhou {MAX_TENTATIVAS} vezes; "
                                   "baixe e instale à mão")
            return self.estado
        if not self.instalador:
            self._situacao("baixando", f"baixando o build {self.info['build']}")
            try:
                self.instalador = baixar(self.info, self.dir, self.get)
            except (ErroAtualizacao, requests.RequestException, OSError) as e:
                self._situacao("erro", f"download falhou: {e}")
                return self.estado
        if not self.pendente_desde:
            self.pendente_desde = agora
        espera_max = float(c.get("espera_maxima_horas", 24) or 24) * 3600
        motivo = self.motivo_ocupado(ignorar_gravacao=agora - self.pendente_desde >= espera_max)
        if forcar:
            motivo = self.motivo_ocupado(ignorar_gravacao=True)
        if motivo:
            self._situacao("aguardando", f"build {self.info['build']} baixado; instala quando terminar: {motivo}")
            return self.estado
        self.aplicar()
        return self.estado

    def aplicar(self):
        tent = self._ler()
        n = tent.get("tentativas", 0) + 1 if tent.get("build") == self.info["build"] else 1
        self._gravar({"build": self.info["build"], "tentativas": n, "quando": self.relogio()})
        registro = self.dir / "ultima_instalacao.txt"
        registro.unlink(missing_ok=True)
        sem_janela = 0x08000000 if sys.platform == "win32" else 0
        try:   # rede de segurança: se o instalador for bloqueado, o programa volta assim mesmo
            self.rodar(comando_rede_de_seguranca(Path(sys.executable)), capture_output=True, timeout=20,
                       creationflags=sem_janela)
        except Exception as e:  # noqa: BLE001
            log.warning("não consegui agendar a reabertura de segurança: %s", e)
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: o instalador segue vivo depois que o programa fecha
        flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
        self._situacao("instalando", f"instalando o build {self.info['build']}; o programa reabre sozinho")
        log.info("atualizando do build %s para o %s (tentativa %s)", self.build, self.info["build"], n)
        self.popen(comando_instalacao(self.instalador, registro), creationflags=flags, close_fds=True,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.encerrar:
            threading.Timer(1.0, self.encerrar).start()

    def _loop(self):
        self.parar.wait(60)                 # deixa o programa terminar de abrir
        while not self.parar.is_set():
            try:
                self.passo()
            except Exception as e:  # noqa: BLE001
                log.warning("atualizador: %s", e)
            self.parar.wait(120)

    def iniciar(self):
        threading.Thread(target=self._loop, name="atualizador", daemon=True).start()
