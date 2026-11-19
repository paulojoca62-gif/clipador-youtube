"""Legendas: segmentação adaptativa, estilos (clássico, impacto, narrativo) e área segura 9:16.

As legendas são guardadas como segmentos editáveis:
    [{"ini": s, "fim": s, "texto": "...", "palavras": [[ini, fim, "p"], ...]}]
com tempos relativos ao início do corte. O editor do painel altera esse JSON e o vídeo é montado de novo
a partir da fonte, sem capturar, transcrever ou chamar a IA outra vez.
"""

import re

W, H = 1080, 1920
# Área segura: faixas que as redes cobrem com botões/legenda do post (aproximado, configurável no painel).
AREA_SEGURA = {"topo": 220, "base": 420, "direita": 150, "esquerda": 60}
ESTILOS = {
    "classico": {"max_caracteres": 22, "max_seg": 2.8, "linhas": 1, "caixa": False, "destaque": False},
    "impacto": {"max_caracteres": 16, "max_seg": 1.8, "linhas": 1, "caixa": False, "destaque": True},
    "narrativo": {"max_caracteres": 34, "max_seg": 4.5, "linhas": 2, "caixa": True, "destaque": False},
}
FIM_FRASE = re.compile(r"[.!?…]+[\"')\]]*$")
VIRGULA = re.compile(r"[,;:]$")


def segmentar(palavras, estilo="classico", max_caracteres=None, pausa=0.45):
    """Quebra por pontuação, pausa, tamanho (legibilidade) e duração máxima do bloco."""
    e = ESTILOS.get(estilo, ESTILOS["classico"])
    limite = int(max_caracteres or e["max_caracteres"]) * e["linhas"]
    segs, atual = [], []

    def fechar():
        if atual:
            segs.append({"ini": round(atual[0][0], 2), "fim": round(atual[-1][1], 2),
                         "texto": " ".join(p[2] for p in atual).strip(),
                         "palavras": [[round(p[0], 2), round(p[1], 2), p[2]] for p in atual]})
            atual.clear()

    for i, w in enumerate(palavras):
        texto_novo = " ".join([p[2] for p in atual] + [w[2]])
        if atual and (len(texto_novo) > limite or w[1] - atual[0][0] > e["max_seg"]):
            fechar()
        atual.append(w)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        if prox is None or FIM_FRASE.search(w[2]) or prox[0] - w[1] >= pausa:
            fechar()
        elif VIRGULA.search(w[2]) and len(" ".join(p[2] for p in atual)) >= limite * 0.5:
            fechar()
    fechar()
    # cada bloco fica na tela até o próximo começar (sem piscar), no máximo +0.6 s depois da fala
    for a, b in zip(segs, segs[1:]):
        a["fim"] = round(min(b["ini"], a["fim"] + 0.6), 2)
    if segs:
        segs[-1]["fim"] = round(segs[-1]["fim"] + 0.4, 2)
    return segs


def recortar(palavras_fonte, ini, fim):
    """Palavras da fonte que caem no corte, com tempos relativos ao início do corte."""
    return [(round(a - ini, 3), round(b - ini, 3), t) for a, b, t in palavras_fonte if a >= ini - 0.05 and b <= fim + 0.2]


