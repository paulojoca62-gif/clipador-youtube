# Roteiro de validação real das publicações (feito pelo usuário)

Nada disso foi executado pelo desenvolvedor: depende das suas contas. Use contas/vídeos de teste e privacidade restrita.

1. **Preparar**: instale o 2.0, abra Diagnóstico (tudo “operacional” ou “não configurado”). Em Canais, registre a autorização do canal de teste com as redes desejadas.
2. **YouTube**: Configurações → Redes e IA → YouTube → Conectar canal → Testar (mostra o nome do canal). Defina privacidade “Privado”. Em Cortes, escolha um corte → Postar agora → youtube.
   - Esperado: Publicações mostra “processando” e, em até alguns minutos, “postado” com link `youtu.be/…`. Se o YouTube travar como privado, o detalhe aparece.
3. **TikTok** (conta privada se o app não for auditado): Conectar conta → Testar (mostra as privacidades permitidas) → Postar agora → tiktok.
   - Esperado: “processando” → “postado” quando o TikTok concluir (`PUBLISH_COMPLETE`), ou “erro” com o motivo.
4. **Facebook**: Buscar minhas Páginas → escolher → Testar → Postar agora → facebook.
   - Esperado: “processando” → “postado” com o link do Reel.
5. **Recuperação**: durante um envio grande, feche o programa pelo Gerenciador de Tarefas e abra de novo. Esperado: o envio aparece como **incerto** na Fila; confira na rede e marque “Foi publicado” / “Não foi”. O agendador não repete sozinho.
6. **Pausa**: desconecte uma conta e deixe o agendador tentar. Esperado: a rede pausa com o motivo e só volta com “Retomar”.
7. Anote os resultados. Só depois disso as integrações podem ser consideradas validadas.
