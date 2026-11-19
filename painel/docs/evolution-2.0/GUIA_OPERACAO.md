# Guia de operação 2.0

- **Modos** (Configurações → Agendador): Manual (você publica), Semiautomático (você aprova; o agendador publica — recomendado e padrão em instalações novas), Automático (publica o que passa nos filtros). Atualizações da 1.x mantêm “automático” para quem já postava sozinho.
- **Autorização** (Canais): sem status “autorizado” + “pode publicar” + a rede marcada (e dentro da validade), nada é publicado automaticamente; no manual, o painel pede confirmação explícita. O registro não substitui análise de direitos.
- **Pipeline** (Fila → Em processamento): detectado → capturado → transcrito → montado → textos → revisão/aprovado. Fechar o programa no meio retoma do último ponto. Falhas temporárias tentam de novo com espera crescente; depois de 3 tentativas o corte vai para “Falharam” com o botão Reprocessar.
- **Editor**: ajuste texto e tempos das legendas, ou início/fim do corte, e “Montar de novo”. Reaproveita gravação, transcrição e textos. O vídeo anterior fica guardado como “original”. Precisa da fonte (guardada por 3 dias, configurável).
- **Nota editorial**: 0–100 com a confiança ao lado. Critérios sem dado não contam (ex.: sem Gemini, contexto/entretenimento ficam de fora). Pesos em Configurações → Sistema (cada mudança cria uma nova versão).
- **Pausas**: limite do dia → pausa longa automática; login/permissão/privacidade → pausa até você clicar em Retomar (Dashboard); instabilidade → espera crescente (2 min → 1 h).
- **Envios incertos**: aparecem na Fila; confirme manualmente.
- **Backup**: a cada versão nova, `backups/atualizacao-…` guarda config (sem chaves), cofre e banco (5 últimos). Migração do banco também guarda uma cópia antes de alterar.
- **Limpeza**: vídeos após N dias (aprovados e envios em andamento são protegidos; publicados opcionalmente), fontes após 3 dias, temporários após 6 h. Abaixo do mínimo de disco, a gravação pausa.
- **Diagnóstico**: saúde geral, recursos, dependências, contas, processos, gravações e erros recentes.
