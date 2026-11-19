# Registro de implementação — CortesEX-PRESS 2.0

| Fase | O que foi feito | Arquivos | Gate |
|---|---|---|---|
| 0 Auditoria | baseline (12 testes ok, CI ok), arquitetura, inventário, riscos | `docs/evolution-2.0/*` | PASS |
| 1 Pipeline | estados persistentes desde a detecção; histórico por corte; workers que reivindicam etapas no banco; retomada do último checkpoint ao reabrir; backoff por etapa; falha definitiva x temporária; validação ffprobe; troca atômica do .mp4; timeout em todo ffmpeg; supervisor da gravação (travamento > 45 s, ffmpeg morto, backoff 30 s→5 min); limite de workers | `db.py`, `motor.py`, `render.py`, `midia.py`, `gravador.py`, `util.py` | PASS |
| 2 Inteligência | score editorial 0–100 com pesos versionados e configuráveis; critérios sem dado = desconhecidos (não somam, reduzem a confiança); sinais de áudio locais; avisos de qualidade; categorias | `editorial.py` | PASS |
| 3 Corte adaptativo | fonte com janela larga (até 60 s antes / 30 s depois); início/fim em frases e pausas; mínimo/máximo configuráveis; agrupamento de picos do mesmo evento ainda não capturado | `editorial.py`, `motor.py` | PASS WITH WARNINGS (frases dependem da transcrição; sem fala usa janela fixa) |
| 4 Legendas | segmentação adaptativa (pontuação, pausa, tamanho, duração); estilos clássico/impacto/narrativo; cor, fonte, tamanho, contorno, posição, margens, animação; área segura 9:16; editor no painel (texto, tempos, início/fim do corte); re-render a partir da fonte guardando o original | `legendas.py`, `motor.py`, `web/index.html` | PASS |
| 5 Segurança | cofre de chaves (DPAPI no Windows) com migração verificada; validação de Host (DNS rebinding) e Origin; cabeçalhos (CSP, X-Frame-Options, nosniff, Referrer-Policy); redação de segredos em logs/erros/eventos/Telegram; caminhos de vídeo restritos à pasta de dados; limites de corpo; métodos bloqueados; prompt injection: instruções no system, dados delimitados e saneados, resposta validada | `seguranca.py`, `segredos.py`, `config.py`, `servidor.py`, `ia.py`, `app.py` | PASS |
| 6 Autorização | cadastro por canal (status, datas, validade, observações, gravar/editar/publicar, redes); gate de publicação no agendador e no manual (manual exige confirmação explícita) | `config.py`, `motor.py`, `servidor.py`, UI | PASS |
| 7 Publicação | erros tipados (temporário, autenticação, permissão, cota, privacidade, mídia, processamento, conta, permanente); pausas persistentes por tipo; status pós-envio (YouTube, TikTok, Facebook) com verificador; estado por rede; idempotência (nunca repete na mesma rede sem “forçar”); envio interrompido = “incerto” (usuário confirma) | `plataformas/*`, `motor.py`, `db.py` | PASS WITH WARNINGS — **validação real pendente (BLOCKED por contas)** |
| 8 Modos | manual / semiautomático (padrão de instalações novas) / automático; migração preserva quem já postava sozinho | `config.py`, `motor.py` | PASS |
| 9 Biblioteca | filtros (estado, canal, categoria, período, ordenação), notas, histórico, aprovar/desaprovar, reanalisar, reprocessar, editar, descartar com motivo, recuperar | `db.py`, `servidor.py`, UI | PASS |
| 10 IA | análise + textos numa chamada JSON; categorias, notas 0–10, riscos, resumo, variante TikTok; validação de campos/hashtags/links; filtro de termos; fallback limitado (6 tentativas), Retry-After, 404, resposta inválida | `ia.py` | PASS |
| 11 Agendador | modos, ordenação configurável (editorial/pico/recente), nota editorial mínima, evitar mesmo canal seguido, pausas visíveis com “Retomar” | `motor.py`, UI | PASS |
| 12 Analytics | snapshots de métricas (tabela própria); coleta oficial do YouTube; relatórios por categoria/duração/nota/canal/horário; recomendações só informativas com amostra mínima; TikTok/Facebook declarados indisponíveis | `analytics.py`, `motor.py` | PASS WITH WARNINGS (só YouTube tem dados) |
| 13 Diagnóstico | CPU/carga, RAM, disco, uso das pastas, ffmpeg/ffprobe/streamlink/Whisper, modelo carregado, integridade do banco, workers (batimento), gravações, saúde por serviço, erros recentes | `diagnostico.py`, UI | PASS |
| 14 Interface | 10 abas (Dashboard, Monitoramento, Cortes, Editor, Fila, Publicações, Analytics, Canais, Configurações, Diagnóstico) sobre o visual 1.0; confirmações em ações destrutivas/automáticas | `web/index.html` | PASS |
| 15 Armazenamento | retenção de vídeos e fontes; proteção de aprovados, envios em andamento e (opcional) publicados; marca `arquivo_removido`; limpeza de temporários; disco baixo pausa a gravação | `motor.py` | PASS |
| 16 Instalador | backup automático (config sem chaves, cofre, banco) ao mudar de versão, guarda 5; ffprobe no pacote; testes rodam no Windows antes de empacotar; release público só manual | `atualizacao.py`, workflow | PASS WITH WARNINGS (instalação real no Windows do usuário a confirmar) |
| 17 Testes | 65 testes (12 da 1.x mantidos; 2 ajustados às regras novas) | `tests/*` | PASS |
| 18 Docs | este diretório + `LEIA-ME.md` | | PASS |

## Testes antigos ajustados (justificativa)
- `test_agendador_posta_o_melhor_e_respeita_regras`: agora configura `modo=automatico`, autoriza o canal e ordena por pico. Sem isso, a regra nova (autorização obrigatória) bloqueia — que é o comportamento pedido. Os asserts originais foram mantidos.
- `test_limite_da_plataforma_pausa_e_registra`: as pausas saíram da memória para o banco (`banco.pausa`) e agora têm tipo; o assert ficou mais forte (confere tipo e duração).
- `test_completa_textos_que_faltaram`: o motor chama `ia.analisar_e_escrever`; o mock mudou de alvo e o teste passou a conferir também a categoria.