def _cor_ass(hexa: str, alfa: int = 0) -> str:
    h = (hexa or "#FFFFFF").lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        h = "FFFFFF"
    return f"&H{alfa:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ts(t):
    t = max(t, 0)
    h, r = divmod(t, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _limpa(t):
    return t.replace("{", "(").replace("}", ")").replace("\\", "/").replace("\n", " ")


def posicao(cfg):
    """Alinhamento ASS e margem vertical respeitando a área segura."""
    pos = cfg.get("posicao", "baixo")
    if pos == "alto":
        return 8, max(int(cfg.get("margem_superior", AREA_SEGURA["topo"] + 60)), AREA_SEGURA["topo"])
    if pos == "meio":
        return 5, 0
    return 2, max(int(cfg.get("margem_inferior", AREA_SEGURA["base"])), AREA_SEGURA["base"])


def gerar_ass(segmentos, cfg: dict) -> str:
    estilo = cfg.get("estilo", "classico")
    e = ESTILOS.get(estilo, ESTILOS["classico"])
    tamanho = int(cfg.get("tamanho") or 74)
    if estilo == "narrativo":
        tamanho = int(tamanho * 0.78)
    alin, mv = posicao(cfg)
    ml = max(int(cfg.get("margem_lateral", 90)), AREA_SEGURA["esquerda"])
    mr = max(int(cfg.get("margem_lateral", 90)), AREA_SEGURA["direita"])
    cor, destaque = _cor_ass(cfg.get("cor", "#FFFFFF")), _cor_ass(cfg.get("cor_destaque", "#FFD23F"))
    contorno = int(cfg.get("contorno", 6))
    fonte = re.sub(r"[,\n\r]", " ", cfg.get("fonte") or "DejaVu Sans")[:60]
    borda, fundo = (3, "&H96000000") if e["caixa"] else (1, "&H64000000")
    cab = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
           "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
           "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
           "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           f"Style: Corte,{fonte},{tamanho},{cor},{destaque},&H00000000,{fundo},-1,0,0,0,100,100,0,0,{borda},"
           f"{contorno if borda == 1 else 12},2,{alin},{ml},{mr},{mv},1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    anim = bool(cfg.get("animacao", True))
    maiusculas = estilo != "narrativo"
    for s in segmentos:
        texto = _limpa(s["texto"].upper() if maiusculas else s["texto"])
        if not texto.strip():
            continue
        if e["destaque"] and s.get("palavras"):
            # uma linha por palavra falada: a palavra da vez muda de cor (e cresce de leve)
            ws = s["palavras"]
            for i, (a, b, _t) in enumerate(ws):
                ini = a if i else s["ini"]
                fim = ws[i + 1][0] if i + 1 < len(ws) else s["fim"]
                partes = []
                for j, (_a, _b, t) in enumerate(ws):
                    t = _limpa(t.upper())
                    if j == i:
                        efeito = r"\fscx108\fscy108" if anim else ""
                        partes.append(f"{{\\c{destaque}{efeito}}}{t}{{\\c{cor}\\fscx100\\fscy100}}")
                    else:
                        partes.append(t)
                ev.append(f"Dialogue: 0,{_ts(ini)},{_ts(max(fim, ini + 0.05))},Corte,,0,0,0,,{' '.join(partes)}")
        else:
            efeito = r"{\fad(80,60)}" if anim and estilo != "narrativo" else (r"{\fad(150,150)}" if anim else "")
            ev.append(f"Dialogue: 0,{_ts(s['ini'])},{_ts(s['fim'])},Corte,,0,0,0,,{efeito}{texto}")
    return cab + "\n".join(ev) + "\n"


def validar_segmentos(segs, duracao) -> list:
    """Limpa o que vem do editor: tempos dentro do corte, em ordem, texto curto."""
    out = []
    for s in segs or []:
        try:
            ini = max(0.0, min(float(s.get("ini", 0)), duracao))
            fim = max(ini + 0.1, min(float(s.get("fim", ini + 1)), duracao))
        except (TypeError, ValueError):
            continue
        texto = re.sub(r"\s+", " ", str(s.get("texto") or "")).strip()[:200]
        palavras = []
        for p in s.get("palavras") or []:
            try:
                palavras.append([float(p[0]), float(p[1]), str(p[2])[:40]])
            except (TypeError, ValueError, IndexError):
                pass
        # se o texto foi editado, as palavras com tempo não valem mais: distribui por igual
        if palavras and " ".join(p[2] for p in palavras).strip() != texto:
            ts = texto.split()
            passo = (fim - ini) / max(len(ts), 1)
            palavras = [[round(ini + i * passo, 2), round(ini + (i + 1) * passo, 2), t] for i, t in enumerate(ts)]
        out.append({"ini": round(ini, 2), "fim": round(fim, 2), "texto": texto, "palavras": palavras})
    out.sort(key=lambda s: s["ini"])
    return out
