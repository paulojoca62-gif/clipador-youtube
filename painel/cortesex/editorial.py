"""Inteligência editorial local: onde começar e terminar o corte, sinais de qualidade e score 0–100.

Princípios:
- Só usa o que foi observado (chat, transcrição com tempo de palavra, volume do áudio, análise do Gemini
  quando existir). Critério sem dado = "desconhecido": não entra na conta e baixa a confiança.
- Nada aqui descarta um corte sozinho: os sinais viram avisos e nota; quem decide é o usuário/agendador.
"""

import re

PESOS_PADRAO = {"chat": 20, "reacao": 20, "contexto": 20, "entretenimento": 15,
                "audio": 10, "inicio_fim": 10, "retencao": 5}
CATEGORIAS = ["engracado", "reacao", "gameplay", "surpresa", "polemica", "informativo", "historia",
              "frustracao", "conquista", "interacao_chat", "outros"]
NOMES_CATEGORIAS = {"engracado": "Engraçado", "reacao": "Reação", "gameplay": "Gameplay", "surpresa": "Surpresa",
                    "polemica": "Polêmica", "informativo": "Informativo", "historia": "História",
                    "frustracao": "Frustração", "conquista": "Conquista", "interacao_chat": "Interação com chat",
                    "outros": "Outros"}
FIM_FRASE = re.compile(r"[.!?…]+[\"')\]]*$")


def _limitar(x, a=0.0, b=1.0):
    return max(a, min(b, x))


# ----------------------------------------------------------------- frases e pausas
def frases(palavras, pausa=0.6):
    """Agrupa palavras [(ini, fim, texto)] em frases: quebra em pontuação final ou pausa >= `pausa` s."""
    grupos, atual = [], []
    for i, w in enumerate(palavras):
        atual.append(w)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        if prox is None or FIM_FRASE.search(w[2]) or prox[0] - w[1] >= pausa:
            grupos.append({"ini": atual[0][0], "fim": atual[-1][1], "texto": " ".join(x[2] for x in atual).strip(),
                           "completa": bool(FIM_FRASE.search(atual[-1][2])) or prox is None or prox[0] - w[1] >= 1.0})
            atual = []
    return grupos


def limites_adaptativos(palavras, t_pico, dur_fonte, cfg):
    """Escolhe início e fim do corte (em segundos da fonte) alinhados com frases e pausas.

    cfg: duracao_min, duracao_max, antes_padrao, depois_padrao (os valores fixos da 1.x).
    Devolve {"ini", "fim", "inicio_alinhado", "fim_alinhado", "metodo"}.
    """
    dmin, dmax = float(cfg.get("duracao_min", 20)), float(cfg.get("duracao_max", 90))
    antes, depois = float(cfg.get("antes_padrao", 40)), float(cfg.get("depois_padrao", 15))
    ini_fixo, fim_fixo = max(0.0, t_pico - antes), min(dur_fonte, t_pico + depois)
    fs = frases(palavras)
    if not fs:
        return {"ini": ini_fixo, "fim": fim_fixo, "inicio_alinhado": False, "fim_alinhado": False,
                "metodo": "fixo (sem fala detectada)"}
    # início: a última frase que começa pelo menos 6 s antes do pico (contexto), sem passar do máximo
    inicios = [f["ini"] for f in fs if f["ini"] <= t_pico - 6 and t_pico - f["ini"] <= dmax - 6]
    ini = inicios[-1] if inicios else ini_fixo
    # junta contexto: se a frase escolhida começa muito perto do pico, volta mais uma frase
    while t_pico - ini < min(antes * 0.5, dmin * 0.6):
        anteriores = [f["ini"] for f in fs if f["ini"] < ini - 0.3]
        if not anteriores or t_pico - anteriores[-1] > dmax - 6:
            break
        ini = anteriores[-1]
    # fim: o fim da primeira frase completa depois de pico + 4 s
    fins = [f["fim"] for f in fs if f["fim"] >= t_pico + 4 and f["completa"]]
    fim = fins[0] if fins else fim_fixo
    # duração mínima: estende o fim por frases; depois o início
    while fim - ini < dmin:
        mais = [f["fim"] for f in fs if f["fim"] > fim + 0.2]
        if mais and mais[0] - ini <= dmax:
            fim = mais[0]
            continue
        menos = [f["ini"] for f in fs if f["ini"] < ini - 0.2]
        if menos and fim - menos[-1] <= dmax:
            ini = menos[-1]
            continue
        falta = dmin - (fim - ini)
        ini, fim = max(0.0, ini - falta / 2), min(dur_fonte, fim + falta / 2)
        break
    # duração máxima: corta o início para frente, frase a frase, mantendo 6 s antes do pico
    while fim - ini > dmax:
        depois_ini = [f["ini"] for f in fs if ini + 0.2 < f["ini"] <= t_pico - 6]
        if depois_ini:
            ini = depois_ini[0]
        else:
            ini = fim - dmax
            break
    ini = max(0.0, ini - 0.25)
    fim = min(dur_fonte, fim + 0.35)
    inicio_alinhado = any(abs(f["ini"] - (ini + 0.25)) < 0.05 for f in fs)
    fim_alinhado = any(abs(f["fim"] - (fim - 0.35)) < 0.05 and f["completa"] for f in fs)
    return {"ini": round(ini, 2), "fim": round(fim, 2), "inicio_alinhado": inicio_alinhado,
            "fim_alinhado": fim_alinhado, "metodo": "adaptativo"}


