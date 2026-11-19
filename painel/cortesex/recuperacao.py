"""Modo de recuperação (AUD-M6): o banco não abre ou falhou na verificação de integridade.

O programa não cria um banco vazio no lugar e não apaga nada. Abre uma página simples que explica o
problema, lista os backups e, se você pedir, guarda o arquivo danificado com outro nome e copia o backup
escolhido no lugar. Depois é só abrir o CortesEX-PRESS de novo.
"""

import html
import json
import logging
import secrets
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cortesex import seguranca

log = logging.getLogger("cortesex")


def restaurar(arquivo: Path, backup: Path) -> Path:
    """Move o banco danificado (e -wal/-shm) para *.danificado-<data> e copia o backup no lugar."""
    marca = time.strftime("%Y%m%d-%H%M%S")
    guardado = arquivo.with_name(f"{arquivo.name}.danificado-{marca}")
    for sufixo in ("", "-wal", "-shm"):
        origem = Path(str(arquivo) + sufixo)
        if origem.exists():
            origem.replace(Path(str(guardado) + sufixo))
    tmp = arquivo.with_name(arquivo.name + ".restaurando")
    shutil.copy2(backup, tmp)
    tmp.replace(arquivo)
    return guardado


def pagina(erro, token) -> bytes:
    itens = "".join(
        f"<li><b>{html.escape(p.name)}</b> ({html.escape(p.parent.name)}) — "
        f"{time.strftime('%d/%m/%Y %H:%M', time.localtime(p.stat().st_mtime))} "
        f"<button onclick='restaurar({i})'>Restaurar este</button></li>" for i, p in enumerate(erro.backups))
    corpo = f"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8"><title>CortesEX-PRESS — recuperação</title>
<style>body{{font-family:system-ui,sans-serif;background:#0b0a14;color:#ecebf7;max-width:760px;margin:40px auto;padding:0 16px}}
button{{padding:6px 12px;border-radius:8px;border:0;background:#9146ff;color:#fff;cursor:pointer}}code{{background:#222;padding:2px 6px}}</style></head>
<body><h1>✂️ CortesEX-PRESS — modo de recuperação</h1>
<p>O banco de dados <code>{html.escape(str(erro.arquivo))}</code> não passou na verificação de integridade:</p>
<p><code>{html.escape(seguranca.redigir(erro.detalhe))}</code></p>
<p>Nada foi apagado nem sobrescrito. Seus vídeos, configurações e chaves continuam na pasta de dados.</p>
<h2>Backups disponíveis</h2>
{"<ul>" + itens + "</ul>" if itens else "<p>Nenhum backup encontrado. Feche o programa e procure ajuda antes de mexer no arquivo.</p>"}
<p>Restaurar guarda o arquivo danificado como <code>cortesex.db.danificado-…</code> (não apaga) e copia o backup no lugar.
O que aconteceu depois do backup (cortes e postagens recentes) não estará no banco restaurado; os arquivos de vídeo continuam na pasta.</p>
<p id="msg"></p>
<script>
async function restaurar(i){{
  if(!confirm("Restaurar este backup? O arquivo danificado será guardado com outro nome.")) return;
  const r = await fetch("/restaurar", {{method:"POST", headers:{{"Content-Type":"application/json","X-CortesEX":"{token}"}}, body: JSON.stringify({{indice:i}})}});
  const j = await r.json(); document.getElementById("msg").textContent = j.msg || j.erro;
}}
</script></body></html>"""
    return corpo.encode()


def servir(erro, porta) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(32)

    class H(BaseHTTPRequestHandler):
        timeout = 30

        def log_message(self, *a):
            pass

        def _resp(self, status, corpo: bytes, tipo):
            self.send_response(status)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            for k, v in seguranca.CABECALHOS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corpo)

        def _host_ok(self):
            hosts = self.headers.get_all("Host") or []
            return len(hosts) == 1 and seguranca.host_permitido(hosts[0], porta)

        def do_GET(self):
            if not self._host_ok():
                return self._resp(403, b"{}", "application/json")
            self._resp(200, pagina(erro, token), "text/html; charset=utf-8")

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 <= n <= 10_000 or not self._host_ok() or \
                    not secrets.compare_digest(self.headers.get("X-CortesEX") or "", token) or \
                    not seguranca.origem_permitida(self.headers.get("Origin"), porta) or self.path != "/restaurar":
                return self._resp(403, b'{"erro": "proibido"}', "application/json")
            try:
                i = int(json.loads(self.rfile.read(n) or b"{}").get("indice"))
                guardado = restaurar(erro.arquivo, erro.backups[i])
                msg = f"Backup restaurado. O arquivo danificado foi guardado como {guardado.name}. Feche e abra o CortesEX-PRESS de novo."
                log.warning("banco restaurado do backup %s; danificado guardado em %s", erro.backups[i].name, guardado.name)
                corpo = json.dumps({"msg": msg}, ensure_ascii=False).encode()
                return self._resp(200, corpo, "application/json")
            except Exception as e:  # noqa: BLE001
                return self._resp(400, json.dumps({"erro": seguranca.redigir(e)}, ensure_ascii=False).encode(),
                                  "application/json")

    srv = ThreadingHTTPServer(("127.0.0.1", porta), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
