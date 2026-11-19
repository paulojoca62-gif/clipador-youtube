"""Ao abrir uma versão nova: copia config, cofre e banco para backups/ antes de qualquer migração.

Guarda as 5 cópias mais recentes. Não baixa nem executa nada (atualizar é instalar o novo .exe por cima).
"""

import json
import shutil
import sqlite3
import time
from pathlib import Path

from cortesex import VERSAO

ARQUIVOS = ("config.json", "segredos.dat", "cortesex.db")


def _copiar_config_sem_chaves(origem: Path, destino: Path):
    """O backup não pode virar uma cópia em texto puro das chaves (elas ficam no cofre, também copiado)."""
    from cortesex.config import SEGREDOS
    try:
        dados = json.loads(origem.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        shutil.copy2(origem, destino)
        return
    for secao, chave in SEGREDOS:
        if isinstance(dados.get(secao), dict) and dados[secao].get(chave):
            dados[secao][chave] = ""
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def backup_se_mudou(pasta: Path, manter=5) -> Path | None:
    pasta = Path(pasta)
    marca = pasta / "versao.txt"
    anterior = marca.read_text(encoding="utf-8").strip() if marca.exists() else ""
    existentes = [pasta / a for a in ARQUIVOS if (pasta / a).exists()]
    destino = None
    if anterior != VERSAO and existentes:
        destino = pasta / "backups" / f"atualizacao-{anterior or '1.x'}-para-{VERSAO}-{time.strftime('%Y%m%d-%H%M%S')}"
        destino.mkdir(parents=True, exist_ok=True)
        for arq in existentes:
            if arq.suffix == ".db":
                # cópia consistente mesmo com o banco em WAL
                origem = sqlite3.connect(str(arq))
                alvo = sqlite3.connect(str(destino / arq.name))
                with alvo:
                    origem.backup(alvo)
                origem.close()
                alvo.close()
            elif arq.name == "config.json":
                _copiar_config_sem_chaves(arq, destino / arq.name)
            else:
                shutil.copy2(arq, destino / arq.name)
        antigos = sorted((p for p in (pasta / "backups").glob("atualizacao-*") if p.is_dir()),
                         key=lambda p: p.stat().st_mtime)
        for p in antigos[:-manter]:
            shutil.rmtree(p, ignore_errors=True)
    marca.write_text(VERSAO, encoding="utf-8")
    return destino
