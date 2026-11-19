# Correções da auditoria — status de cada achado

Status: **CORRIGIDO** · **CONFIRMADO E ABERTO** · **NÃO REPRODUZIDO** · **NÃO CONFIRMADO** · **BLOQUEADO** · **ACEITO COMO RISCO**.
"CORRIGIDO" só quando existe teste comportamental que falhava antes ou que reproduz o cenário.

## Achados da auditoria independente

| ID | Achado | Status | Correção | Prova |
|---|---|---|---|---|
| AUD-H1 | publicação dupla em corrida | CORRIGIDO (reforçado) | além do `_lock_reserva`, índice único parcial no banco → vale entre processos | `test_corrida_nao_publica_duas_vezes`, `test_reserva_atomica_entre_dois_processos` |
| AUD-H2 | sem Whisper nenhum corte sai | CORRIGIDO (rodada anterior) | corte segue sem legenda | `test_falha_do_whisper_nao_impede_o_corte` |
| AUD-H3 | gravador órfão | CORRIGIDO (ampliado) | Job Object/PDEATHSIG + **grupo de processos e `matar_arvore`** (netos também) | `test_processo_de_gravacao_morre_junto_com_o_programa`, `test_disco_baixo_…` |
| AUD-H4 | queda vira erro e reposta | CORRIGIDO (ampliado) | também quando o banco falha depois da confirmação | testes H4, `test_banco_falha_depois_da_confirmacao_vira_incerto` |
| AUD-M1 | disco baixo não para gravação em andamento | CORRIGIDO | `pode_gravar()` dentro do laço | `test_disco_baixo_interrompe_gravacao_em_andamento` |
| AUD-M2 | editor remonta corte em processamento | CORRIGIDO | só PRONTOS, reivindica `→ montando`, semáforo de workers, restaura em falha | 3 testes do editor |
| AUD-M3 | limpeza apaga blocos de candidatos | CORRIGIDO | `protegido_desde` = menor `janela_ini` pendente (teto de 3 h) | `test_limpeza_do_buffer_protege_candidatos_pendentes` |
| AUD-M4 | IA bloqueia worker de mídia | CORRIGIDO | thread "ia" própria; 30 s por tentativa, 150 s no total | `test_worker_de_midia_nao_pega_etapa_da_ia`, `test_ia_respeita_orcamento_total_de_tempo` |
| AUD-M5 | nota parcial fura a fila | CORRIGIDO | ordena primeiro quem tem cobertura ≥ mínima; automático exige cobertura | `test_ranking_nao_deixa_nota_parcial_furar_a_fila` |
| AUD-M6 | banco corrompido sem restauração guiada | CORRIGIDO | abre só leitura + `quick_check`; modo de recuperação com lista de backups | 3 testes de recuperação |
| AUD-M7 | DPAPI falha → não abre | CORRIGIDO | modo degradado sem perda | `test_falha_no_cofre_durante_migracao_nao_perde_chaves` |
| AUD-M8 | verificação TikTok sem renovar token | CORRIGIDO | renova antes | `test_verificacao_do_tiktok_renova_token` |
| AUD-L1 | permissão "editar" não aplicada | CORRIGIDO | `remontar` recusa | `test_permissao_editar_e_aplicada` |
| AUD-L2 | agrupamento quase nunca age | CORRIGIDO | picos no intervalo mínimo estendem o corte aberto | `test_pico_durante_intervalo_minimo_estende_o_corte_aberto` |
| AUD-L3 | critério `inicio_fim` infla a nota | CONFIRMADO E ABERTO | mede o alinhamento que o próprio algoritmo escolheu (peso 10 de 100). Não alterado para não mudar a escala de notas já gravadas sem calibração com dados reais | — |
| AUD-L4 | reconexão do chat a 5 s fixos | CORRIGIDO | espera crescente 5→120 s, zera ao conectar | revisão + teste de backoff existente |
| AUD-L5 | backup do cofre em texto no Linux | CORRIGIDO | 600/700 | `test_backup_da_atualizacao_so_o_dono_le` |
| AUD-L6 | eventos antigos com segredos | CORRIGIDO | redação única na migração | `test_eventos_antigos_sao_redigidos_na_migracao` |
| AUD-L7 | Content-Length negativo prende thread | CORRIGIDO | 400 + fecha; timeout 30 s | `test_corpo_e_metodos` |
| AUD-I1 | outro usuário local abre o painel | ACEITO COMO RISCO | ver VALIDACAO_SEGURANCA | — |
| AUD-I2 | "confiança" = cobertura | ACEITO (documentado) | nome mantido; documentado como cobertura | — |
| AUD-I3 | YouTube não retoma upload parcial | ACEITO COMO RISCO | sem duplicata (incerto) | — |
| AUD-I4 | desinstalar mantém dados | ACEITO (esperado) | — | — |
| AUD-I5 | restauração manual | CORRIGIDO | modo de recuperação guiada (M6) | — |
| AUD-I6 | score técnico heurístico | ACEITO COMO RISCO | — | — |

