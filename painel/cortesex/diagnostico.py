"""Aba Diagnóstico: recursos da máquina, dependências, processos, fila, contas e erros recentes.

Só leitura. Não executa nada pedido pelo navegador e nunca mostra chaves.
Saúde: operacional | atencao | falha | nao_configurado | nao_validado | pausado.
"""

import os
import shutil
import sys
import threading
import time

from cortesex import VERSAO, util
from cortesex.segredos import protegido

_cache = {}


def _versao(cmd, chave):
    if chave in _cache:
        return _cache[chave]
    try:
        r = util.rodar(cmd, timeout=15)
        linha = (r.stdout or r.stderr).decode(errors="ignore").strip().splitlines()[0][:120] if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        linha = ""
    _cache[chave] = linha
    return linha


def memoria() -> dict:
    try:
        if sys.platform == "win32":
            import ctypes

            class M(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong), ("c", ctypes.c_ulonglong),
                            ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
            m = M()
            m.dwLength = ctypes.sizeof(M)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return {"total_gb": round(m.ullTotalPhys / 1e9, 1), "livre_gb": round(m.ullAvailPhys / 1e9, 1)}
        info = {}
        with open("/proc/meminfo") as f:
            for linha in f:
                k, v = linha.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        return {"total_gb": round(info["MemTotal"] / 1e9, 1), "livre_gb": round(info["MemAvailable"] / 1e9, 1)}
    except Exception:  # noqa: BLE001
        return {"total_gb": None, "livre_gb": None}


def _saude_plataforma(motor, p) -> dict:
    from cortesex.motor import conectado
    ag = motor.config.get("agendador")
    pausa = motor.banco.pausa(p)
    agora = time.time()
    ultimos = [x for x in motor.banco.log(300) if x["plataforma"] == p]
    ok = next((x for x in ultimos if x["status"] == "postado"), None)
    erro = next((x for x in ultimos if x["status"] == "erro"), None)
    if not conectado(motor.config, p):
        estado, texto = "nao_configurado", "conta não conectada"
    elif pausa and pausa["ate"] > agora:
        estado, texto = "pausado", pausa["motivo"]
    elif ok and (not erro or ok["quando"] >= erro["quando"]):
        estado, texto = "operacional", "último envio confirmado"
    elif erro:
        estado, texto = "falha", erro["erro"]
    else:
        estado, texto = "nao_validado", "conectado, mas nenhum envio real confirmado ainda"
    return {"estado": estado, "texto": texto, "no_agendador": bool(ag["plataformas"].get(p)),
            "ultimo_ok": ok["quando"] if ok else 0, "ultimo_erro": erro["quando"] if erro else 0}


def coletar(motor) -> dict:
    pasta = motor.pasta
    disco = shutil.disk_usage(pasta)
    minimo = float(motor.config.get("armazenamento", "minimo_livre_gb") or 5)
    try:
        carga = os.getloadavg()[0]
    except (AttributeError, OSError):
        carga = None
    try:
        import importlib.util
        whisper = "instalado" if importlib.util.find_spec("faster_whisper") else "não encontrado"
    except Exception:  # noqa: BLE001
        whisper = "não encontrado"
    try:
        import streamlink
        streamlink_v = getattr(streamlink, "__version__", "instalado")
    except Exception:  # noqa: BLE001
        streamlink_v = ""
    tam = {}
    for nome in ("buffer", "cortes", "modelos", "backups"):
        p = pasta / nome
        tam[nome] = round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9, 2) if p.exists() else 0.0
    agora = time.time()
    workers = {n: {"ultimo_batimento": t, "ok": agora - t < 180} for n, t in motor.batimentos.items()}
    canais = []
    for c in motor.canais.values():
        e = c.estado()
        e["bloco_ha_s"] = round(agora - e["ultimo_bloco"]) if e.get("ultimo_bloco") else None
        canais.append(e)
    try:
        banco_ok = motor.banco.verificar()
    except Exception as e:  # noqa: BLE001
        banco_ok = f"erro: {type(e).__name__}"
    ia = motor.ia_status
    gem_estado = "nao_configurado" if not motor.config.get("gemini", "api_key") else (
        "nao_validado" if ia.get("ok") is None else ("operacional" if ia["ok"] else "falha"))
    tg = motor.config.get("telegram")
    deps = {
        "ffmpeg": _versao([util.ffmpeg(), "-version"], "ffmpeg") or "não encontrado",
        "ffprobe": (_versao([util.ffprobe(), "-version"], "ffprobe") if util.ffprobe() else "") or
                   "não encontrado (validação usa o ffmpeg)",
        "streamlink": streamlink_v or "não encontrado",
        "whisper": whisper,
        "modelo_carregado": motor.render.modelo_carregado or "nenhum (carrega no primeiro corte)",
        "python": sys.version.split()[0], "versao_app": VERSAO,
    }
    saude = {p: _saude_plataforma(motor, p) for p in ("youtube", "tiktok", "facebook")}
    saude["gemini"] = {"estado": gem_estado, "texto": ia.get("erro") or ("último uso ok" if ia.get("ok") else "")}
    saude["telegram"] = {"estado": "operacional" if tg.get("bot_token") and tg.get("chat_id") else "nao_configurado",
                         "texto": ""}
    alertas = []
    if disco.free / 1e9 < minimo:
        alertas.append(f"Pouco espaço em disco: {disco.free / 1e9:.1f} GB livres (mínimo {minimo:.0f} GB).")
    if "não encontrado" in deps["ffmpeg"]:
        alertas.append("ffmpeg não encontrado: nada é gravado nem montado.")
    if banco_ok != "ok":
        alertas.append(f"Banco de dados: {banco_ok}")
    if motor.config.cofre.erro:
        alertas.append(motor.config.cofre.erro)
    for n, w in workers.items():
        if not w["ok"]:
            alertas.append(f"O processo '{n}' não responde há {agora - w['ultimo_batimento']:.0f} s.")
    incertos = len(motor.banco.postagens_status("incerto"))
    if incertos:
        alertas.append(f"{incertos} envio(s) com resultado incerto: confira na aba Fila.")
    geral = "falha" if any("ffmpeg" in a or "Banco" in a for a in alertas) else ("atencao" if alertas else "operacional")
    return {
        "geral": geral, "alertas": alertas,
        "sistema": {"cpus": os.cpu_count(), "carga_1min": carga, "memoria": memoria(),
                    "disco": {"livre_gb": round(disco.free / 1e9, 1), "total_gb": round(disco.total / 1e9, 1),
                              "minimo_gb": minimo, "baixo": motor.disco_baixo},
                    "uso_pastas_gb": tam, "pasta_dados": str(pasta), "threads": threading.active_count(),
                    "cofre_cifrado": protegido()},
        "dependencias": deps, "banco": {"integridade": banco_ok, "estados": motor.banco.contagem_estados()},
        "workers": workers, "canais": canais, "saude": saude,
        "erros_recentes": motor.banco.eventos(limite=30, nivel="erros"),
    }
