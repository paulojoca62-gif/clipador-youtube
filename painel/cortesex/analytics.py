"""Relatórios de desempenho a partir das métricas coletadas pelas APIs oficiais.

Disponibilidade real (19/11/2026):
- YouTube: visualizações, curtidas e comentários pela YouTube Data API (escopo youtube.readonly, já pedido
  no login). Retenção/impressões exigem a YouTube Analytics API (não integrada).
- TikTok: exige o escopo video.list (não pedido) → indisponível.
- Facebook: insights de Reels exigem read_insights e revisão do app → indisponível.
Nenhum número é estimado: o que não foi coletado aparece como indisponível.
"""

from collections import defaultdict
from datetime import datetime

from cortesex.editorial import NOMES_CATEGORIAS

DISPONIBILIDADE = {
    "youtube": "visualizações, curtidas e comentários (API oficial); retenção não integrada",
    "tiktok": "indisponível: exige o escopo video.list e app auditado",
    "facebook": "indisponível: exige read_insights e revisão do app",
}


def _faixa_duracao(d):
    if not d:
        return "desconhecida"
    return "até 30 s" if d <= 30 else "31–60 s" if d <= 60 else "61–90 s" if d <= 90 else "mais de 90 s"


def _faixa_score(s):
    if s is None:
        return "sem nota"
    return "0–39" if s < 40 else "40–59" if s < 60 else "60–79" if s < 80 else "80–100"


def relatorio(banco, minimo_amostras=3) -> dict:
    linhas = banco.metricas_ultimas()
    grupos = {"categoria": defaultdict(list), "canal": defaultdict(list), "duracao": defaultdict(list),
              "hora_postagem": defaultdict(list), "nota_editorial": defaultdict(list)}
    for r in linhas:
        if r["views"] is None:
            continue
        grupos["categoria"][NOMES_CATEGORIAS.get(r["categoria"] or "outros", r["categoria"] or "outros")].append(r)
        grupos["canal"][r["canal"]].append(r)
        grupos["duracao"][_faixa_duracao(r["duracao"])].append(r)
        grupos["hora_postagem"][f"{datetime.fromtimestamp(r['postado_em']).hour:02d}h"].append(r)
        grupos["nota_editorial"][_faixa_score(r["score_editorial"])].append(r)

    def resumo(itens):
        views = [i["views"] for i in itens]
        return {"videos": len(itens), "media_views": round(sum(views) / len(views)) if views else 0,
                "media_likes": round(sum(i["likes"] or 0 for i in itens) / len(itens), 1) if itens else 0,
                "amostra_suficiente": len(itens) >= minimo_amostras}

    tabelas = {g: sorted(({"grupo": k, **resumo(v)} for k, v in d.items()), key=lambda x: -x["media_views"])
               for g, d in grupos.items()}
    recomendacoes = []
    for g, nome in (("categoria", "categoria"), ("duracao", "duração"), ("hora_postagem", "horário")):
        boas = [t for t in tabelas[g] if t["amostra_suficiente"]]
        if len(boas) >= 2:
            recomendacoes.append(f"Melhor {nome} até agora: {boas[0]['grupo']} "
                                 f"(média {boas[0]['media_views']} views em {boas[0]['videos']} vídeos).")
    if not recomendacoes:
        recomendacoes.append(f"Ainda não há dados suficientes (mínimo {minimo_amostras} vídeos por grupo) "
                             "para recomendar mudanças. Nada é ajustado automaticamente.")
    top = sorted((r for r in linhas if r["views"] is not None), key=lambda r: -r["views"])[:10]
    return {"disponibilidade": DISPONIBILIDADE, "total_videos_medidos": len([r for r in linhas if r["views"] is not None]),
            "tabelas": tabelas, "recomendacoes": recomendacoes, "top": top}
