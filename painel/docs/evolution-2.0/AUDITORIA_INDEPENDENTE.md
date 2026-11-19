# CortesEX-PRESS 2.0 — Auditoria pós-implementação

Data: 19/11/2026 · Repositório `paulojoca62-gif/clipador-youtube` · Branch `claude/cortesex-painel` · Pasta `painel/`

> **Limite de independência:** esta auditoria foi feita pelo mesmo agente que escreveu a 2.0. Para compensar,
> o relatório anterior foi tratado como alegação a provar. As conclusões vêm de leitura do código, de testes
> novos que tentam quebrar o sistema (rodados **antes** das correções) e de logs reais do CI e da live.
> Uma revisão por outra pessoa continua recomendada.

## 1. Resumo executivo

- **O que se sustenta:** quase tudo o que o relatório anterior afirma tem evidência — o código, os 65 testes passando no Linux e no Windows, e o teste numa live real.
- **O que não se sustentava:** a auditoria achou **4 problemas ALTOS**, todos reproduzidos por teste antes de qualquer correção:
  - **AUD-H1:** corrida que **publicava o mesmo corte 3 vezes** na mesma rede.
  - **AUD-H2:** se o Whisper não carregasse, **o corte inteiro falhava**. Na 1.x, o corte saía só sem legenda (regressão).
  - **AUD-H3:** matar o programa à força deixava **streamlink e ffmpeg órfãos**, gravando blocos duplicados. Foi a causa real das falas repetidas de 18/11.
  - **AUD-H4:** uma conexão perdida no fim do upload virava "erro" em vez de "incerto", o que permitia **publicar de novo**.
- **Todos os 4 foram corrigidos e cobertos por testes.**
- **Problemas registrados sem correção:** 8 médios, 7 baixos e 6 informativos.
- **CRÍTICO:** nenhum encontrado.

**Veredito: PASS WITH WARNINGS.** Sem validação real de publicação, o sistema **não** está pronto para publicar no modo automático.

## 2. Commits auditados

| Commit | Conteúdo | Situação |
|---|---|---|
| `8c59dc7` | a 2.0 (39 arquivos em `painel/` e o workflow) | auditado |
| `0fdbdcd` | relatório final; nome do instalador "2.0.x" | auditado (só documentação e nome) |
| este | correções da auditoria (AUD-H1 a H4) e os testes de auditoria | ver §16 |

## 3. Metodologia

1. Conferi a branch, o commit, os arquivos alterados e os logs do CI ligados ao commit (`head_sha`).
2. Li todos os módulos do `painel/`, ~4.250 linhas de Python e o `index.html`.
3. Fiz a varredura de XSS em todas as interpolações dos templates do painel.
4. Escrevi testes de auditoria em `tests/test_auditoria.py`, cada um com uma falha controlada, num ambiente temporário. Nada tocou dados reais nem a rede.
5. Rodei esses testes **contra o código auditado**, sem nenhuma correção. Os que falharam viraram achados.
6. Corrigi só o que era ALTO. Depois rodei a regressão completa 3 vezes e conferi o resultado no Windows pelo CI.
7. Não fiz nenhuma publicação nem conectei contas.

## 4. Evidências da entrega (Fase 1)

| Item | Implementado | Evidência | Resultado |
|---|---|---|---|
| Commit 8c59dc7 na branch | sim | `git log`; run #6 com `head_sha=8c59dc7…` | OK |
| Novos módulos (editorial, legendas, midia, seguranca, segredos, diagnostico, analytics, atualizacao) | sim | `git show --stat 8c59dc7` | OK |
| Migração do banco | sim | `db.py`: `COLUNAS_CORTES`, `_migrar`; teste com o esquema 1.x real; migração de dados reais da 1.0 (11 cortes) | OK |
| 65 testes no Linux | sim | pytest local, 5 rodadas | OK |
| 65 testes no Windows | sim | log do run #6: **65 "." sem F/E/s/x** (o resumo "65 passed" some por causa do `-q` duplo) | OK |
| DPAPI real no Windows | sim | `test_cofre_cifrado_no_windows` exige o prefixo `CXS1D:` no win32 e passou no run #6 | OK |
| Exe abre o painel | sim | log: "painel respondeu"; "streamlink dentro do exe: streamlink 8.6.3" | OK |
| Instalador | sim | log: `Successful compile`; arquivo `CortesEX-PRESS-Instalador-1.0.6.exe` | OK (nome errado; corrigido em `0fdbdcd`) |
| ffprobe no pacote | sim | workflow `--add-binary "bin\ffprobe.exe;bin"` | OK |
| Release público só manual | sim | step "Publicar" = **skipped** no run #6 | OK |
| Live real | sim | corte #12 (19/11 16:27): estados completos, 1080x1920, nota 81 / confiança 0,80, textos do Gemini | OK |
| Publicação real | **não** | nenhum envio pela 2.0 | NÃO VERIFICADO |

