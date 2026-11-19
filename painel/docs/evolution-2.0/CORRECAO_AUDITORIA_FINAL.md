# CortesEX-PRESS 2.0 — Correção integral, auditoria técnica e validação final

Data: 19/11/2026 · Branch: `claude/cortesex-painel` · Base auditada: `5dc84d7` · Correção: `c1ada26` e seguintes.

## 1. Resumo executivo

O código real foi auditado de novo, sem confiar nos relatórios anteriores. Dos 25 achados da auditoria
independente, **19 foram corrigidos com teste comportamental**, 1 continua aberto (L3) e 5 informativos
foram aceitos e documentados. A rodada achou **14 problemas novos**; os 2 mais graves eram a **Graph API
v21.0, que deixa de existir em 21/01/2027** (o Facebook pararia de postar), e o **ffmpeg-neto que ficava vivo**
quando a gravação era parada. Os dois foram corrigidos. A suíte foi de 79 testes + 4 xfail para **131 testes,
sem xfail e sem falhas**.

A publicação real **não foi validada**: não havia autorização para publicar. Por isso o veredito é
**PASS WITH WARNINGS**.

## 2. Escopo e restrições respeitadas

Sem publicação real, sem conectar contas, sem revogar credenciais, sem apagar dados ou vídeos, sem release,
sem deploy, sem custo externo. Nenhum teste foi removido para passar e nenhuma assertion foi enfraquecida:
os 4 xfail viraram testes **mais fortes**. O workflow de release só publica com disparo manual, e ele não foi disparado.

## 3. Metodologia

1. Ler o código atual, não os relatórios.
2. Reproduzir cada achado com teste (HTTP real, socket cru, Chromium real, processos reais, ffmpeg real e SQLite real).
3. Corrigir.
4. Rodar o teste e a suíte inteira.
5. Rodar no CI Windows.
6. Conferir na documentação oficial o que depende de plataforma externa.

Os status usados estão em CORRECOES_AUDITORIA.md.

## 4. Linha de base

HEAD `5dc84d7`, árvore limpa: **79 passaram + 4 xfail** (M1, M2, M3 e L1 abertos). Havia um teste tautológico
(`… or True`) e 4 testes que só liam o código-fonte.

## 5. Achados e status

A tabela completa está em **CORRECOES_AUDITORIA.md**. Resumo:

| Origem | CORRIGIDO | CONFIRMADO E ABERTO | ACEITO COMO RISCO | NÃO CONFIRMADO | BLOQUEADO |
|---|---|---|---|---|---|
| Auditoria (H/M/L/I = 25) | 20 (H1–H4, M1–M8, L1, L2, L4–L7, I5) | 1 (L3) | 4 (I1, I2, I3, I6) + I4 esperado | 0 | 0 |
| Novos (COR-1…14) | 11 | 0 | 1 (CSP) | 1 (limites de parte do TikTok) | 1 (publicação real) |

## 6. Segurança do painel

O controle de Host ficou estrito, com um único Host aceito. O CSRF agora usa um token por execução, junto com Origin e Sec-Fetch-Site. Foram adicionados limites de corpo e métodos, novos cabeçalhos e a recusa de symlink. O XSS foi testado no Chromium real. Os detalhes e as exceções justificadas (CSP `unsafe-inline` e usuários locais) estão em **VALIDACAO_SEGURANCA.md**.

## 7. Segredos

O DPAPI agora tem assinaturas explícitas e `UI_FORBIDDEN`. Se o cofre falhar, o programa entra em modo degradado sem perder nada. A redação foi ampliada e testada com 10 formatos reais. Os eventos antigos são redigidos uma vez, e os backups fora do Windows ficam com permissão 600/700. Nenhum segredo aparece neste relatório.

## 8. Publicação e idempotência

Uma reserva ativa por corte e rede, garantida pelo **índice único no banco**: o teste com 4 processos reais deu 1 reserva e 3 recusas. O repost forçado é explícito. Uma queda, ou uma falha do banco depois da confirmação, deixa a postagem **incerta**, e o agendador não a repete. Ver **VALIDACAO_PUBLICACAO.md**.

## 9. Plataformas

