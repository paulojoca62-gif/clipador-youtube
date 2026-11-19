# Validação da publicação

**Nenhum vídeo real foi publicado nesta rodada** (sem autorização para isso). Tudo abaixo é validado
contra a documentação oficial e por testes com a rede simulada. A publicação real continua **não
validada** e o modo automático continua **não recomendado** até um teste real acompanhado.

## Idempotência (vale para as 3 redes)

- Índice único parcial `ux_postagem_ativa (corte_id, plataforma) WHERE status IN ('enviando','processando',
  'postado','incerto') AND forcado = 0`: o **banco** recusa uma segunda reserva ativa, mesmo vinda de
  outro processo. Teste: `test_reserva_atomica_entre_dois_processos` (4 processos → 1 "ok", 3 "duplicada").
- Repostar de propósito continua possível, marcado como `forcado=1` (`test_repostar_de_proposito_continua_possivel`).
- Queda de rede durante o envio, ou depois da confirmação antes de gravar o resultado → **incerto**,
  nunca reenviado sozinho (`test_banco_falha_depois_da_confirmacao_vira_incerto`, testes H4).

## YouTube
- Upload resumível da API Data v3. **Limitação conhecida:** a sessão resumível não é guardada no disco;
  se cair, a próxima tentativa recomeça do zero (o estado "incerto" impede duplicata automática).
- Não validado com conta real nesta rodada.

## TikTok (Content Posting API)
- **PKCE desktop:** `code_challenge` em **hex** do SHA-256 e verificador de 64 caracteres — **CONFIRMADO**
  na documentação oficial (Login Kit for Desktop: verificador de 43 a 128 caracteres, desafio em hex).
  Fonte: https://developers.tiktok.com/doc/login-kit-desktop
- **Partes:** vídeo até 64 MB vai numa parte só; acima disso, partes de 10 MB e
  `total_chunk_count = floor(tamanho / chunk_size)`, com a última levando o resto; cada PUT com
  `Content-Range`. Fonte: https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide.
  Os limites exatos de 5 MB/64 MB por parte aparecem em fontes de terceiros e **não foram confirmados**
  na página oficial → **NÃO CONFIRMADO** (os valores usados ficam dentro dos dois). Teste: `test_partes_do_tiktok_seguem_a_regra_oficial`.
- **Verificação** de envios em processamento agora renova o token antes (AUD-M8). Teste: `test_verificacao_do_tiktok_renova_token`.
- Queda de conexão só vira "incerto" na **última** parte; antes disso é erro (nada foi publicado).
- App ainda depende da aprovação do TikTok para postar em público (auditoria do app) → **BLOQUEADO** por fator externo.

## Facebook (Graph API, Reels)
- A versão v21.0 usada antes é **removida em 21/01/2027**; atualizado para **v26.0** (versão atual, lançada
  em 29/07/2026). **CORRIGIDO.** Fontes: https://developers.facebook.com/docs/graph-api/changelog/versions
  e https://developers.facebook.com/blog/post/2026/07/29/introducing-graph-api-v26-and-marketing-api-v26/
- Teste de guarda: `test_versao_da_graph_api_nao_e_removida_em_2027`.
- Não validado com página real nesta rodada.