## 5. Funcionalidades verificadas

| Funcionalidade | Como foi verificada |
|---|---|
| Estados persistentes e histórico | teste real com ffmpeg, live real |
| Retomada do checkpoint depois de queda simulada | teste |
| Nova tentativa com espera crescente | teste |
| Falha definitiva (buffer expirado) | teste |
| Dois workers não pegam o mesmo corte | teste novo: 4 threads, 1 processa |
| Validação de vídeo | 6 casos: resolução, sem áudio, duração, lixo, inexistente, bom |
| Migração do banco 1.x, com backup, sem repetir | teste |
| Backup de atualização restaurável | teste novo: apaga tudo, restaura copiando de volta e as chaves e cortes voltam |
| Banco corrompido não é sobrescrito | teste novo |
| Migração do cofre que falha não perde a chave | teste novo: `config.json` fica intacto |
| DNS rebinding, Origin, cabeçalhos, 405, video fora da pasta → 404 | testes de servidor real |
| Callback OAuth com `state` forjado não troca o código | teste novo |
| Gate de autorização | 6 testes, incluindo novos: revogação vale para cortes já aprovados; "gravar" não implica "publicar" |
| Modos, ordenação e pausas por tipo de erro | testes |
| Pausa e envio incerto sobrevivem ao reinício; incerto não é repostado | teste novo |
| Corte adaptativo, legendas (3 estilos, área segura), editor (re-render guarda o original) | testes e live real |
| IA: injeção, validação, filtro, fallback limitado, Retry-After | testes |
| Diagnóstico e analytics sem segredos e sem inventar dados | testes |

## 6. Funcionalidades não verificadas

- **Publicação real** em YouTube, TikTok e Facebook, inclusive OAuth, upload, status e privacidade.
- **Instalação e atualização no Windows do usuário:** foi testada só no runner do GitHub.
- **Travamento real do streamlink:** a lógica dos 45 s existe no código, mas nunca houve um travamento de verdade para testar.
- **Timeout real do ffmpeg:** o caminho de código existe, mas não foi provocado.
- **Reconexão do chat em quedas reais da Twitch.**
- **Coleta real de métricas do YouTube.**
- **Vários canais ao vivo ao mesmo tempo sob carga:** o teste real foi com 1 canal ao vivo e 1 offline.

## 7. Problemas encontrados

### Altos (corrigidos)

| ID | Arquivo / local | Evidência (antes da correção) | Impacto | Reproduzir | Correção |
|---|---|---|---|---|---|
| AUD-H1 | `motor.py` `Motor.postar` | `test_corrida_nao_publica_duas_vezes`: **"publicou 3 vezes"** | o agendador junto com "Postar agora", ou um clique duplo, publica o mesmo corte várias vezes | 3 threads chamando `postar` no mesmo corte e rede, com uma demora entre a checagem e o registro | checagem "já postado", autorização e registro do envio passaram a ser uma operação só (`_lock_reserva`) |
| AUD-H2 | `motor.py` `_etapa_transcrever` | `test_falha_do_whisper_nao_impede_o_corte`: o corte terminou em **falhou** | sem o modelo do Whisper (sem internet no primeiro uso, falha de download), **nenhum** corte é produzido; regressão em relação à 1.x | fazer `transcrever` levantar uma exceção | o corte segue sem legenda, com a janela fixa e a marca `sem_transcricao` |
| AUD-H3 | `gravador.py` (`subprocess.Popen`) | `orfao.py`: depois de `kill -9` no pai, **o filho continuou vivo**; mesmo sintoma do incidente de 18/11 | gravadores órfãos duplicam blocos e os cortes saem com a fala repetida | matar o CortesEX-PRESS à força durante uma gravação | `util.popen_vinculado`: Job Object `KILL_ON_JOB_CLOSE` no Windows e `PR_SET_PDEATHSIG` no Linux; teste `test_processo_de_gravacao_morre_junto_com_o_programa` |
| AUD-H4 | `motor.py`, `plataformas/*` | uma conexão caída no PUT do YouTube/TikTok ou no "finish" do Facebook ficava **erro** e voltava para a fila | o vídeo pode ter sido publicado, a resposta se perdeu e o agendador repostava | levantar `ConnectionError` no `postar` | esses casos agora ficam **incerto**, e só você libera de novo; teste parametrizado |