| Rede | Conferido na documentação oficial | Status |
|---|---|---|
| Facebook | v21.0 removida em 21/01/2027; v26.0 atual | CORRIGIDO → v26.0 |
| TikTok | PKCE desktop com desafio em hex e verificador de 43 a 128 caracteres | CONFIRMADO (o código já estava certo) |
| TikTok | regra das partes `floor(tamanho/chunk)` | CORRIGIDO |
| TikTok | limites de 5 e 64 MB por parte | NÃO CONFIRMADO |
| TikTok | o token é renovado antes da verificação | CORRIGIDO |
| YouTube | upload resumível sem retomada persistente | ACEITO COMO RISCO (sem duplicata) |
| Todas | publicação real | BLOQUEADO (sem autorização) |

## 10. Pipeline e processos

- A IA roda num worker próprio: a etapa de mídia não espera pela IA, que tem 30 s por tentativa e 150 s no total.
- O editor reserva o corte e respeita o limite de workers.
- O disco baixo interrompe uma gravação que já está em andamento.
- Parar um processo encerra a **árvore inteira** (`taskkill /T` e `killpg`).
- A limpeza protege os blocos de candidatos pendentes, com um teto de 3 h.
- A reivindicação de etapa é atômica mesmo entre processos.
- Uma queda não sobrescreve textos já editados.

## 11. Nota editorial e IA

- Pesos inválidos, negativos ou desconhecidos valem 0.
- Valores de critério ficam entre 0 e 1; NaN e texto contam como "sem dado".
- Peso total zero devolve "sem nota".
- O ranking prioriza a cobertura mínima. No automático, uma nota mínima também exige cobertura (padrão 0,5).
- O L3 (`inicio_fim` infla a nota) continua aberto: falta calibrar com dados reais.
- Contra prompt injection, os dados externos vão só na mensagem do usuário, delimitados; o teste tautológico foi trocado.
- A resposta da IA é validada campo a campo.

## 12. Agendador e autorização

- A permissão "editar" agora é aplicada.
- "Publicar" e "redes" continuam exigidos por envio.
- Picos que chegam durante o intervalo mínimo são agrupados no corte aberto.
- O modo automático continua desaconselhado até a validação real.

## 13. Recuperação

Um banco corrompido é aberto só para leitura e conferido. Se a conferência falha, o painel abre o modo de recuperação, que lista os backups e restaura um deles guardando o arquivo danificado. Nada é apagado. A tabela completa está em **RECUPERACAO.md**.

## 14. Testes e CI

| | Antes | Depois |
|---|---|---|
| Linux (Python 3.13, ffmpeg real, Chromium real) | 79 + 4 xfail | **131 passaram, 0 xfail, 0 falhas** |
| Windows CI (windows-latest, Python 3.11, DPAPI real) | 83 (run #8) | **129 passaram, 1 pulado (XSS: sem playwright), 0 falhas** — run #10, `c1ada26`, depois PyInstaller, teste do executável e instalador OK; release **pulado** |

Os testes pulam condicionalmente em dois casos: o XSS, quando não há node + playwright, e o symlink e a permissão POSIX no Windows. Detalhes em **TESTES_COMPORTAMENTAIS.md**.

## 15. Riscos e pendências

1. A publicação real nunca foi validada nas 3 redes (**BLOQUEADO**). Ela é o próximo passo, com acompanhamento: 1 corte por rede, em modo semiautomático.
2. O TikTok depende da aprovação do app para postar em público.
3. L3 continua aberto.
4. A CSP mantém `unsafe-inline`.
5. Usuários locais do mesmo Windows conseguem acessar o painel.
6. O upload do YouTube não é retomado.
7. Os limites de parte do TikTok não foram confirmados.
8. O teste de XSS não roda no CI Windows.

## 16. Veredito

**PASS WITH WARNINGS.**

- **Base local** (gravação, corte, legenda, nota, biblioteca, editor, recuperação, segurança do painel e segredos): sustentada por código, testes comportamentais e execução real em Linux (131/131) e Windows (129 + 1 pulado; run https://github.com/paulojoca62-gif/clipador-youtube/actions/runs/44581172828).
- **Publicação:** correta conforme a documentação oficial e os testes simulados, mas **não validada em rede real**. O uso recomendado é o modo **semiautomático** até um envio real acompanhado em cada rede.


Observação do CI: o log do Windows mostra uma linha "Exception occurred during processing of request" — é o servidor registrando a conexão que o próprio teste de corpo inválido fecha de propósito (socket cru); o teste passou. O teste `test_backup_da_atualizacao_so_o_dono_le` foi adicionado depois do run #10 (é pulado no Windows por definição).
