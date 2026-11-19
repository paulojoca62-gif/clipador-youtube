# CortesEX-PRESS 2.0 — Relatório final

Data: 19/11/2026 · Branch `claude/cortesex-painel` · Commit da 2.0: `8c59dc7` · Pasta `painel/`

## 1. Resultado

**STATUS GERAL: PASS WITH WARNINGS**

O que roda no seu computador (gravação, detecção, corte, legenda, IA, nota, biblioteca, editor, fila,
agendador, segurança, diagnóstico, instalador) está implementado e testado, inclusive numa live real e
num Windows real do GitHub. Falta **uma coisa crítica que só você pode fazer**: validar a publicação real
em YouTube, TikTok e Facebook com as suas contas. Por isso não é PASS.

## 2. Baseline (antes da 2.0)

| Item | Resultado |
|---|---|
| Testes | 12 passando, todos com simulação |
| Build Windows | passando (5 execuções) |
| Problemas que já existiam | painel aceitava DNS rebinding (crítico); chaves em texto puro no `config.json`; logs sem esconder segredos; fila de cortes só na memória (fechar perdia picos); envio interrompido podia ser repostado; nenhum vídeo validado antes de postar; pausa fixa de 1 h e esquecida ao reiniciar; streamlink travado não era detectado; layout "câmera em cima" não funcionava; limpeza deixava links quebrados |

## 3. O que foi implementado

**Confiabilidade**
- Cada corte é registrado no banco **no momento do pico**, com estado: detectado → capturando → capturado → transcrevendo → transcrito → montando → montado → gerando textos → aguardando revisão / aprovado (ou falhou / descartado). Tudo tem histórico com data.
- **Retomada após queda:** ao reabrir, cada corte volta para o último ponto concluído, sem capturar ou transcrever de novo.
- **Novas tentativas com espera crescente** em cada etapa. Uma falha definitiva (gravação expirada) não fica tentando para sempre.
- **Todo vídeo é validado com o ffprobe** antes de ficar pronto e antes de ir para a rede: existe, não está vazio, abre, duração, áudio, vídeo, 1080x1920, fps.
- **Proteção do arquivo:** o .mp4 só é trocado quando está completo, e todo ffmpeg tem tempo máximo.
- **Gravação vigiada:** se o vídeo para de chegar por 45 s ou o ffmpeg morre, a gravação reinicia sozinha, com espera de 30 s a 5 min quando falha seguido.
- **Quantos cortes ao mesmo tempo:** configurável, com padrão 1 para computadores comuns.

**Qualidade editorial**
- **Corte adaptativo:** começa e termina em frases e pausas da fala, entre 20 e 90 s (configurável), com até 60 s de contexto antes do pico.
- **Picos agrupados:** picos do mesmo momento viram um corte só.
- **Nota editorial de 0 a 100**, com 7 critérios e pesos configuráveis e versionados. Critério sem dado não entra na conta, e a **confiança** aparece ao lado da nota.
- **Avisos de qualidade:** silêncio, áudio estourado ou baixo, pouca fala, começo no meio da fala, fim abrupto.
- **Gemini em uma chamada só:** categoria (engraçado, reação, gameplay e outras), notas, riscos, resumo, título, legenda, hashtags e versão para o TikTok, tudo validado. Remove links, filtra termos ofensivos, não repete sem limite e respeita o pedido de espera da API.

**Legendas**
- **Divisão das frases** por pontuação, pausa, tamanho e duração, em vez de 3 palavras fixas.
- **3 estilos:** Clássico, Impacto (palavra da vez destacada) e Narrativo (frases, com caixa de fundo).
- **Personalização:** fonte, tamanho, cores, contorno, posição, margens, letras por linha e animação.
- **Área segura:** a legenda não fica embaixo dos botões das redes.
- **Editor no painel:** você corrige o texto, os tempos e o começo e fim do corte, e o vídeo é montado de novo a partir da gravação guardada, sem repetir a captura nem a IA. O vídeo original fica guardado.

**Segurança**
- **Chaves num cofre** cifrado com a proteção de dados do Windows (DPAPI). As chaves da 1.0 são migradas sozinhas, e conferidas antes de saírem do `config.json`.
- **Painel bloqueia** outros sites e DNS rebinding (checa Host e Origin), envia cabeçalhos de proteção e só serve vídeos de dentro da pasta de dados.
- **Logs, erros e avisos** escondem tokens e chaves.
- **Proteção contra instruções escondidas no chat ou na fala:** elas não viram instrução para a IA.

