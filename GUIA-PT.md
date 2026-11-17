# Clipador YouTube: guia em português

Este repositório é uma versão modificada do
[Clips Kitty](https://github.com/ColinGPT9/clips-studio) (licença AGPL-3.0).
Ele monitora canais na **Twitch, Kick e YouTube**, corta os melhores momentos de
cada live com IA e **sobe os clipes direto no seu canal do YouTube**, sem serviço
pago no meio.

## O que mudou em relação ao original

No original, o modo automático ("hands-off") de um canal monitorado só publicava
pelo WoopSocial, um serviço pago, mesmo quando o destino era apenas o YouTube.
Nesta versão:

- **YouTube vai direto pela API oficial** quando a publicação no YouTube está ligada
  e um canal está conectado. O upload usa o mesmo mecanismo do botão "Upload" do
  editor, com o mesmo controle de cota e a mesma verificação depois do envio.
  Código: `server/youtube_automation.py`.
- **Nenhum clipe é enviado duas vezes.** Um clipe que já subiu ou está na fila é
  ignorado. Um envio que falhou pode ser repetido.
- **Espaçamento diário:** com "espalhar posts" ligado, cada clipe sobe na hora como
  **privado com data de publicação**, e o YouTube publica no horário. O computador
  pode ser desligado depois do upload.
- **Interface:** a opção "YouTube" aparece na configuração de publicação dos canais
  monitorados mesmo sem WoopSocial.
- Outras redes (TikTok, Instagram etc.) continuam pelo WoopSocial, como antes.

## Como funciona (e o que não faz)

1. O sistema verifica os canais monitorados a cada 15 minutos (configurável).
2. **Quando a live termina e o VOD (gravação) fica disponível**, ele baixa o vídeo.
   O corte não acontece durante a live: o Clips Kitty trabalha só com VODs.
3. Transcreve o áudio (Whisper), analisa áudio, imagem e chat (o chat só na Twitch,
   porque a Kick apaga o chat depois da live) e uma IA escolhe os melhores trechos.
4. Corta em formato vertical 9:16, com enquadramento em quem fala e legendas.
5. Sobe os clipes no YouTube como Shorts. O YouTube classifica sozinho pelo
   formato e pela duração.

**A live precisa ficar salva como VOD.** Na Twitch, ative em Configurações do
canal → Stream → "Armazenar transmissões anteriores".

## Requisitos

| | Mínimo | Recomendado |
|---|---|---|
| Sistema | Windows 10/11 (app). O motor Python roda em Linux/macOS e Docker. | |
| GPU | Opcional; sem GPU roda na CPU, mais devagar | NVIDIA RTX 3060 12 GB |
| RAM | 16 GB | 32 GB |
| Disco | Espaço para os VODs (vários GB por live) | SSD |

A IA pode ser **local** (Ollama, sem custo) ou **OpenRouter** (na nuvem, centavos
por VOD).

## Instalação

### Windows (com interface)

```bash
git clone https://github.com/paulojoca62-gif/clipador-youtube.git
cd clipador-youtube
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
cd ui && npm install && npm run dev
```

Detalhes e dependências opcionais estão na seção "Install and run" do `README.md`.

### Servidor sem interface (Linux, Docker)

```bash
docker compose up
docker compose exec ollama ollama pull gemma3:4b
```

Veja `docs/DOCKER.md`.

## Configurar o YouTube (uma vez, cerca de 10 minutos, grátis)

1. Crie um projeto no [Google Cloud Console](https://console.cloud.google.com/projectcreate).
2. Em APIs e serviços → Biblioteca, ative a **YouTube Data API v3**.
3. Preencha a **Tela de consentimento OAuth** e clique em **Publicar app**. Se ficar
   em "Teste", o login expira a cada 7 dias.
4. Em Credenciais → Criar credenciais → **ID do cliente OAuth** → tipo **App para
   computador**.
5. No Clips Kitty: **Settings → Publish to YouTube**, ligue a opção, cole o ID e o
   segredo do cliente e conecte o canal. O aviso "Google hasn't verified this app"
   é normal: clique em Avançado → Ir para (não seguro).

### Importante: vídeos públicos

> **Enquanto o projeto do Google Cloud não passar pela auditoria gratuita da API do
> YouTube, todo vídeo enviado pela API fica travado como privado, para sempre.**
> Peça a auditoria pelo
> [formulário da YouTube API Services](https://support.google.com/youtube/contact/yt_api_form)
> **antes** de publicar publicamente. Até ser aprovado, use **não listado** ou
> **privado** para testar.

### Cota

A cota padrão da API permite **cerca de 6 uploads por dia** por projeto. Configure
o canal monitorado com no máximo 5 ou 6 posts por dia. Para mais, peça aumento de
cota no mesmo formulário da auditoria.

## Monitorar canais

### Pela interface

1. Abra a página **Watch**.
2. Adicione o canal: Twitch (login), Kick (slug) ou YouTube (link ou ID `UC…`).
3. Em publicação, escolha **Automatic (hands-off)** e marque **YouTube**.
4. Ajuste quantos clipes por dia, o horário do primeiro post, suas hashtags fixas
   (ex.: `#nomedostreamer #twitch`) e um rodapé (ex.: "Live completa em twitch.tv/…").
5. Ligue a automação.

### Pela API (servidor sem interface)

```bash
python main.py serve --port 8765
```

```bash
curl -X PATCH http://127.0.0.1:8765/youtube/settings -H "Content-Type: application/json" -d '{"enabled": true, "privacy": "unlisted"}'
```

```bash
curl -X PATCH http://127.0.0.1:8765/automation -H "Content-Type: application/json" -d '{"enabled": true}'
```

```bash
curl -X POST http://127.0.0.1:8765/automation/watches -H "Content-Type: application/json" -d '{"platform": "twitch", "channel": "nome_do_canal", "publish": {"mode": "auto", "platforms": ["youtube"], "per_day": 5, "gap_hours": 2, "hashtags": ["nomedostreamer", "twitch"], "footer": "Live completa: twitch.tv/nome_do_canal"}}'
```

Use `"platform": "kick"` ou `"platform": "youtube"` para os outros sites. A conexão
da conta do YouTube (OAuth) precisa ser feita uma vez pela interface, ou pelas rotas
`/youtube/credentials` e `/youtube/connect` descritas em `docs/API.md`.

## Direitos de uso

Corte apenas lives **suas** ou de streamers que **autorizaram por escrito**. Clipes
de terceiros sem autorização geram strikes de direitos autorais e podem derrubar o
canal. Guarde as autorizações.

## Licença

AGPL-3.0-or-later, a mesma do projeto original (veja `LICENSE` e `NOTICE`). Se você
distribuir esta versão ou oferecê-la como serviço na rede, o código-fonte com as
modificações precisa continuar disponível.

## Testes das alterações

```bash
pip install pytest pyyaml requests httpx numpy pillow "fastapi<1"
python -m pytest tests/test_youtube_automation.py tests/test_automation.py tests/test_publish_jobs.py
```
