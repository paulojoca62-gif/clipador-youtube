"""Banco SQLite: cortes (com estado do pipeline), histórico, postagens, pausas, métricas e eventos.

Migração: bancos da 1.x ganham as colunas e tabelas novas com ALTER TABLE (nada é apagado). Antes de
qualquer alteração de estrutura, o arquivo é copiado para backups/.
"""

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote

ESQUEMA = """
CREATE TABLE IF NOT EXISTS cortes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canal TEXT NOT NULL,
    arquivo TEXT NOT NULL UNIQUE,
    criado REAL NOT NULL,
    duracao REAL DEFAULT 0,
    score REAL DEFAULT 0,
    motivo TEXT DEFAULT '',
    chat TEXT DEFAULT '[]',
    fala TEXT DEFAULT '',
    titulo TEXT DEFAULT '',
    legenda TEXT DEFAULT '',
    hashtags TEXT DEFAULT '[]',
    descartado INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS postagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    corte_id INTEGER NOT NULL,
    plataforma TEXT NOT NULL,
    status TEXT NOT NULL,
    url TEXT DEFAULT '',
    erro TEXT DEFAULT '',
    quando REAL NOT NULL,
    origem TEXT DEFAULT 'agendador'
);
CREATE INDEX IF NOT EXISTS ix_post_corte ON postagens(corte_id, plataforma);
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quando REAL NOT NULL,
    nivel TEXT NOT NULL,
    texto TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    corte_id INTEGER NOT NULL,
    quando REAL NOT NULL,
    estado TEXT NOT NULL,
    detalhe TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_hist_corte ON historico(corte_id);
CREATE TABLE IF NOT EXISTS pausas (
    plataforma TEXT PRIMARY KEY,
    ate REAL NOT NULL,
    motivo TEXT DEFAULT '',
    tipo TEXT DEFAULT '',
    tentativas INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS metricas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    postagem_id INTEGER NOT NULL,
    quando REAL NOT NULL,
    views INTEGER, likes INTEGER, comentarios INTEGER, compartilhamentos INTEGER,
    extra TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_metricas_post ON metricas(postagem_id, quando);
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);
"""

# coluna -> definição (acrescentadas aos bancos da 1.x)
COLUNAS_CORTES = {
    "estado": "TEXT", "etapa_tentativas": "INTEGER DEFAULT 0", "erro": "TEXT DEFAULT ''",
    "proxima_acao": "TEXT DEFAULT ''", "atualizado": "REAL DEFAULT 0", "momento": "REAL DEFAULT 0",
    "janela_ini": "REAL DEFAULT 0", "janela_fim": "REAL DEFAULT 0", "fonte": "TEXT DEFAULT ''",
    "fonte_ini": "REAL DEFAULT 0", "corte_ini": "REAL DEFAULT 0", "corte_fim": "REAL DEFAULT 0",
    "layout": "TEXT DEFAULT 'blur'", "score_editorial": "REAL", "confianca": "REAL DEFAULT 0",
    "score_tecnico": "REAL", "criterios": "TEXT DEFAULT '{}'", "categoria": "TEXT DEFAULT ''",
    "flags": "TEXT DEFAULT '[]'", "analise": "TEXT DEFAULT '{}'", "validacao": "TEXT DEFAULT '{}'",
    "versao_render": "INTEGER DEFAULT 0", "arquivo_removido": "INTEGER DEFAULT 0",
    "aprovado_em": "REAL DEFAULT 0", "motivo_descarte": "TEXT DEFAULT ''", "textos_plataforma": "TEXT DEFAULT '{}'",
}
COLUNAS_POSTAGENS = {"ext_id": "TEXT DEFAULT ''", "tipo_erro": "TEXT DEFAULT ''", "detalhe": "TEXT DEFAULT ''",
                     "forcado": "INTEGER DEFAULT 0"}
JSON_CAMPOS = ("chat", "hashtags", "criterios", "flags", "analise", "validacao", "textos_plataforma")

