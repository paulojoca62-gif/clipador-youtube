# Validação de segurança do painel (rodada de correção final)

Escopo: o painel HTTP local (`cortesex/servidor.py`, 127.0.0.1:8777), a página `web/index.html`, o cofre de
chaves e a redação de logs. Tudo abaixo foi **reproduzido com teste comportamental** (requisição HTTP real,
socket cru ou navegador Chromium real); nada foi aceito só por leitura de código.

| Controle | Como é feito | Teste que prova |
|---|---|---|
| Host | exatamente **um** cabeçalho Host; só `127.0.0.1:PORTA` ou `localhost:PORTA` (maiúsc./minúsc. indiferente); máx. 64 caracteres; só `[A-Za-z0-9.:-]` | `test_host_recusado` (13 variantes: evil.com, `127.1`, `0.0.0.0`, `[::1]`, `2130706433`, porta errada, sem porta, vazio, `user@`, espaço, porta com zero à esquerda, ponto final), `test_host_duplicado_recusado` (socket cru) |
| CSRF | token aleatório por execução (`secrets.token_urlsafe(32)`) inserido na página; todo POST exige `X-CortesEX` igual (`compare_digest`); `Origin` (se vier) tem que ser o próprio painel; `Sec-Fetch-Site` só `same-origin`/`none` | `test_csrf_token_obrigatorio_e_cross_site`, `test_seguranca.py` (token antigo "1" → 403) |
| Métodos | só GET e POST; PUT, DELETE, PATCH, OPTIONS, HEAD, TRACE, CONNECT → 405; ações só por POST | `test_corpo_e_metodos` |
| Corpo | `Content-Length` obrigatório, inteiro, ≥ 0, ≤ 2 MB (400/413 e fecha a conexão); `Transfer-Encoding` → 411; só `application/json`, só objeto JSON (415/400); timeout de socket 30 s | `test_corpo_e_metodos` (socket cru: -5, `abc`, 99 999 999, chunked) |
| Cabeçalhos | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (câmera, microfone, geolocalização, pagamento desligados), `Cache-Control: no-store` | `test_corpo_e_metodos`, `test_seguranca.py` |
| XSS | a página monta tudo com escape (`esc`) e `textContent`; links externos só se forem `https://` (`urlSegura`); "Clipar agora" usa `data-canal`, não string JS | `test_xss_nao_executa_no_painel`: Chromium real, 6 payloads em título, legenda, hashtags, fala, chat, erro, análise, categoria, eventos e URL `javascript:` → nenhum script rodou, nenhum diálogo, nenhum link `javascript:`, o texto aparece escapado |
| Caminhos | `/video/<id>` só aceita id numérico; o arquivo tem que estar **resolvido** dentro da pasta de dados (symlink para fora é recusado) | `test_symlink_para_fora_nao_e_servido`, variantes `..`, `%2F`, `-1`, `1.mp4` |
| Segredos | cofre DPAPI no Windows (assinaturas ctypes explícitas para 64 bits, `CRYPTPROTECT_UI_FORBIDDEN`, `LocalFree`); a API só devolve se a chave existe | `test_seguranca.py`, CI Windows |
| Cofre que falha | **modo degradado**: as chaves continuam no config.json, nada é apagado, o painel avisa | `test_falha_no_cofre_durante_migracao_nao_perde_chaves`, `test_cofre_ilegivel_nao_e_sobrescrito` |
| Redação | tokens Google (`ya29.`, `1//`, `4/0`, `GOCSPX-`, `AIza`), JWT, Telegram `/bot…`, Meta `EAA…`, TikTok `act.`/`rft.`, `sig`/`token` da Twitch, cabeçalhos `Authorization`, `x-goog-api-key`, `x-api-key`, campos JSON e de formulário; + todo segredo cadastrado é registrado e trocado por `***` | `test_redacao_de_formatos_reais` (10 strings realistas) |
| Logs antigos | a tabela `eventos` gravada pela 1.x é redigida uma vez na migração | `test_eventos_antigos_sao_redigidos_na_migracao` |
| Backups | fora do Windows, cópias do cofre ficam com permissão 600 e a pasta 700 | `test_backup_da_atualizacao_so_o_dono_le` |

## Exceções aceitas (justificadas)

1. **CSP com `'unsafe-inline'` em script e estilo.** A página é um arquivo único com script e handlers
   `onclick` embutidos. Trocar por nonce exigiria reescrever toda a interface (fora do escopo de "não
   recriar funcionalidades"). Mitigação: todo dado externo passa por `esc()`/`textContent`, URLs são
   filtradas, `connect-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'` e o teste
   de XSS em navegador real passa. **ACEITO COMO RISCO** (baixo).
2. **Outros usuários do mesmo Windows** (I1). O token CSRF impede que *sites* comandem o painel, mas
   qualquer processo local pode ler a página e o token. O painel não tem senha. **ACEITO COMO RISCO**
   para uso em computador pessoal; documentado no GUIA_OPERACAO.
3. **SQLite com conexão única** compartilhada entre threads sob `RLock`, com WAL. As operações críticas
   (reserva de postagem, reivindicação de etapa) são atômicas **no próprio banco** (índice único parcial
   e `UPDATE … WHERE estado=?`), então valem também entre processos — provado com 4 processos reais.
