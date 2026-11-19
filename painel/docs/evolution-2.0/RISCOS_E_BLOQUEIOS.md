# Riscos e bloqueios encontrados

## Segurança (preexistentes)
1. **CRÍTICO — DNS rebinding**: o servidor não valida o cabeçalho `Host`. Uma página maliciosa que faça o próprio domínio apontar para 127.0.0.1 vira “mesma origem”, consegue mandar o cabeçalho `X-CortesEX` e chamar qualquer ação (mudar configuração, postar, encerrar).
2. **ALTO — chaves em texto puro** no `config.json` (Gemini, client secrets, refresh tokens, token da Página, token do bot).
3. **MÉDIO — logs sem redação**: exceções de rede podem conter URLs com segredo (ex.: token do bot do Telegram vai na URL).
4. **MÉDIO — prompt injection**: mensagens do chat e a transcrição entram no mesmo texto das instruções do Gemini.
5. **BAIXO — falta de cabeçalhos** (`X-Frame-Options`, `nosniff`, `Referrer-Policy`); o código OAuth chega na URL do painel.

## Confiabilidade (preexistentes)
6. Fila de render em memória: fechar o programa perde os picos ainda não renderizados.
7. Envio interrompido vira `erro` ao reabrir → o agendador pode **repostar** algo que talvez tenha sido publicado (duplicidade).
8. Sem validação do vídeo antes de postar.
9. Pausa fixa de 1 h para qualquer erro “definitivo”; não há retomada manual; pausas se perdem ao reiniciar.
10. Streamlink travado (vivo, mas sem gravar) não é detectado.
11. Limpeza apaga o arquivo mas deixa o corte no banco (link quebrado no ranking); não protege cortes aguardando publicação; não monitora disco; temporários de render podem sobrar após queda.
12. Subprocessos ffmpeg sem timeout.

## Bloqueios externos (dependem do usuário/plataformas)
- **YouTube**: projeto sem auditoria → vídeos podem ficar privados; limite diário do canal atingido em 18/11; cliente OAuth sem o redirect `http://127.0.0.1:8777/`.
- **TikTok**: app em cadastro; sem auditoria só posta `SELF_ONLY` em conta privada.
- **Facebook**: app e Página ainda não configurados.
- Validação real de publicação exige contas conectadas pelo usuário — não pode ser feita daqui.
