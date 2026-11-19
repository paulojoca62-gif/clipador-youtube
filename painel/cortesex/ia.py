"""Gemini escreve o título, a legenda e as hashtags de cada corte."""

import json
import logging
import time

import requests

log = logging.getLogger("cortesex")

URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
RESERVA = ["gemini-flash-lite-latest", "gemini-2.5-flash"]

PROMPT = """Você escreve posts de YouTube Shorts, TikTok e Reels para cortes de lives brasileiras da Twitch.
Canal: {canal}
O que o streamer falou no corte: {fala}
Mensagens do chat no pico: {chat}

Responda só com JSON: {{"titulo": "...", "legenda": "...", "hashtags": ["#...", ...]}}
- titulo: até 70 caracteres, chamativo, em maiúsculas, sobre o que aconteceu, 1 emoji no fim.
- legenda: 1 ou 2 frases curtas em português contando o ocorrido; pode citar uma fala marcante entre aspas.
- hashtags: 5 a 7, incluindo #{canal} e #shorts, mais o jogo ou o assunto se ficar claro.
- Não invente fatos que não estejam na fala ou no chat. Nada ofensivo."""


class SemChave(RuntimeError):
    pass


def gerar_textos(canal: str, fala: str, chat: list, chave: str, modelo: str = "gemini-flash-latest",
                 post=requests.post, dormir=time.sleep) -> dict:
    if not chave:
        raise SemChave("Coloque a chave do Gemini em Contas → Gemini.")
    corpo = {"response_format": {"type": "json_object"},
             "messages": [{"role": "user", "content": PROMPT.format(
                 canal=canal, fala=fala or "(sem fala)", chat=" | ".join(chat or []))}]}
    modelos = [modelo] + [m for m in RESERVA if m != modelo]
    resp = None
    for tentativa in range(6):   # Gemini ocupado ou lento: espera e tenta outro modelo
        corpo["model"] = modelos[tentativa % len(modelos)]
        try:
            resp = post(URL, headers={"Authorization": f"Bearer {chave}"}, json=corpo, timeout=45)
        except requests.RequestException:
            resp = None
        if resp is not None and resp.status_code not in (429, 500, 502, 503, 504):
            break
        dormir(3 * (tentativa + 1))
    if resp is None:
        raise RuntimeError("O Gemini não respondeu.")
    if resp.status_code in (400, 401, 403):
        raise RuntimeError(f"O Gemini recusou a chave ou o pedido ({resp.status_code}).")
    resp.raise_for_status()
    texto = resp.json()["choices"][0]["message"]["content"].strip()
    texto = texto.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    j = json.loads(texto)
    tags = []
    for t in j.get("hashtags") or []:
        t = "#" + str(t).strip().lstrip("#").replace(" ", "")
        if len(t) > 1 and t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    if "#shorts" not in [t.lower() for t in tags]:
        tags.append("#shorts")
    return {"titulo": str(j.get("titulo") or "").strip()[:100],
            "legenda": str(j.get("legenda") or "").strip()[:1500],
            "hashtags": tags[:8]}
