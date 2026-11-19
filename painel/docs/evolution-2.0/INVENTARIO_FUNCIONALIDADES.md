# Inventário de funcionalidades (antes da 2.0)

| Funcionalidade | Situação |
|---|---|
| Vigiar até 5 canais Twitch | implementada, validada em uso real |
| Gravação contínua (buffer 15 min) | implementada; **sem detecção de travamento** do streamlink |
| Chat anônimo por WebSocket | implementada, validada |
| Detecção de pico (ritmo/“clip”) | implementada, validada |
| Corte 9:16 com fundo desfocado | implementado, validado |
| Layout “câmera em cima” | **parcial**: não há onde informar a posição da câmera → cai no desfocado |
| Legenda queimada (Whisper, 3 palavras) | implementada; segmentação fixa; sem edição |
| Textos Gemini + fallback de modelos + retry 5 min | implementados, validados |
| Ranking (só pela nota do pico) | implementado |
| Agendador N por hora/dia, janela, dias | implementado (testes com mock) |
| YouTube (OAuth refresh + upload resumível) | código pronto, **sem validação real** |
| TikTok (PKCE + Direct Post) | código pronto, **sem validação real**; não consulta status do processamento |
| Facebook Reels | código pronto, **sem validação real**; não consulta status |
| Telegram | código pronto (simples) |
| Estados persistentes / retomada | **inexistente** (fila em memória; corte detectado e não renderizado se perde ao fechar) |
| Validação de mídia (ffprobe) | **inexistente** |
| Score editorial / categorias / corte adaptativo | **inexistentes** |
| Editor de legendas / estilos / safe areas | **inexistentes** |
| Autorização por canal | **inexistente** (só aviso no texto) |
| Modos manual/semi/auto | **inexistente** (só “postar automaticamente” liga/desliga) |
| Analytics | **inexistente** |
| Diagnóstico | **inexistente** |
| Backup/migração em atualização | **inexistente** |
