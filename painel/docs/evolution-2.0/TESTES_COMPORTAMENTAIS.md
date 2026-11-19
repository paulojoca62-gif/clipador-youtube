# Testes comportamentais

Regra desta rodada: **um teste só conta se reproduz o comportamento** (requisição HTTP real, socket cru,
navegador real, processo real, ffmpeg real, banco SQLite real). Testes que só conferiam string de código
(`inspect.getsource`) ou que nunca falhavam foram trocados.

## Antes × depois

| | Antes (HEAD 5dc84d7) | Depois |
|---|---|---|
| Total | 79 passaram + **4 xfail** | **131 passaram, 0 xfail, 0 falhas** (Linux) |
| xfail | M1, M2, M3, L1 (marcavam problemas abertos) | removidos: os 4 problemas foram corrigidos e viraram testes normais |
| Teste tautológico | `test_editorial_legendas_ia.py` tinha `… or True` | trocado por asserts reais (o texto externo **não** está na mensagem de sistema e **está** na do usuário) |
| Testes de código-fonte | 4 com `inspect.getsource` | 0 |

## Testes trocados (fracos → comportamentais)

| Antes | Depois |
|---|---|
| xfail "pode_gravar só é checado antes" (getsource) | `test_disco_baixo_interrompe_gravacao_em_andamento`: um streamlink falso manda vídeo MPEG-TS de verdade pelo ffmpeg; o disco "acaba" no meio; o teste exige que a gravação pare, que **todos** os processos morram e que nenhum bloco novo apareça. **Achou um bug novo:** o ffmpeg-neto ficava vivo (corrigido com grupo de processos + `matar_arvore`). |
| xfail "remontar aceita montando" | `test_remontar_recusa_corte_em_processamento`, `test_editor_reserva_o_corte_enquanto_monta`, `test_editor_falha_mantem_estado_e_video` |
| xfail "limpeza apaga blocos de candidatos" | `test_limpeza_do_buffer_protege_candidatos_pendentes` (blocos reais em disco) |
| xfail "editar não é aplicado" | `test_permissao_editar_e_aplicada` |
| "banco corrompido levanta DatabaseError" | `test_banco_corrompido_nao_e_sobrescrito`: o arquivo fica byte a byte igual e o backup aparece na lista |
| cofre: "levanta exceção" | `test_falha_no_cofre_durante_migracao_nao_perde_chaves`: abre em modo degradado e a chave continua funcionando |
| POST com `X-CortesEX: 1` | o teste lê o token da página servida, como o navegador; o valor antigo agora dá 403 |

## Testes novos (`tests/test_correcoes.py`)

- **Painel:** 13 variantes de Host recusadas, Host duplicado (socket cru), Host aceito; CSRF (sem token,
  token errado, `cross-site`, `Origin: null`); corpo (não JSON, lista, `text/plain`, `Content-Length` -5/abc/
  enorme, chunked); métodos PUT/DELETE/PATCH/OPTIONS/HEAD; cabeçalhos; symlink para fora da pasta de dados.
- **XSS no Chromium real** (Playwright via node): 6 payloads em todos os campos exibidos e em 4 abas.
  Pulado automaticamente onde não há node + playwright (o runner Windows não tem; roda no Linux).
- **Redação:** 10 strings realistas (troca de código OAuth Google, resposta de token, Bearer, chave
  Gemini em cabeçalho e URL, erro do requests com token do Telegram, Graph API, TikTok, `Authorization: OAuth`,
  URL da Twitch com `sig`/`token`); migração redige eventos antigos.
- **Publicação:** reserva atômica entre **4 processos reais**; repost forçado; banco falhando depois
  da confirmação → incerto e o agendador não reposta; reivindicação de etapa entre 4 processos;
  verificação do TikTok renova o token.
- **Pipeline/IA:** worker de mídia não pega etapa da IA (e vice-versa); orçamento total de tempo da IA
  com relógio simulado; textos editados não são sobrescritos depois de uma queda; agrupamento durante
  o intervalo mínimo.
- **Nota editorial:** todos 1, todos 0, vazio, valores fora de 0–1, NaN, texto, critério inventado, pesos
  negativos/inválidos/zerados; ranking por cobertura (nota 95 com 20% não passa à frente de 70 com 90%).
- **Plataformas:** matemática das partes do TikTok; versão da Graph API.
- **Recuperação:** restaurar guarda o danificado (+ WAL) e copia o backup; página de recuperação com Host e
  token; backup de atualização com permissão 600/700.

## O que os testes **não** provam

- Publicação real em YouTube, TikTok ou Facebook (rede simulada; sem autorização para publicar).
- Comportamento com uma live real da Twitch por horas (o gravador é testado com fonte sintética).
- O teste de XSS não roda no CI Windows (sem playwright lá).