# ----------------------------------------------------------------- sinais técnicos
def sinais(palavras, perfil, ini, fim, t_pico):
    """Sinais observados no trecho final [ini, fim] (segundos da fonte)."""
    from cortesex.midia import janela_db
    dur = max(fim - ini, 0.1)
    dentro = [w for w in palavras if w[0] >= ini - 0.05 and w[1] <= fim + 0.05]
    fala_s = sum(w[1] - w[0] for w in dentro)
    db = janela_db(perfil, ini, fim) if perfil else []
    s = {"duracao": round(dur, 1), "palavras": len(dentro),
         "fala_proporcao": round(fala_s / dur, 2) if palavras is not None else None}
    if db:
        import statistics
        mediana_fonte = perfil.get("mediana_db")
        perto_pico = janela_db(perfil, max(t_pico - 3, ini), min(t_pico + 4, fim))
        s["volume_mediano_db"] = round(statistics.median(db), 1)
        s["reacao_db"] = round(max(perto_pico or db) - (mediana_fonte if mediana_fonte is not None else statistics.median(db)), 1)
        s["silencio"] = round(sum(1 for x in db if x < -45) / len(db), 2)
        s["saturacao"] = perfil.get("saturacao")
        prim = janela_db(perfil, ini, ini + 3)
        s["inicio_ativo"] = bool(prim) and max(prim) > (mediana_fonte or -60) - 3
    return s


def avisos(sinal: dict, limites: dict, palavras_no_corte: int) -> list:
    out = []
    if limites and not limites.get("inicio_alinhado") and palavras_no_corte:
        out.append("inicio_no_meio_da_fala")
    if limites and not limites.get("fim_alinhado") and palavras_no_corte:
        out.append("fim_abrupto")
    if sinal.get("silencio") is not None and sinal["silencio"] > 0.4:
        out.append("silencio_excessivo")
    if sinal.get("saturacao") and sinal["saturacao"] > 0.01:
        out.append("audio_estourado")
    if sinal.get("volume_mediano_db") is not None and sinal["volume_mediano_db"] < -40:
        out.append("audio_baixo")
    if sinal.get("fala_proporcao") is not None and sinal["fala_proporcao"] < 0.15 and palavras_no_corte < 8:
        out.append("pouca_fala")
    return out


# ----------------------------------------------------------------- score
def criterios(score_pico, sinal, limites, analise_ia, manual=False):
    """Cada critério vira um número 0–1, ou None quando não há dado para avaliar."""
    ia = analise_ia or {}
    notas = ia.get("notas") or {}

    def ia_nota(k):
        v = notas.get(k)
        return _limitar(float(v) / 10) if isinstance(v, (int, float)) else None

    c = {}
    c["chat"] = None if manual or not score_pico else _limitar((float(score_pico) - 1.0) / 4.0)
    reacao_audio = None
    if sinal.get("reacao_db") is not None:
        reacao_audio = _limitar((sinal["reacao_db"] - 3) / 12)
    ia_reacao = ia_nota("reacao")
    vals = [v for v in (reacao_audio, ia_reacao) if v is not None]
    c["reacao"] = sum(vals) / len(vals) if vals else None
    c["contexto"] = ia_nota("contexto")
    c["entretenimento"] = ia_nota("entretenimento")
    audio = None
    if sinal.get("silencio") is not None:
        audio = 1.0 - _limitar(sinal["silencio"] * 1.5)
        if sinal.get("saturacao"):
            audio -= _limitar(sinal["saturacao"] * 20, 0, 0.5)
        if sinal.get("volume_mediano_db") is not None and sinal["volume_mediano_db"] < -40:
            audio -= 0.3
        if ia_nota("clareza") is not None:
            audio = (audio + ia_nota("clareza")) / 2
        audio = _limitar(audio)
    c["audio"] = audio
    if limites and limites.get("metodo") == "adaptativo":
        c["inicio_fim"] = (0.5 if limites.get("inicio_alinhado") else 0.15) + (0.5 if limites.get("fim_alinhado") else 0.15)
    else:
        c["inicio_fim"] = None
    c["retencao"] = ia_nota("potencial")   # estimativa da IA; não é retenção medida
    return c


def pontuar(crit: dict, pesos: dict | None = None) -> dict:
    """Score 0–100 sobre os critérios conhecidos + confiança (quanto do peso total foi avaliado)."""
    pesos = pesos or PESOS_PADRAO
    total = sum(pesos.values()) or 1
    conhecidos = {k: v for k, v in crit.items() if v is not None and pesos.get(k)}
    peso_conhecido = sum(pesos[k] for k in conhecidos)
    if not peso_conhecido:
        return {"score": None, "confianca": 0.0}
    score = sum(pesos[k] * v for k, v in conhecidos.items()) / peso_conhecido * 100
    return {"score": round(score, 1), "confianca": round(peso_conhecido / total, 2)}


def score_tecnico(validacao: dict, sinal: dict) -> float | None:
    if not validacao or not validacao.get("ok"):
        return 0.0 if validacao else None
    nota = 100.0
    if not validacao.get("audio"):
        nota -= 40
    if sinal.get("silencio") is not None:
        nota -= min(sinal["silencio"], 1) * 40
    if sinal.get("saturacao"):
        nota -= min(sinal["saturacao"] * 1000, 30)
    return round(max(nota, 0), 1)