### Médios (registrados nesta auditoria — **todos CORRIGIDOS depois**, ver CORRECOES_AUDITORIA.md)

| ID | Local | Evidência | Impacto | Recomendação |
|---|---|---|---|---|
| AUD-M1 | `gravador._gravar_uma_vez` | o teste xfail confirma que `pode_gravar` só é checado antes de começar uma gravação | com disco baixo, uma live **em andamento continua gravando**; o painel diz "gravação pausada" | checar `pode_gravar()` dentro do laço da gravação |
| AUD-M2 | `motor.remontar` | xfail: aceita um corte em `montando` | o editor e o pipeline montam o mesmo arquivo ao mesmo tempo; o estado pode ficar errado | aceitar só `aguardando_revisao` ou `aprovado`, e rodar a montagem pelo limite de workers |
| AUD-M3 | `gravador.limpar` | xfail: apaga blocos com mais de 15 min mesmo que um corte detectado ainda precise deles | com fila longa (IA lenta, muitos picos), o corte falha por "buffer expirou" | não apagar blocos a partir do `janela_ini` do candidato pendente mais antigo |
| AUD-M4 | `ia.analisar_e_escrever` no worker | até 6 tentativas de 45 s, perto de 4,5 min, bloqueando 1 worker | atrasa a captura dos outros cortes (somado ao M3, risco de perder cortes) | tirar a IA do worker de mídia ou baixar o tempo máximo |
| AUD-M5 | `editorial.pontuar` + ordenação | a nota é renormalizada só pelos critérios conhecidos; a ordenação ignora a confiança | um corte com 20% de confiança pode passar à frente de um com 100% | ordenar por `nota × confiança`, ou exigir uma confiança mínima no automático |
| AUD-M6 | `db.Banco` (abertura) | teste: banco corrompido gera `DatabaseError` | o programa não abre; existe backup, mas não há restauração guiada | detectar a corrupção e mostrar como restaurar o último backup |
| AUD-M7 | `config._migrar_segredos` | teste: se o cofre falha, levanta exceção (sem perda de dados) | o programa não abre se a DPAPI falhar | abrir em modo degradado com aviso e manter as chaves no JSON até resolver |
| AUD-M8 | `motor.verificar_processando` (TikTok) | usa o `access_token` sem renovar | a verificação falha se o token venceu (24 h) e o envio vira incerto | chamar `tiktok.renovar` antes de verificar |

### Baixos

| ID | Local | Observação |
|---|---|---|
| AUD-L1 | `motor.remontar` | a permissão "editar" é registrada, mas não bloqueia o editor (xfail) |
| AUD-L2 | `deteccao.agrupar_segundos` (30) < `cooldown_segundos` (75) | o agrupamento quase nunca age em picos automáticos; o relatório anterior superestimou essa função |
| AUD-L3 | `editorial.criterios` "inicio_fim" | mede o alinhamento que o próprio algoritmo escolheu, por isso quase sempre vale 1,0 e infla a nota |
| AUD-L4 | `gravador._loop_chat` | reconexão a cada 5 s fixos, sem espera crescente |
| AUD-L5 | `atualizacao` (Linux) | fora do Windows, o backup copia o cofre em texto (`CXS1P`), protegido só por permissão |
| AUD-L6 | tabela `eventos` | eventos gravados pela 1.x, antes da redação de segredos, podem conter dados sensíveis e aparecem em Publicações → Atividade |
| AUD-L7 | `servidor.do_POST` | um `Content-Length` negativo prende a thread; só um processo local consegue fazer isso |

