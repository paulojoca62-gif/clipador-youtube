# Arquitetura atual (antes da 2.0)

- **Linguagem**: Python 3.11 (exe via PyInstaller). Sem framework web: `http.server` em `127.0.0.1:8777`.
- **Front-end**: uma página `cortesex/web/index.html` (HTML/CSS/JS puro, sem build).
- **Banco**: SQLite `cortesex.db` (tabelas `cortes`, `postagens`, `eventos`). Configuração em `config.json` (inclui chaves em texto puro).
- **Processos externos**: `streamlink` (o próprio exe com `--streamlink`) → `ffmpeg` segmentando em blocos de 4 s; `ffmpeg` para concatenar e renderizar; `faster-whisper` em CPU (int8).
- **Threads** (todas em um processo):
  - por canal: gravação, chat (WebSocket da Twitch), detecção (1 s);
  - motor: render (fila **em memória**), agendador (20 s), limpeza (30 s), textos da IA (5 min).
- **Módulos**: `config`, `db`, `gravador`, `render`, `ia` (Gemini via endpoint compatível com OpenAI), `motor`, `servidor`, `plataformas/{youtube,tiktok,facebook,telegram}`, `app` (entrada), `util`.
- **Instalador**: `instalador.iss` (Inno Setup, por usuário, sem admin) + workflow `.github/workflows/cortesex-windows.yml`.

Fluxo: pico → job na fila em memória → espera o fim da janela → concatena blocos → (Whisper) → render 9:16 → Gemini → linha em `cortes` → agendador escolhe o maior `score` (pico) ainda não postado na rede → `postagens`.