**Autorização e publicação**
- **Autorização por canal:** status, datas, validade, observações, se pode gravar, editar ou publicar, e em quais redes. Sem autorização registrada, nada é publicado sozinho. Para postar à mão, o painel pede confirmação.
- **3 modos:** Manual, Semiautomático (o padrão em instalações novas) e Automático. Quem já postava sozinho na 1.0 continua no automático.
- **Erros separados por tipo:** temporário, login, permissão, cota, privacidade, mídia e outros. Cada rede pausa do jeito certo e tem botão **Retomar**.
- **Status depois do envio:** o programa confere na rede se o vídeo terminou de processar (YouTube, TikTok e Facebook).
- **Nunca posta o mesmo corte duas vezes na mesma rede**, a não ser que você peça. Um envio interrompido vira **incerto** e espera você confirmar.

**Painel**
- **10 abas:** Dashboard, Monitoramento, Cortes (biblioteca com filtros), Editor, Fila, Publicações, Analytics, Canais, Configurações e Diagnóstico.
- **Analytics:** visualizações, curtidas e comentários do YouTube pela API oficial. TikTok e Facebook aparecem como indisponíveis, sem números inventados. As recomendações só informam, nada muda sozinho.
- **Diagnóstico:** CPU, memória, disco, dependências, banco, processos, gravações, saúde de cada conta e erros recentes.
- **Limpeza:** a retenção protege cortes aprovados e envios em andamento. Com pouco disco, a gravação pausa.

**Instalador**
- **Backup automático** da configuração (sem as chaves), do cofre e do banco ao mudar de versão, guardando os 5 últimos.
- **ffprobe incluído** no pacote.
- **Testes no Windows antes de empacotar.**
- **Publicação manual:** o instalador só vai para o release público quando você manda, em vez de a cada envio de código.

## 4. Arquitetura

O programa continua em um processo, sem framework web e com o mesmo visual. Mudou de duas formas:
- **A fila de cortes saiu da memória e foi para o banco.** Workers pegam a próxima etapa de cada corte direto no banco, o que permite retomar depois de uma queda.
- **Cada etapa tem arquivos próprios guardados no disco:** a gravação larga (fonte), as palavras com o tempo de cada uma, e as legendas. É isso que deixa remontar o vídeo e retomar sem refazer o trabalho.

Os módulos novos são `editorial`, `legendas`, `midia`, `seguranca`, `segredos`, `diagnostico`, `analytics` e `atualizacao`.

## 5. Banco

- **Estrutura:** 26 colunas novas em `cortes`, 3 em `postagens` e as tabelas novas `historico`, `pausas`, `metricas` e `meta`.
- **Como migra:** só acrescenta, nada é apagado. Antes de mudar, faz um **backup automático** em `backups/`.
- **Cortes da 1.0:** viram "aguardando revisão" e são validados com o ffprobe na primeira vez que o agendador olha para eles.
- **Teste com dados reais:** migrei uma pasta de dados real da 1.0, com 11 cortes e a chave do Gemini. Tudo foi preservado.

## 6. Segurança

| Problema | Correção | Limitação que continua |
|---|---|---|
| DNS rebinding (crítico) | Host, Origin e cabeçalho obrigatórios; testado com servidor real | — |
| Chaves em texto puro | Cofre DPAPI no Windows, testado no Windows do CI | Fora do Windows: arquivo com permissão 600, sem cifra |
| Segredos em logs | Redação em todos os caminhos | Depende dos padrões conhecidos e das chaves salvas |
| Prompt injection | Separação sistema/dados e validação da resposta | Nenhuma defesa é 100%; a resposta nunca é executada |
| Outros usuários do mesmo Windows | — | Podem abrir o painel em 127.0.0.1 (sem senha) |

## 7. Testes