## Achados novos desta rodada

| ID | Achado | Severidade | Status | Prova |
|---|---|---|---|---|
| COR-1 | Graph API v21.0 é removida em 21/01/2027 → Facebook pararia de postar | ALTA | CORRIGIDO (v26.0) | `test_versao_da_graph_api_nao_e_removida_em_2027` |
| COR-2 | cabeçalho CSRF era a constante "1": a proteção dependia só do preflight CORS do navegador; sem segredo, qualquer falha de CORS/extensão a anulava | BAIXA | CORRIGIDO (token por execução) | `test_csrf_token_obrigatorio_e_cross_site` |
| COR-3 | Host duplicado / variantes numéricas não testadas | MÉDIA | CORRIGIDO (exatamente um Host, validação estrita) | `test_host_recusado`, `test_host_duplicado_recusado` |
| COR-4 | `Transfer-Encoding`, métodos extras, corpo não-objeto | BAIXA | CORRIGIDO | `test_corpo_e_metodos` |
| COR-5 | parar a gravação deixava o **ffmpeg-neto** vivo | ALTA | CORRIGIDO (`matar_arvore`) | `test_disco_baixo_interrompe_gravacao_em_andamento` |
| COR-6 | reserva de postagem atômica só dentro do processo | MÉDIA | CORRIGIDO (índice único) | `test_reserva_atomica_entre_dois_processos` |
| COR-7 | IA podia sobrescrever textos editados após queda | MÉDIA | CORRIGIDO | `test_editor_nao_perde_textos_editados_se_cair` |
| COR-8 | nota editorial aceitava NaN, pesos negativos, valores > 1 | BAIXA | CORRIGIDO | `test_nota_com_valores_invalidos_e_extremos` |
| COR-9 | TikTok enviava arquivo grande numa parte só | MÉDIA | CORRIGIDO (partes conforme guia oficial) | `test_partes_do_tiktok_seguem_a_regra_oficial` |
| COR-10 | DPAPI via ctypes sem `argtypes` (ponteiros truncáveis em 64 bits) | MÉDIA | CORRIGIDO | CI Windows (`test_cofre_cifrado_no_windows`) |
| COR-11 | link de publicação com URL não-https era clicável | BAIXA | CORRIGIDO (`urlSegura`) | `test_xss_nao_executa_no_painel` |
| COR-12 | limites de 5/64 MB por parte do TikTok | — | NÃO CONFIRMADO (fonte oficial não explicita) | — |
| COR-13 | CSP com `unsafe-inline` | BAIXA | ACEITO COMO RISCO (justificado) | — |
| COR-14 | publicação real nas 3 redes | — | BLOQUEADO (sem autorização para publicar; TikTok ainda depende de aprovação do app) | — |
