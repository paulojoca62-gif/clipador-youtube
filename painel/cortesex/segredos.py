"""Cofre local das chaves, separado do config.json.

- Windows: cifrado com a DPAPI do usuário do Windows (CryptProtectData). Só esse usuário, neste
  computador, consegue abrir. Copiar o arquivo para outra máquina não revela as chaves.
- Outros sistemas: JSON com permissão 600 (só o dono lê). NÃO é cifrado; o painel avisa isso.

Formato do arquivo `segredos.dat`: cabeçalho de 6 bytes ("CXS1D:" = DPAPI, "CXS1P:" = texto) + dados.
"""

import base64
import json
import os
import sys
import threading
from pathlib import Path

ENTROPIA = b"CortesEX-PRESS/segredos/v1"


def _dpapi(dados: bytes, cifrar: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b: bytes):
        buf = ctypes.create_string_buffer(b, len(b))
        return DATA_BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    entrada, _b1 = blob(dados)
    entropia, _b2 = blob(ENTROPIA)
    saida = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PBLOB = ctypes.POINTER(DATA_BLOB)
    funcao = crypt32.CryptProtectData if cifrar else crypt32.CryptUnprotectData
    # assinaturas explícitas (64 bits): Protect(pIn, szDesc, pEntropy, pvReserved, pPrompt, flags, pOut)
    funcao.argtypes = [PBLOB, ctypes.c_void_p, PBLOB, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, PBLOB]
    funcao.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = funcao(ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(saida))
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI recusou a operação (arquivo de outro usuário/computador?)")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(saida.pbData, ctypes.c_void_p))


def protegido() -> bool:
    return sys.platform == "win32"


class Cofre:
    def __init__(self, arquivo: Path):
        self.arquivo = arquivo
        self._lock = threading.RLock()
        self._dados: dict = {}
        self.erro = ""
        if arquivo.exists():
            try:
                self._dados = self._ler()
            except Exception as e:  # noqa: BLE001
                # Não apaga nem sobrescreve um cofre que não abriu: guarda de lado e avisa.
                self.erro = f"Não consegui abrir o cofre de chaves ({type(e).__name__}). Conecte as contas de novo."
                try:
                    arquivo.replace(arquivo.with_suffix(".ilegivel"))
                except OSError:
                    pass

    def _ler(self) -> dict:
        bruto = self.arquivo.read_bytes()
        tipo, corpo = bruto[:6], bruto[6:]
        if tipo == b"CXS1D:":
            corpo = _dpapi(base64.b64decode(corpo), cifrar=False)
        elif tipo != b"CXS1P:":
            raise ValueError("formato desconhecido")
        return json.loads(corpo.decode("utf-8"))

    def _gravar(self) -> None:
        corpo = json.dumps(self._dados, ensure_ascii=False).encode("utf-8")
        if protegido():
            conteudo = b"CXS1D:" + base64.b64encode(_dpapi(corpo, cifrar=True))
        else:
            conteudo = b"CXS1P:" + corpo
        tmp = self.arquivo.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(conteudo)
        os.replace(tmp, self.arquivo)
        if not protegido():
            os.chmod(self.arquivo, 0o600)

    def get(self, secao: str, chave: str) -> str:
        with self._lock:
            return str(self._dados.get(f"{secao}.{chave}", ""))

    def definir(self, valores: dict) -> None:
        """valores: {"secao.chave": "valor"}; valor vazio apaga a chave."""
        with self._lock:
            for k, v in valores.items():
                if v:
                    self._dados[k] = str(v)
                else:
                    self._dados.pop(k, None)
            self._gravar()
            # confere que o que foi gravado abre igual antes de alguém apagar a cópia antiga
            if self._ler() != self._dados:
                raise OSError("o cofre gravado não confere")

    def todos(self) -> dict:
        with self._lock:
            return dict(self._dados)
