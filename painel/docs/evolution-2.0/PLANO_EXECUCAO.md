# Plano de execução (ordem mais segura)

1. Fase 1 — pipeline persistente: migração do banco com backup; cortes passam a ser linhas com estado desde a detecção; workers que retomam do último checkpoint; validação com ffprobe; postagem idempotente (estado “incerto”); pausas persistentes com backoff; supervisor do gravador; limites de concorrência.
2. Fases 2/3/4/10 — fonte do corte com janela larga guardada → transcrição com tempo de palavra → corte adaptativo (início/fim em pausas) → legendas com segmentação adaptativa, 3 estilos e área segura → Gemini estruturado (análise + textos, validação, proteção contra injeção) → score editorial 0–100 com pesos versionados e critérios “desconhecidos”.
3. Fase 5 — segredos fora do `config.json` (DPAPI no Windows), validação de Host/Origin, cabeçalhos, redação de logs, caminhos seguros.
4. Fases 6/8/11 — autorização por canal (gate de publicação), modos manual/semi/automático, agendador por score configurável, pausas com retomada.
5. Fase 7 — classificação de erros, status pós-envio (TikTok/Facebook/YouTube), roteiro de validação real.
6. Fases 12/13/15/16 — analytics (YouTube oficial; o resto declarado indisponível), diagnóstico, retenção segura, backup antes de atualizar, ffprobe no instalador.
7. Fase 14 — interface reorganizada sobre o visual atual.
8. Fases 17/18 — testes, documentação, relatório final.
