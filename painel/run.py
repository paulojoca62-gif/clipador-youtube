"""Ponto de entrada do executável (PyInstaller).

O mesmo .exe também faz o papel do streamlink: o gravador chama
"CortesEX-PRESS.exe --streamlink ..." num processo separado, com a saída
ligada ao ffmpeg.
"""
import io
import os
import sys


def _religar_saidas():
    # Sem console (--noconsole), o PyInstaller deixa sys.stdout/stderr = None,
    # mas o processo ainda recebe o pipe do pai nos descritores 1 e 2.
    for fd, nome in ((1, "stdout"), (2, "stderr")):
        if getattr(sys, nome) is None:
            try:
                bruto = os.fdopen(fd, "wb", buffering=0)
                setattr(sys, nome, io.TextIOWrapper(io.BufferedWriter(bruto), encoding="utf-8",
                                                    line_buffering=True))
            except OSError:
                setattr(sys, nome, open(os.devnull, "w", encoding="utf-8"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--streamlink":
        _religar_saidas()
        from streamlink_cli.main import main as streamlink_main
        sys.argv = ["streamlink"] + sys.argv[2:]
        sys.exit(streamlink_main())
    from cortesex.app import main
    main()
