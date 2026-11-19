# Recuperação

| Situação | Comportamento | Teste |
|---|---|---|
| Programa fechado à força durante a gravação | streamlink, ffmpeg e **netos** morrem junto (Job Object no Windows, `PDEATHSIG` + grupo de processos no Linux) | `test_processo_de_gravacao_morre_junto_com_o_programa` |
| Gravação parada (disco baixo, canal removido) | a árvore inteira é encerrada (`taskkill /T` / `killpg`) — antes o ffmpeg-neto ficava vivo | `test_disco_baixo_interrompe_gravacao_em_andamento` |
| Queda no meio de uma etapa | a etapa volta para o estado anterior e é refeita; textos já gerados/editados **não** são sobrescritos pela IA | `test_editor_nao_perde_textos_editados_se_cair`, testes de pipeline |
| Queda depois de publicar, antes de gravar | postagem fica **incerto**; não é reenviada | `test_banco_falha_depois_da_confirmacao_vira_incerto` |
| Editor falha ao remontar | estado e vídeo anteriores mantidos | `test_editor_falha_mantem_estado_e_video` |
| Cofre DPAPI ilegível/indisponível | modo degradado, nada apagado | `test_falha_no_cofre_durante_migracao_nao_perde_chaves`, `test_cofre_ilegivel_nao_e_sobrescrito` |
| **Banco corrompido** | o banco é aberto **só leitura** e conferido (`PRAGMA quick_check`) antes de qualquer escrita; se falhar, o painel abre em **modo de recuperação**: lista os backups (`backups/*.db` e `atualizacao-*/cortesex.db`), e restaurar move o banco danificado (+ `-wal`/`-shm`) para `*.danificado-<data>` e copia o backup por arquivo temporário. Nada é apagado. A página de recuperação tem o mesmo controle de Host e token. | `test_banco_corrompido_nao_e_sobrescrito`, `test_restaurar_guarda_o_danificado_e_copia_o_backup`, `test_pagina_de_recuperacao_protegida` |

Restauração manual (se preferir): feche o programa, renomeie `cortesex.db` e copie um backup no lugar.