### Informativos
- **I1:** outros usuários do mesmo Windows conseguem abrir o painel em 127.0.0.1, porque não há senha.
- **I2:** a "confiança" é a fração do peso total que foi avaliada. Tem significado matemático claro, mas **não** é uma confiança estatística.
- **I3:** o upload do YouTube não retoma um envio parcial; na próxima tentativa recomeça do zero, sem criar duplicata.
- **I4:** a desinstalação não apaga a pasta de dados (comportamento esperado).
- **I5:** a recuperação a partir de backup é manual, copiando os arquivos (foi testada).
- **I6:** o score técnico usa penalidades heurísticas fixas.

## 8. Segurança

| Área | Situação |
|---|---|
| Host, Origin, DNS rebinding e CSRF | OK, testado com servidor real |
| XSS | varredura de todas as interpolações: o conteúdo externo (títulos, legendas, chat, erros) passa por `esc`. Os `onclick` usam só nomes de canal já limpos (`[a-z0-9_]`) ou chaves fixas. Nenhum XSS encontrado |
| SSRF | nenhum endpoint busca uma URL vinda do usuário |
| Path traversal | vídeos são servidos só pelo id do banco e só dentro da pasta de dados (teste novo); nomes de canal são limpos |
| Cookies e WebSockets do painel | não existem |
| Subprocessos | sempre lista de argumentos, sem shell; canal, fonte e câmera validados |
| Segredos em logs | redação aplicada (o log do teste mostra `chave ***`) |
| OAuth | `state` de uso único (teste novo de `state` forjado) e PKCE no TikTok |
| DPAPI | cifra real no Windows (CI); cofre ilegível é isolado, não sobrescrito |
| Backups | configuração sem chaves; cofre cifrado no Windows (no Linux, ver L5) |
| Pendências | M6, M7, L5, L6, L7 e I1 |

## 9. Recuperação

| Cenário | Situação |
|---|---|
| Queda no meio da montagem | retoma do checkpoint, testado |
| Falha temporária | nova tentativa com espera crescente, testado |
| Falha definitiva | vai para "falhou", testado |
| Workers concorrentes | 1 pega o corte, testado |
| Envio interrompido | fica incerto e não é repostado, testado |
| Resposta perdida no upload | corrigido (H4) |
| Gravador órfão | corrigido (H3) |
| Whisper indisponível | corrigido (H2) |
| Banco corrompido | sem recuperação automática (M6) |

O *jitter* existe (`util.backoff`, ±20%) e é o motivo de dois testes antigos terem ficado instáveis; os limites foram corrigidos na 2.0.

## 10. Pipeline

Gargalos e riscos encontrados:
- **Worker único (padrão):** uma montagem por vez, então canais simultâneos esperam na fila. A captura tem prioridade, o que reduz o risco de expirar o buffer. Sobra M3/M4.
- **Limite de concorrência:** respeitado para Whisper (trava) e montagem (número de workers), **exceto no editor** (M2).
- **Temporários:** limpos a cada 6 h; o `.mp4` é trocado de forma atômica.
- **Processos órfãos:** corrigido (H3).

## 11. IA

As instruções vão separadas dos dados. Os dados vêm delimitados e limpos, e os marcadores que imitam instrução são neutralizados. A resposta é validada campo a campo, links são removidos, há filtro de termos e no máximo 6 tentativas.

Riscos: o filtro de termos é simples (lista curta), e o tempo total por corte é alto (M4).

## 12. Vídeo

- A validação com ffprobe é confiável para o que promete: abre o arquivo, confere duração esperada, áudio, resolução 1080x1920 e fps.
- Um arquivo pela metade nunca vira o `.mp4` final, porque é montado num temporário e só então trocado.
- O corte adaptativo respeita os limites de duração (teste e live real). Sem fala detectada, usa a janela fixa.
- A re-renderização guarda o `_original.mp4` e a fonte.

## 13. Publicação (classificação)