# Estados do corte
DETECTADO, CAPTURANDO, CAPTURADO = "detectado", "capturando", "capturado"
TRANSCREVENDO, TRANSCRITO, MONTANDO, MONTADO = "transcrevendo", "transcrito", "montando", "montado"
GERANDO_TEXTOS, REVISAO, APROVADO = "gerando_textos", "aguardando_revisao", "aprovado"
FALHOU, DESCARTADO = "falhou", "descartado"
EM_ANDAMENTO = {CAPTURANDO: DETECTADO, TRANSCREVENDO: CAPTURADO, MONTANDO: TRANSCRITO, GERANDO_TEXTOS: MONTADO}
PRONTOS = (REVISAO, APROVADO)
# Postagem: enviando -> processando -> postado | erro | incerto (fechou no meio; precisa conferência humana)
OCUPA_REDE = ("enviando", "processando", "postado", "incerto")


class BancoCorrompido(RuntimeError):
    """O arquivo do banco não abre ou falhou na verificação de integridade. Nada foi alterado nele."""

    def __init__(self, arquivo, detalhe, backups):
        super().__init__(f"O banco {arquivo} está danificado ({detalhe}).")
        self.arquivo, self.detalhe, self.backups = Path(arquivo), detalhe, backups


class PostagemDuplicada(RuntimeError):
    pass


def backups_do_banco(pasta_backup: Path) -> list:
    """Cópias do banco disponíveis para restaurar, da mais nova para a mais velha."""
    pasta_backup = Path(pasta_backup)
    achados = list(pasta_backup.glob("*.db")) + list(pasta_backup.glob("atualizacao-*/cortesex.db"))
    return sorted(achados, key=lambda p: p.stat().st_mtime, reverse=True)


