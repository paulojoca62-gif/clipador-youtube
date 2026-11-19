"""Avisos no Telegram por um bot seu (crie no @BotFather)."""

import requests


def enviar(cfg: dict, texto: str) -> None:
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        return
    r = requests.post(f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage", timeout=20,
                      json={"chat_id": cfg["chat_id"], "text": texto[:4000], "disable_web_page_preview": False})
    if not r.ok:
        raise RuntimeError(f"Telegram recusou ({r.status_code}): {r.text[:150]}")


def descobrir_chat(cfg: dict) -> str:
    """O chat_id da última conversa com o bot (mande um "oi" para ele antes)."""
    r = requests.get(f"https://api.telegram.org/bot{cfg['bot_token']}/getUpdates", timeout=20,
                     params={"allowed_updates": '["message","channel_post","callback_query"]'})
    r.raise_for_status()
    for u in reversed(r.json().get("result", [])):
        msg = u.get("message") or u.get("channel_post") or {}
        if msg.get("chat", {}).get("id"):
            return str(msg["chat"]["id"])
    raise RuntimeError('Mande um "oi" para o seu bot no Telegram e clique de novo.')