| Rede | Código | Simulação | Validação parcial | Validação real |
|---|---|---|---|---|
| YouTube | completo, revisado | testado | upload pelo script de 18/11 (outro código) funcionou; o painel 2.0 nunca enviou | **não** → BLOCKED (OAuth do usuário) |
| TikTok | completo, revisado | testado | — | **não** → BLOCKED (app e auditoria) |
| Facebook | completo, revisado | testado | — | **não** → BLOCKED (app e Página) |

O sistema diferencia o fim do upload ("processando") da publicação efetiva ("postado", só depois da verificação na rede). Upload parcial não se perde: vira incerto ou erro.

## 14. Agendador

- **Respeita as configurações:** frequência, janela, dias, modo, autorização, nota, validade, ordenação e pausas, com testes.
- **Depois de reiniciar** não publica nada inesperado: pausas e envios incertos persistem (teste novo).
- **Config 1.0 com "postar automaticamente":** vira modo automático, mas **nada é publicado** até você registrar as autorizações. É uma mudança segura de comportamento e está documentada.

## 15. Instalador

- **Montagem:** o run #6 corresponde ao commit 8c59dc7 (`head_sha` confere).
- **Pacote:** inclui ffmpeg, ffprobe, streamlink, Whisper e Gemini. O modelo do Whisper é baixado no primeiro uso.
- **Atualização sobre a 1.0:** preserva os dados. Foi testada com dados reais da 1.0 no Linux; no Windows real, falta confirmar.
- **Backup:** restaurável (teste).
- **Rollback:** não há rollback automático, só o backup (I5).
- **Atalhos e início com o Windows:** definidos no `.iss`, não testados num Windows real.
- **Encerramento:** o botão Encerrar e a desinstalação (`taskkill /T`) fecham tudo. A morte forçada agora também fecha (H3).

## 16. Testes adicionais

`tests/test_auditoria.py` tem **15 testes**:
- **11 passam e 4 são xfail estritos.** Os xfail documentam M1, M2, M3 e L1: se alguém corrigir um deles, o teste passa a falhar e precisa virar teste normal.
- **4 desses 11 falhavam antes das correções:** H1, H2, H4 e o teste de órfãos, que era H3 e foi demonstrado com `orfao.py`.
- **Total:** 79 passam e 4 são xfail. Rodei 3 vezes seguidas no Linux.
- **Windows (run #8, commit `2347cc5`):** no log do step de testes, **79 "." e 4 "x", sem F/E/s**. Isso inclui
  `test_processo_de_gravacao_morre_junto_com_o_programa`, que valida o Job Object `KILL_ON_JOB_CLOSE` num Windows real.
  O exe abriu o painel, o instalador foi montado e o step de publicação foi pulado (sem release).
- **Asserts antigos:** nenhum foi removido ou afrouxado.
- **Simulações:** os testes de rede continuam simulados e estão declarados assim.

## 17. Riscos

1. **Publicação real nunca foi validada:** é o maior risco.
2. **Fila longa** (M3/M4) com 5 canais ativos e Whisper em CPU.
3. **Disco baixo durante a live** (M1).
4. **Ordenação distorcida** pela confiança (M5) no modo automático.

## 18. Recomendações (prioridade)

1. Validar as 3 redes pelo roteiro `VALIDACAO_REAL.md`, em **modo semiautomático**.
2. Corrigir M3, M4 e M1 antes de usar 3 a 5 canais ao mesmo tempo.
3. Corrigir M2 e L1 (editor).
4. Corrigir M5: ordenar considerando a confiança.
5. Corrigir M6, M7 e M8.
6. Pedir uma revisão humana externa do código de publicação e de segurança.

> **Atualização (19/11/2026):** M1–M8, L1, L2, L4–L7 e I5 foram corrigidos com teste comportamental na rodada
> CORRECAO_AUDITORIA_FINAL.md; L3 segue aberto. Este documento fica como registro do estado em `5dc84d7`.

## 19. Veredito

**PASS WITH WARNINGS.**

- **Base local:** depois das 4 correções ALTAS, gravação, corte, legenda, nota, biblioteca, editor, recuperação e segurança do painel são **sustentados por código, testes e execução real**.
- **Publicação:** **implementada e testada só com simulação**. Nenhuma rede foi validada de verdade, por isso o automático **não** está liberado.
- **Uso recomendado até a validação real:** modo **semiautomático**, conferindo cada envio.