class Banco:
    def __init__(self, arquivo: Path, pasta_backup: Path | None = None):
        self.arquivo = Path(arquivo)
        self._lock = threading.RLock()
        self.migracoes: list[str] = []
        self._migrar(pasta_backup or self.arquivo.parent / "backups")

    def _migrar(self, pasta_backup: Path):
        existia = self.arquivo.exists() and self.arquivo.stat().st_size > 0
        if existia:
            # AUD-M6: confere a integridade ANTES de qualquer escrita; corrompido → avisa, não mexe no arquivo
            try:
                con = sqlite3.connect("file:" + quote(self.arquivo.resolve().as_posix(), safe="/:") + "?mode=ro",
                                      uri=True)
                try:
                    ok = con.execute("PRAGMA quick_check").fetchone()[0]
                    tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    cols = {r[1] for r in con.execute("PRAGMA table_info(cortes)")} if "cortes" in tabelas else set()
                finally:
                    con.close()
            except sqlite3.DatabaseError as e:
                raise BancoCorrompido(self.arquivo, str(e), backups_do_banco(pasta_backup)) from e
            if ok != "ok":
                raise BancoCorrompido(self.arquivo, str(ok)[:200], backups_do_banco(pasta_backup))
            if "cortes" in tabelas and not set(COLUNAS_CORTES) <= cols:
                pasta_backup.mkdir(parents=True, exist_ok=True)
                destino = pasta_backup / f"cortesex-antes-da-migracao-{time.strftime('%Y%m%d-%H%M%S')}.db"
                shutil.copy2(self.arquivo, destino)
                self.migracoes.append(f"backup do banco em {destino.name}")
        self.con = sqlite3.connect(str(self.arquivo), check_same_thread=False, timeout=30)
        self.con.row_factory = sqlite3.Row
        with self._lock:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.executescript(ESQUEMA)
            for tabela, extras in (("cortes", COLUNAS_CORTES), ("postagens", COLUNAS_POSTAGENS)):
                cols = {r[1] for r in self.con.execute(f"PRAGMA table_info({tabela})")}
                for nome, tipo in extras.items():
                    if nome not in cols:
                        self.con.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
            # cortes da 1.x: já são vídeos prontos
            n = self.con.execute(
                "UPDATE cortes SET estado = CASE WHEN descartado = 1 THEN 'descartado' ELSE 'aguardando_revisao' END,"
                " momento = criado, atualizado = criado WHERE estado IS NULL").rowcount
            if n:
                self.migracoes.append(f"{n} corte(s) da versão anterior marcados como prontos para revisão")
            # AUD-H1 (reforço no banco): no máximo um envio ativo por corte e rede, mesmo com dois processos.
            # "Postar de novo" de propósito marca forcado=1 e fica fora da regra.
            try:
                self.con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_postagem_ativa ON postagens(corte_id, plataforma)"
                                 " WHERE status IN ('enviando','processando','postado','incerto') AND forcado = 0")
            except sqlite3.IntegrityError:
                self.migracoes.append("há envios repetidos antigos no banco: a trava extra de duplicidade não foi "
                                      "criada (a trava do programa continua valendo)")
            # AUD-L6: eventos gravados antes da redação de segredos
            if not self.con.execute("SELECT 1 FROM meta WHERE chave='eventos_redigidos'").fetchone():
                from cortesex.seguranca import redigir
                for i, t in self.con.execute("SELECT id, texto FROM eventos").fetchall():
                    limpo = redigir(t)
                    if limpo != t:
                        self.con.execute("UPDATE eventos SET texto=? WHERE id=?", (limpo, i))
                self.con.execute("INSERT INTO meta (chave, valor) VALUES ('eventos_redigidos', '1')")
            self.con.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    def _todos(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def verificar(self) -> str:
        return self._todos("PRAGMA quick_check")[0]["quick_check"]

    # ----- cortes: criação e estados
    def novo_candidato(self, canal, arquivo, momento, score, motivo, chat, janela_ini, janela_fim, layout="blur"):
        agora = time.time()
        cur = self._exec(
            "INSERT INTO cortes (canal, arquivo, criado, momento, score, motivo, chat, janela_ini, janela_fim,"
            " layout, estado, atualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (canal, str(arquivo), momento, momento, score, motivo, json.dumps(chat, ensure_ascii=False),
             janela_ini, janela_fim, layout, DETECTADO, agora))
        self.historico(cur.lastrowid, DETECTADO, motivo)
        return cur.lastrowid

    def candidato_aberto(self, canal, desde):
        """Último candidato do canal ainda não capturado e recente (para agrupar picos do mesmo evento)."""
        r = self._todos("SELECT * FROM cortes WHERE canal=? AND estado=? AND momento>=? ORDER BY momento DESC LIMIT 1",
                        (canal, DETECTADO, desde))
        return _corte(r[0]) if r else None

    def agrupar(self, corte_id, janela_fim, score, motivo, chat):
        c = self.corte(corte_id)
        juntos = (c["chat"] + list(chat))[-40:]
        self._exec("UPDATE cortes SET janela_fim=MAX(janela_fim, ?), score=MAX(score, ?), motivo=?, chat=?,"
                   " atualizado=? WHERE id=?",
                   (janela_fim, score, (c["motivo"] + " + " + motivo)[:300], json.dumps(juntos, ensure_ascii=False),
                    time.time(), corte_id))
        self.historico(corte_id, DETECTADO, f"pico agrupado: {motivo}")

    def mudar_estado(self, corte_id, estado, detalhe="", erro="", proxima="", zerar_tentativas=True, **campos):
        sets = ["estado=?", "erro=?", "proxima_acao=?", "atualizado=?"]
        args = [estado, erro[:500], proxima, time.time()]
        if zerar_tentativas:
            sets.append("etapa_tentativas=0")
        for k, v in campos.items():
            sets.append(f"{k}=?")
            args.append(json.dumps(v, ensure_ascii=False) if k in JSON_CAMPOS else v)
        self._exec(f"UPDATE cortes SET {', '.join(sets)} WHERE id=?", (*args, corte_id))
        self.historico(corte_id, estado, detalhe or erro)

    def atualizar(self, corte_id, **campos):
        if not campos:
            return
        sets = [f"{k}=?" for k in campos]
        args = [json.dumps(v, ensure_ascii=False) if k in JSON_CAMPOS else v for k, v in campos.items()]
        self._exec(f"UPDATE cortes SET {', '.join(sets)}, atualizado=? WHERE id=?", (*args, time.time(), corte_id))

    def reivindicar(self, corte_id, de, para) -> bool:
        """Passa o corte de um estado para outro só se ninguém pegou antes (workers concorrentes)."""
        cur = self._exec("UPDATE cortes SET estado=?, atualizado=?, etapa_tentativas=etapa_tentativas+1 "
                         "WHERE id=? AND estado=?", (para, time.time(), corte_id, de))
        if cur.rowcount:
            self.historico(corte_id, para, "")
        return cur.rowcount == 1

    def pendentes(self, estados, limite=20):
        marcas = ",".join("?" * len(estados))
        return [_corte(r) for r in self._todos(
            f"SELECT * FROM cortes WHERE estado IN ({marcas}) ORDER BY momento LIMIT ?", (*estados, limite))]

    def recuperar_interrompidos(self) -> int:
        """Ao abrir: etapas que estavam rodando quando o programa fechou voltam ao último checkpoint."""
        n = 0
        for em, volta in EM_ANDAMENTO.items():
            for c in self._todos("SELECT id FROM cortes WHERE estado=?", (em,)):
                self.mudar_estado(c["id"], volta, "retomado após reinício", zerar_tentativas=False)
                n += 1
        return n

    def historico(self, corte_id, estado, detalhe=""):
        self._exec("INSERT INTO historico (corte_id, quando, estado, detalhe) VALUES (?,?,?,?)",
                   (corte_id, time.time(), estado, str(detalhe)[:500]))

    def historico_de(self, corte_id):
        return self._todos("SELECT quando, estado, detalhe FROM historico WHERE corte_id=? ORDER BY id", (corte_id,))

    # ----- cortes: leitura e edição
    def novo_corte(self, canal, arquivo, criado, duracao, score, motivo, chat, fala, titulo="",
                   legenda="", hashtags=()) -> int:
        """Compatibilidade (1.x e testes): um corte já pronto."""
        cur = self._exec(
            "INSERT INTO cortes (canal, arquivo, criado, momento, duracao, score, motivo, chat, fala, titulo,"
            " legenda, hashtags, estado, atualizado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (canal, str(arquivo), criado, criado, duracao, score, motivo, json.dumps(chat, ensure_ascii=False),
             fala, titulo, legenda, json.dumps(list(hashtags), ensure_ascii=False), REVISAO, time.time()))
        return cur.lastrowid

    def atualizar_textos(self, corte_id, titulo, legenda, hashtags, textos_plataforma=None):
        campos = {"titulo": titulo, "legenda": legenda, "hashtags": list(hashtags)}
        if textos_plataforma is not None:
            campos["textos_plataforma"] = textos_plataforma
        self.atualizar(corte_id, **campos)

    def descartar(self, corte_id, sim=True, motivo=""):
        c = self.corte(corte_id)
        if sim:
            self.mudar_estado(corte_id, DESCARTADO, motivo or "descartado pelo usuário",
                              motivo_descarte=motivo, descartado=1)
        else:
            volta = REVISAO if c and c.get("arquivo_removido") == 0 and Path(c["arquivo"]).exists() else MONTADO
            self.mudar_estado(corte_id, volta, "recuperado do descarte", descartado=0, motivo_descarte="")

    def aprovar(self, corte_id, sim=True):
        if sim:
            self.mudar_estado(corte_id, APROVADO, "aprovado pelo usuário", aprovado_em=time.time())
        else:
            self.mudar_estado(corte_id, REVISAO, "aprovação retirada", aprovado_em=0)

    def corte(self, corte_id):
        r = self._todos("SELECT * FROM cortes WHERE id=?", (corte_id,))
        return _corte(r[0]) if r else None

    def remover_corte(self, corte_id):
        self._exec("DELETE FROM cortes WHERE id=?", (corte_id,))

    def biblioteca(self, limite=200, horas=None, canal=None, categoria=None, estado=None, ordenar="editorial"):
        filtros, args = [], []
        if horas:
            filtros.append("criado >= ?")
            args.append(time.time() - horas * 3600)
        if canal:
            filtros.append("canal = ?")
            args.append(canal)
        if categoria:
            filtros.append("categoria = ?")
            args.append(categoria)
        if estado == "melhores":
            filtros.append("estado IN ('aguardando_revisao','aprovado')")
        elif estado == "publicado":
            filtros.append("EXISTS (SELECT 1 FROM postagens p WHERE p.corte_id=cortes.id AND p.status='postado')")
        elif estado == "pronto":
            filtros.append("estado = 'aprovado'")
        elif estado == "andamento":
            filtros.append("estado IN ('detectado','capturando','capturado','transcrevendo','transcrito',"
                           "'montando','montado','gerando_textos')")
        elif estado:
            filtros.append("estado = ?")
            args.append(estado)
        else:
            filtros.append("estado != 'descartado'")
        ordem = {"pico": "score DESC, criado DESC", "recente": "criado DESC",
                 "editorial": "COALESCE(score_editorial, -1) DESC, score DESC, criado DESC"}.get(ordenar)
        where = ("WHERE " + " AND ".join(filtros)) if filtros else ""
        cortes = [_corte(r) for r in self._todos(f"SELECT * FROM cortes {where} ORDER BY {ordem or 'criado DESC'} LIMIT ?",
                                                 (*args, limite))]
        for c in cortes:
            c["postagens"] = self._todos(
                "SELECT id, plataforma, status, url, erro, tipo_erro, quando FROM postagens WHERE corte_id=? ORDER BY id",
                (c["id"],))
        return cortes

    def ranking(self, limite=100, horas=None):
        return self.biblioteca(limite=limite, horas=horas, ordenar="pico")

    def candidatos_para(self, plataforma, horas, estados, limite=60):
        """Cortes prontos que ainda não foram (nem estão sendo) postados nessa rede."""
        marcas = ",".join("?" * len(estados))
        return [_corte(r) for r in self._todos(
            f"""SELECT * FROM cortes c WHERE estado IN ({marcas}) AND criado >= ? AND arquivo_removido = 0
                AND NOT EXISTS (SELECT 1 FROM postagens p WHERE p.corte_id = c.id AND p.plataforma = ?
                                AND p.status IN ('enviando','processando','postado','incerto'))
                AND (SELECT COUNT(*) FROM postagens p WHERE p.corte_id = c.id
                     AND p.plataforma = ? AND p.status = 'erro') < 2
                ORDER BY score DESC LIMIT ?""",
            (*estados, time.time() - horas * 3600, plataforma, plataforma, limite))]

    def proximo_para(self, plataforma, score_minimo, horas, exigir_textos):
        """Compatibilidade com a 1.x: o melhor pelo pico."""
        for c in self.candidatos_para(plataforma, horas, PRONTOS):
            if c["score"] >= score_minimo and (c["titulo"] or not exigir_textos):
                return c
        return None

    def sem_textos(self, horas):
        return [_corte(r) for r in self._todos(
            "SELECT * FROM cortes WHERE estado IN ('aguardando_revisao','aprovado','montado') AND titulo = ''"
            " AND criado >= ? ORDER BY score DESC", (time.time() - horas * 3600,))]

    def contagem_estados(self) -> dict:
        return {r["estado"]: r["n"] for r in self._todos("SELECT estado, COUNT(*) n FROM cortes GROUP BY estado")}

    def para_limpeza(self, antes_de):
        return [_corte(r) for r in self._todos(
            "SELECT * FROM cortes WHERE criado < ? AND arquivo_removido = 0", (antes_de,))]

    def cortes_antigos(self, antes_de):
        return self._todos("SELECT id, arquivo FROM cortes WHERE criado < ?", (antes_de,))

    def ja_postado(self, corte_id, plataforma) -> dict | None:
        r = self._todos("SELECT * FROM postagens WHERE corte_id=? AND plataforma=? AND status IN "
                        "('enviando','processando','postado','incerto') ORDER BY id DESC LIMIT 1",
                        (corte_id, plataforma))
        return r[0] if r else None

    # ----- postagens
    def nova_postagem(self, corte_id, plataforma, origem="agendador", forcado=False) -> int:
        """Reserva o envio (status 'enviando'). Levanta PostagemDuplicada se já houver um ativo."""
        try:
            return self._exec("INSERT INTO postagens (corte_id, plataforma, status, quando, origem, forcado)"
                              " VALUES (?,?,?,?,?,?)", (corte_id, plataforma, "enviando", time.time(), origem,
                                                        1 if forcado else 0)).lastrowid
        except sqlite3.IntegrityError as e:
            raise PostagemDuplicada(f"já existe um envio ativo deste corte para {plataforma}") from e

    def fim_postagem(self, post_id, status, url="", erro="", tipo_erro="", ext_id=None, detalhe=""):
        sets = "status=?, url=?, erro=?, tipo_erro=?, quando=?, detalhe=?"
        args = [status, url, erro[:500], tipo_erro, time.time(), detalhe[:500]]
        if ext_id is not None:
            sets += ", ext_id=?"
            args.append(ext_id)
        self._exec(f"UPDATE postagens SET {sets} WHERE id=?", (*args, post_id))

    def postagem(self, post_id):
        r = self._todos("SELECT * FROM postagens WHERE id=?", (post_id,))
        return r[0] if r else None

    def postagens_de(self, corte_id):
        return self._todos("SELECT id, plataforma, status, url, erro, tipo_erro, quando, origem, detalhe "
                           "FROM postagens WHERE corte_id=? ORDER BY id", (corte_id,))

    def postagens_status(self, status):
        return self._todos("SELECT * FROM postagens WHERE status=? ORDER BY id", (status,))

    def ultima_postagem(self, plataforma=None) -> float:
        sql = "SELECT MAX(quando) m FROM postagens WHERE status IN ('postado','enviando','processando')"
        args = ()
        if plataforma:
            sql += " AND plataforma = ?"
            args = (plataforma,)
        return self._todos(sql, args)[0]["m"] or 0.0

    def ultimo_canal_postado(self, plataforma) -> str:
        r = self._todos("SELECT c.canal FROM postagens p JOIN cortes c ON c.id=p.corte_id WHERE p.plataforma=? AND"
                        " p.status IN ('postado','processando') ORDER BY p.id DESC LIMIT 1", (plataforma,))
        return r[0]["canal"] if r else ""

    def postagens_desde(self, desde, plataforma=None) -> int:
        sql = "SELECT COUNT(*) n FROM postagens WHERE status IN ('postado','enviando','processando') AND quando >= ?"
        args = [desde]
        if plataforma:
            sql += " AND plataforma = ?"
            args.append(plataforma)
        return self._todos(sql, args)[0]["n"]

    def log(self, limite=200):
        return self._todos(
            """SELECT p.id, p.plataforma, p.status, p.url, p.erro, p.tipo_erro, p.quando, p.origem,
                      c.id corte_id, c.canal, c.titulo, c.score, c.score_editorial, c.arquivo
               FROM postagens p JOIN cortes c ON c.id = p.corte_id
               ORDER BY p.id DESC LIMIT ?""", (limite,))

    def limpar_enviando(self) -> int:
        """Ao abrir: envio que ficou pela metade vira 'incerto' (pode ter sido publicado).
        Não é repostado sozinho: o usuário confere e marca."""
        return self._exec("UPDATE postagens SET status='incerto', erro='O programa fechou durante o envio: "
                          "confira na rede se o vídeo foi publicado' WHERE status='enviando'").rowcount

    def resolver_incerto(self, post_id, publicado: bool, url=""):
        if publicado:
            self.fim_postagem(post_id, "postado", url=url, detalhe="confirmado pelo usuário")
        else:
            self.fim_postagem(post_id, "erro", erro="não publicado (confirmado pelo usuário)", tipo_erro="usuario")

    # ----- pausas
    def pausar(self, plataforma, ate, motivo, tipo, tentativas):
        self._exec("INSERT INTO pausas (plataforma, ate, motivo, tipo, tentativas) VALUES (?,?,?,?,?) "
                   "ON CONFLICT(plataforma) DO UPDATE SET ate=excluded.ate, motivo=excluded.motivo, "
                   "tipo=excluded.tipo, tentativas=excluded.tentativas", (plataforma, ate, motivo[:300], tipo, tentativas))

    def pausa(self, plataforma) -> dict | None:
        r = self._todos("SELECT * FROM pausas WHERE plataforma=?", (plataforma,))
        return r[0] if r else None

    def retomar(self, plataforma):
        self._exec("DELETE FROM pausas WHERE plataforma=?", (plataforma,))

    # ----- métricas
    def nova_metrica(self, post_id, views=None, likes=None, comentarios=None, compartilhamentos=None, extra=None):
        self._exec("INSERT INTO metricas (postagem_id, quando, views, likes, comentarios, compartilhamentos, extra)"
                   " VALUES (?,?,?,?,?,?,?)", (post_id, time.time(), views, likes, comentarios, compartilhamentos,
                                               json.dumps(extra or {})))

    def postagens_para_metricas(self, plataforma, desde, intervalo):
        return self._todos(
            """SELECT p.* FROM postagens p WHERE p.plataforma=? AND p.status='postado' AND p.quando>=? AND
               p.ext_id != '' AND COALESCE((SELECT MAX(quando) FROM metricas m WHERE m.postagem_id=p.id), 0) < ?""",
            (plataforma, desde, time.time() - intervalo))

    def metricas_ultimas(self):
        return self._todos(
            """SELECT p.plataforma, p.url, p.quando AS postado_em, c.id corte_id, c.canal, c.categoria,
                      c.duracao, c.score, c.score_editorial, c.titulo, m.views, m.likes, m.comentarios,
                      m.compartilhamentos, m.quando AS coletado_em
               FROM metricas m JOIN postagens p ON p.id=m.postagem_id JOIN cortes c ON c.id=p.corte_id
               WHERE m.id IN (SELECT MAX(id) FROM metricas GROUP BY postagem_id)""")

    # ----- eventos
    def evento(self, nivel, texto):
        self._exec("INSERT INTO eventos (quando, nivel, texto) VALUES (?,?,?)", (time.time(), nivel, texto[:500]))
        self._exec("DELETE FROM eventos WHERE id <= (SELECT MAX(id) FROM eventos) - 3000")

    def eventos(self, limite=150, nivel=None):
        if nivel:
            return self._todos("SELECT quando, nivel, texto FROM eventos WHERE nivel IN ('warning','error') "
                               "ORDER BY id DESC LIMIT ?", (limite,))
        return self._todos("SELECT quando, nivel, texto FROM eventos ORDER BY id DESC LIMIT ?", (limite,))

    def get_meta(self, chave, padrao=""):
        r = self._todos("SELECT valor FROM meta WHERE chave=?", (chave,))
        return r[0]["valor"] if r else padrao

    def set_meta(self, chave, valor):
        self._exec("INSERT INTO meta (chave, valor) VALUES (?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                   (chave, str(valor)))


def _corte(r: dict) -> dict:
    r = dict(r)
    for k in JSON_CAMPOS:
        if k not in r:
            continue
        padrao = [] if k in ("chat", "hashtags", "flags") else {}
        try:
            r[k] = json.loads(r.get(k) or json.dumps(padrao))
        except ValueError:
            r[k] = padrao
    return r
