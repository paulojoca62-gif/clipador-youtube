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

## Atualização automática

Configurações → Sistema → **🔄 Atualizações**.

- **Atualizar sozinho** (padrão): a cada 6 h o programa consulta o release `cortesex-latest` no GitHub
  oficial. Havendo build novo, baixa o instalador, confere o SHA-256 (do `versao.json` e o calculado pelo
  GitHub) e espera um momento livre: nenhum envio em andamento, nenhum corte em processamento e nenhuma
  live gravando. Se as lives não pararem em 24 h, atualiza assim que processamento e envios estiverem livres.
  Então fecha, instala em silêncio (backup automático do banco e da configuração) e abre de novo.
- **Só avisar**: mostra o build novo e o botão **Atualizar agora**.
- **Não verificar**: desliga a consulta.

Cada build é tentado no máximo 3 vezes; depois disso o painel pede a instalação manual. Os arquivos ficam
em `<pasta de dados>\atualizacoes\` (`estado.json`, `ultima_instalacao.txt` com o código de saída do
instalador e o log da instalação). Só os programas instalados a partir do build com o atualizador em diante
se atualizam sozinhos; a instalação anterior a ele precisa ser feita à mão uma última vez.

## Revisão pelo Telegram

Configurações → Contas → Telegram → **Revisar pelo Telegram**. Cada corte que fica "aguardando revisão"
chega no chat configurado com o vídeo (até 50 MB) e os botões **✅ Aprovar**, **❌ Descartar** e
**🚀 Postar agora** (nas redes ligadas no Agendador). Só o chat configurado comanda; os botões são
assinados (HMAC) e um toque repetido não repete a ação. Funciona enquanto o programa estiver aberto.

## TikTok e Facebook via Zernio

Para publicar sem app próprio (o TikTok só deixa app próprio postar em público depois da auditoria):
1. Crie a conta em zernio.com e conecte lá o TikTok (entra pelo TikTok for Business) e a Página do Facebook.
2. Gere uma chave de API no Zernio; em Configurações → Contas → **Zernio**, cole a chave e clique
   **Salvar e buscar contas**; escolha a conta do TikTok e a Página; **Salvar contas**.
3. No cartão do TikTok e/ou do Facebook, mude **Conexão** para **Via Zernio** e salve.

- TikTok: publica em **público**, até 15 vídeos por conta a cada 24 h (acima disso o Zernio segura na fila).
  A confirmação pode levar horas: o envio fica "processando" e o verificador confirma depois.
- Facebook: sai como **Reel** (3–60 s). Cortes maiores vão com os **últimos 60 s** (onde fica o pico);
  só a cópia enviada é recortada. Meta limita 30 Reels por Página a cada 24 h.
- Cada envio leva uma Idempotency-Key ligada ao registro do envio: repetir o mesmo envio não duplica.
- Custo: 2 contas grátis no Zernio; a partir da 3ª, cobrança mensal por conta (ver zernio.com/pricing).
- A chave fica no cofre (DPAPI). Recomendado ligar "zero content retention" no painel do Zernio.

## IA com reserva: Gemini + OpenRouter

Configurações → Contas → **OpenRouter (reserva da IA)**. O Gemini continua sendo o primeiro; se ele
falhar (ocupado, fora do ar, modelo indisponível, tempo esgotado) ou não tiver chave, o OpenRouter
escreve título, legenda, hashtags e análise, com o mesmo prompt protegido e a mesma validação.

- Modelos vazio = automático: os modelos `:free` do momento que respondem em JSON (lista consultada a
  cada 12 h, sem os modelos minúsculos). O OpenRouter recebe até 3 e pula sozinho para o próximo.
- Limites dos grátis: 20 pedidos/min e 50/dia (1.000/dia depois de comprar US$ 10 em créditos uma vez).
- Saldo negativo bloqueia até os grátis (402). Privacidade dos provedores: openrouter.ai → Settings → Privacy.
