"""Abre o CortesEX-PRESS: liga o motor e o painel e abre o navegador.

Se o programa já estiver aberto (a porta do painel está ocupada), só abre o
navegador na janela que já existe.
"""

import logging
import os
import signal
import socket
import sys
import time
import webbrowser
from logging.handlers import RotatingFileHandler

from cortesex import NOME, PORTA
from cortesex.config import pasta_dados


def ja_aberto(porta: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", porta)) == 0


def configurar_log():
    raiz = logging.getLogger("cortesex")
    raiz.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    arq = RotatingFileHandler(pasta_dados() / "cortesex.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    arq.setFormatter(fmt)
    raiz.addHandler(arq)
    if sys.stderr and not getattr(sys, "frozen", False):
        tela = logging.StreamHandler()
        tela.setFormatter(fmt)
        raiz.addHandler(tela)


def main(abrir_navegador: bool = True):
    url = f"http://127.0.0.1:{PORTA}/"
    if ja_aberto(PORTA):
        webbrowser.open(url)
        return
    configurar_log()
    from cortesex.motor import Motor
    from cortesex.servidor import Painel
    motor = Motor()
    painel = Painel(motor)
    painel.iniciar()
    motor.iniciar()

    def sair(*_):
        motor.encerrar()   # fecha streamlink e ffmpeg junto; nada fica gravando sozinho
        os._exit(0)
    painel.ao_encerrar = sair
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, sair)
    if abrir_navegador and "--sem-navegador" not in sys.argv:
        webbrowser.open(url)
    logging.getLogger("cortesex").info("%s aberto em %s", NOME, url)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
