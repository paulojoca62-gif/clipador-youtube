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
from cortesex.atualizacao import backup_se_mudou
from cortesex.config import pasta_dados
from cortesex.seguranca import RedigirLog


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
    arq.addFilter(RedigirLog())          # nenhuma chave/token vai para o arquivo de log
    raiz.addHandler(arq)
    if sys.stderr and not getattr(sys, "frozen", False):
        tela = logging.StreamHandler()
        tela.setFormatter(fmt)
        tela.addFilter(RedigirLog())
        raiz.addHandler(tela)


def usar_certificados_do_sistema():
    """HTTPS verificado pela loja de certificados do Windows (não só pela lista do certifi).

    Antivírus com inspeção de HTTPS e proxies corporativos trocam o certificado dos sites pelo deles;
    o Windows confia nessa autoridade, o certifi não ("self-signed certificate in certificate chain").
    Continua verificando tudo: só passa a aceitar o que o próprio Windows aceita.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception as e:  # noqa: BLE001
        logging.getLogger("cortesex").warning("certificados do sistema indisponíveis (%s); usando o certifi", e)


def main(abrir_navegador: bool = True):
    url = f"http://127.0.0.1:{PORTA}/"
    if ja_aberto(PORTA):
        if abrir_navegador and "--sem-navegador" not in sys.argv:
            webbrowser.open(url)
        return
    configurar_log()
    usar_certificados_do_sistema()
    copia = backup_se_mudou(pasta_dados())
    if copia:
        logging.getLogger("cortesex").info("versão nova: backup da configuração e do banco em %s", copia.name)
    from cortesex.db import BancoCorrompido
    from cortesex.motor import Motor
    from cortesex.servidor import Painel
    try:
        motor = Motor()
    except BancoCorrompido as e:
        logging.getLogger("cortesex").error("%s Abrindo o modo de recuperação.", e)
        from cortesex import recuperacao
        recuperacao.servir(e, PORTA)
        if abrir_navegador and "--sem-navegador" not in sys.argv:
            webbrowser.open(url)
        while True:
            time.sleep(3600)
    painel = Painel(motor)
    painel.iniciar()
    motor.iniciar()

    def sair(*_):
        motor.encerrar()   # fecha streamlink e ffmpeg junto; nada fica gravando sozinho
        os._exit(0)
    painel.ao_encerrar = sair
    from cortesex.atualizador import Atualizador
    painel.atualizador = Atualizador(motor, pasta_dados(), encerrar=sair)
    painel.atualizador.iniciar()
    from cortesex.telegram_revisao import RevisaoTelegram
    motor.telegram_rev = RevisaoTelegram(motor)
    motor.telegram_rev.iniciar()
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
