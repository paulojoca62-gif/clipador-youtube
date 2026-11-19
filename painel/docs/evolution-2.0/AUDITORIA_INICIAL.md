# Auditoria inicial (Fase 0) — 19/11/2026

Base auditada: branch `claude/cortesex-painel`, commit `33d3204`, pasta `painel/`.

## Baseline de testes e build
| Item | Resultado |
|---|---|
| `pytest tests` | **12 passaram, 0 falharam** (todos com mocks; nenhum toca rede real) |
| `pyflakes cortesex tests run.py` | limpo |
| CI "CortesEX-PRESS (Windows)" | 5 execuções; a última (#5, `33d3204`) **passou**: testes, PyInstaller, exe abre o painel, streamlink embutido, Inno Setup, release `cortesex-latest` |
| Execução real no Linux (nuvem) | gravação de 2 canais, chat, pico automático, corte 55 s com legenda, Gemini: **funcionou** (18–19/11) |
| Execução real no Windows | feita pelo usuário: painel abre, grava, corta, Gemini funciona (log de 19/11 12:35) |
| Publicação real pelo painel novo | **nunca validada** (YouTube travou no `redirect_uri_mismatch`; TikTok em cadastro; Facebook não configurado) |

Problemas preexistentes registrados (não causados pela evolução): ver `RISCOS_E_BLOQUEIOS.md`.