| Onde | Quantidade | Resultado |
|---|---|---|
| Linux (aqui) | **65** | **65 passaram**, 5 rodadas seguidas sem falha |
| Windows (GitHub, run #6) | **65** | **passaram**, com ffmpeg real e o cofre DPAPI de verdade |
| Exe no Windows | — | abre o painel e o streamlink embutido responde |
| Live real (alanzoka, 19/11) | — | ver abaixo |

- **Os 12 testes da 1.0 continuam.** 3 foram ajustados às regras novas (autorização, pausas no banco, IA estruturada), e os asserts originais foram mantidos ou ficaram mais fortes. A justificativa está em `REGISTRO_IMPLEMENTACAO.md`.
- **Dois testes falhavam às vezes, por erro do próprio teste:** não contavam com a variação aleatória da espera. Foram corrigidos sem afrouxar a regra.
- **Os testes nunca acessam a internet.** Um bloqueio automático derruba qualquer tentativa.
- **Teste real com a live do alanzoka:**
  - corte adaptativo de 31,6 s, começando e terminando em frases;
  - validado em 1080x1920, 30 fps, com áudio;
  - nota editorial 81, com confiança de 80%;
  - categoria "engraçado", título, legenda e hashtags do Gemini;
  - editor remontou o vídeo (versão 2) e guardou o original.

Os testes cobrem estes temas (simulações aparecem como simulações):

| Tema | Tipo de teste |
|---|---|
| Pipeline e retomada depois de queda | ffmpeg real |
| Nova tentativa com espera crescente | real |
| Agrupamento de picos | real |
| Idempotência, envio incerto, modos, autorização, pausas por tipo, verificação depois do envio | redes simuladas |
| Validação de vídeo e migração do banco | real |
| Cofre e segurança do servidor | real |
| Legendas, nota editorial, IA, diagnóstico, analytics, limpeza e disco | real |

## 8. Redes

| Rede | Código | Teste simulado | Teste real | Situação |
|---|---|---|---|---|
| YouTube | completo: login que se renova, envio retomável, status, privacidade, métricas | passa | **não**: travou no cadastro do OAuth (`redirect_uri_mismatch`); o envio antigo, pelo token do Playground, funcionou em 18/11 | **validação pendente** |
| TikTok | completo: PKCE, Direct Post, status `PUBLISH_COMPLETE` | passa | **não**: app em cadastro | **BLOCKED** (app e auditoria) |
| Facebook Reels | completo: Página, envio, status | passa | **não**: app e Página não configurados | **BLOCKED** (configuração) |

## 9. O que depende de você

1. **YouTube:** no Google Cloud, crie um ID do cliente do tipo **"App para computador"**, ou adicione `http://127.0.0.1:8777/` ao "Cliente Web 1". Depois, Conectar canal → Testar → postar 1 corte de teste como **Privado**.
2. **TikTok:** termine o app (Login Kit com o Redirect URI `http://127.0.0.1:8777/`, Content Posting API com Direct Post, Sandbox com a sua conta privada) e faça 1 envio de teste. Para postar público: auditoria do app.
3. **Facebook:** crie o app Empresa e escolha a Página no painel.
4. **Autorização:** registre a autorização de cada canal na aba Canais. Sem isso, o agendador não publica.
5. **Validação:** siga o roteiro `VALIDACAO_REAL.md` e anote os resultados.
6. **Auditorias:** peça a auditoria da API do YouTube e a do app do TikTok, para os vídeos saírem públicos.
7. **Publicar o instalador**, quando quiser liberar a 2.0: Actions → "CortesEX-PRESS (Windows)" → Run workflow → **publicar = sim**.

## 10. Instalação

- **Instalador 2.0:** montado e testado no Windows do GitHub. Está nos **artefatos** da execução #6 (precisa estar logado no GitHub). Ainda **não foi para o release público**, porque isso agora é manual (item 7 acima).
- **Atualizar por cima da 1.0:** mantém configurações, cortes e chaves, e faz o backup automático.
- **Riscos:**
  - não foi testado no seu Windows (antivírus, firewall);
  - o Windows avisa porque o instalador não tem assinatura digital;
  - o primeiro corte baixa o Whisper (~500 MB);
  - o arquivo da execução #6 se chama "1.0.6" por engano no número de versão. Já corrigi para as próximas ("2.0.x"); o conteúdo é a 2.0.

## 11. Próximas ações

1. Validar a publicação real nas 3 redes (item 9).
2. Rodar a 2.0 no seu Windows por 1 dia, em modo semiautomático, e me mandar o Diagnóstico e o `cortesex.log` se algo der errado.
3. Depois de alguns vídeos publicados, olhar o Analytics. As recomendações só ficam confiáveis com pelo menos 3 vídeos por grupo.

## 12. Veredito técnico

- **Pronto para uso em semiautomático:** a parte local está sólida. Grava, sobrevive a quedas, faz cortes melhores (começo e fim nas frases, nota, legenda editável) e está bem mais segura que a 1.0.
- **Não está pronto para produção automática:** a publicação real nunca foi confirmada com as suas contas, e TikTok e YouTube ainda dependem de auditoria para postar público. Até a validação real, use o modo **semiautomático** e confira cada publicação na aba Publicações.
- **Limites que continuam:**
  - só Twitch como fonte (Kick e YouTube ao vivo não estão no painel);
  - a transcrição do Whisper erra palavras (dá para corrigir no editor);
  - analytics só do YouTube;
  - o painel não tem senha para outros usuários do mesmo Windows.
