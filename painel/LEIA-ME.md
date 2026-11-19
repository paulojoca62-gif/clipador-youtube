# CortesEX-PRESS

Vigia até 5 canais da Twitch, corta a live quando o chat explode, escreve
título, legenda e hashtags com o Gemini e posta os melhores cortes no
YouTube Shorts, no TikTok e numa Página do Facebook, no ritmo que você
escolher.

## Instalar no Windows

1. Abra a página de releases do repositório e baixe
   **CortesEX-PRESS-Instalador.exe** (release `cortesex-latest`).
2. Rode o instalador. Ele não pede administrador e cria o atalho
   **CortesEX-PRESS** na área de trabalho.
3. Abra pelo atalho: o painel abre no navegador, em `http://127.0.0.1:8777`.

O Windows pode mostrar "O Windows protegeu o computador", porque o
instalador não tem assinatura digital paga. Clique em **Mais informações →
Executar assim mesmo**.

## O painel

| Aba | Para que serve |
|---|---|
| Painel | Canais ao vivo, ritmo do chat, botão **Clipar agora**, situação de cada destino |
| Ranking | Cortes do melhor para o pior pico do chat. Dá para assistir, editar os textos, gerar de novo com o Gemini, postar na hora ou descartar |
| Agendador | Quantos vídeos por hora ou por dia, horário e dias permitidos, nota mínima e em quais redes postar. Também a sensibilidade da detecção |
| Canais | Até 5 canais da Twitch e o layout do vídeo vertical |
| Contas | YouTube, TikTok, Facebook, Gemini e avisos no Telegram |
| Log | Cada postagem (link ou erro) e a atividade do sistema |

O agendador espalha os vídeos no período: com 5 por hora, posta um a cada
12 minutos, sempre o corte de maior nota que ainda não foi postado naquela
rede. Se uma rede avisar que o limite do dia acabou, ela pausa por 1 hora.

## Contas

- **YouTube**: no Google Cloud, ative a YouTube Data API v3, crie um ID do
  cliente OAuth do tipo **App para computador**, ponha o seu e-mail em
  "Usuários de teste", cole o ID e a chave no painel e clique em **Conectar
  canal**. O login se renova sozinho. Sem a auditoria da API do Google, o
  YouTube pode travar os vídeos como privados, e o canal tem limite de
  envios por dia (verifique o canal em youtube.com/verify).
- **TikTok**: app em developers.tiktok.com com Login Kit e Content Posting
  API (Direct Post). Sem a auditoria do TikTok, os posts saem privados e a
  conta precisa estar privada.
- **Facebook**: app do tipo Empresa; gere no Explorador da Graph API um token
  com `pages_show_list`, `pages_read_engagement` e `pages_manage_posts`, cole
  com o App ID e o App secret e escolha a Página. O token da Página não vence.
  Até 30 Reels por dia.
- **Gemini**: chave grátis em aistudio.google.com/apikey.
- **Telegram**: crie um bot no @BotFather, cole o token, mande um "oi" para o
  bot e clique em **Descobrir chat**.

As chaves ficam só no seu computador, em
`%LOCALAPPDATA%\CortesEX-PRESS\config.json`, junto com os cortes, o banco
`cortesex.db` e o log `cortesex.log`.

## Rodar pelo código (Windows ou Linux)

```bash
cd painel
pip install -r requirements.txt
python -m cortesex
```

Precisa do `ffmpeg` instalado (no instalador do Windows ele já vem junto).
Testes: `python -m pytest tests`.

## Avisos

- Corte e poste só lives de quem autorizou.
- Na primeira legenda, o Whisper baixa o modelo (uns 500 MB).
- O instalador inclui o ffmpeg (build GPL do projeto BtbN/FFmpeg-Builds).
