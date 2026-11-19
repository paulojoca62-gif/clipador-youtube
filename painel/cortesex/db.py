"""Banco SQLite: os cortes gerados e o log de cada postagem."""

import json
import sqlite3
import threading
import time
from pathlib import Path

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
    status TEXT NOT NULL,          -- enviando / postado / erro
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
"""


class Banco:
    def __init__(self, arquivo: Path):
        self._lock = threading.Lock()
        self.con = sqlite3.connect(str(arquivo), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        with self._lock:
            self.con.executescript(ESQUEMA)
            self.con.commit()

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    def _todos(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    # ----- cortes
    def novo_corte(self, canal, arquivo, criado, duracao, score, motivo, chat, fala, titulo="",
                   legenda="", hashtags=()) -> int:
        cur = self._exec(
            "INSERT INTO cortes (canal, arquivo, criado, duracao, score, motivo, chat, fala, titulo,"
            " legenda, hashtags) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (canal, str(arquivo), criado, duracao, score, motivo, json.dumps(chat, ensure_ascii=False),
             fala, titulo, legenda, json.dumps(list(hashtags), ensure_ascii=False)),
        )
        return cur.lastrowid

    def atualizar_textos(self, corte_id, titulo, legenda, hashtags):
        self._exec("UPDATE cortes SET titulo=?, legenda=?, hashtags=? WHERE id=?",
                   (titulo, legenda, json.dumps(list(hashtags), ensure_ascii=False), corte_id))

    def descartar(self, corte_id, sim=True):
        self._exec("UPDATE cortes SET descartado=? WHERE id=?", (1 if sim else 0, corte_id))

    def corte(self, corte_id):
        r = self._todos("SELECT * FROM cortes WHERE id=?", (corte_id,))
        return _corte(r[0]) if r else None

    def remover_corte(self, corte_id):
        self._exec("DELETE FROM cortes WHERE id=?", (corte_id,))

    def ranking(self, limite=100, horas=None):
        """Cortes do melhor para o pior pico do chat, com o que já foi postado em cada um."""
        args = []
        filtro = "WHERE descartado = 0"
        if horas:
            filtro += " AND criado >= ?"
            args.append(time.time() - horas * 3600)
        cortes = [_corte(r) for r in self._todos(
            f"SELECT * FROM cortes {filtro} ORDER BY score DESC, criado DESC LIMIT ?", (*args, limite))]
        for c in cortes:
            c["postagens"] = self._todos(
                "SELECT plataforma, status, url, erro, quando FROM postagens WHERE corte_id=? ORDER BY id",
                (c["id"],))
        return cortes

    def proximo_para(self, plataforma, score_minimo, horas, exigir_textos):
        """O melhor corte que ainda não foi postado (nem está sendo) nessa plataforma."""
        filtro = "AND titulo != ''" if exigir_textos else ""
        r = self._todos(
            f"""SELECT * FROM cortes c WHERE descartado = 0 AND score >= ? AND criado >= ? {filtro}
                AND NOT EXISTS (SELECT 1 FROM postagens p WHERE p.corte_id = c.id
                                AND p.plataforma = ? AND p.status IN ('enviando', 'postado'))
                AND (SELECT COUNT(*) FROM postagens p WHERE p.corte_id = c.id
                     AND p.plataforma = ? AND p.status = 'erro') < 2
                ORDER BY score DESC, criado DESC LIMIT 1""",
            (score_minimo, time.time() - horas * 3600, plataforma, plataforma))
        return _corte(r[0]) if r else None

    def sem_textos(self, horas):
        return [_corte(r) for r in self._todos(
            "SELECT * FROM cortes WHERE descartado = 0 AND titulo = '' AND criado >= ? ORDER BY score DESC",
            (time.time() - horas * 3600,))]

    def cortes_antigos(self, antes_de):
        return self._todos("SELECT id, arquivo FROM cortes WHERE criado < ?", (antes_de,))

    # ----- postagens
    def nova_postagem(self, corte_id, plataforma, origem="agendador") -> int:
        return self._exec("INSERT INTO postagens (corte_id, plataforma, status, quando, origem)"
                          " VALUES (?,?,?,?,?)", (corte_id, plataforma, "enviando", time.time(), origem)).lastrowid

    def fim_postagem(self, post_id, status, url="", erro=""):
        self._exec("UPDATE postagens SET status=?, url=?, erro=?, quando=? WHERE id=?",
                   (status, url, erro[:500], time.time(), post_id))

    def ultima_postagem(self, plataforma=None) -> float:
        sql = "SELECT MAX(quando) m FROM postagens WHERE status IN ('postado', 'enviando')"
        args = ()
        if plataforma:
            sql += " AND plataforma = ?"
            args = (plataforma,)
        r = self._todos(sql, args)
        return r[0]["m"] or 0.0

    def postagens_desde(self, desde, plataforma=None) -> int:
        sql = "SELECT COUNT(*) n FROM postagens WHERE status IN ('postado', 'enviando') AND quando >= ?"
        args = [desde]
        if plataforma:
            sql += " AND plataforma = ?"
            args.append(plataforma)
        return self._todos(sql, args)[0]["n"]

    def log(self, limite=200):
        return self._todos(
            """SELECT p.id, p.plataforma, p.status, p.url, p.erro, p.quando, p.origem,
                      c.id corte_id, c.canal, c.titulo, c.score, c.arquivo
               FROM postagens p JOIN cortes c ON c.id = p.corte_id
               ORDER BY p.id DESC LIMIT ?""", (limite,))

    def limpar_enviando(self):
        """Ao abrir: um envio que ficou pela metade (programa fechado) vira erro."""
        self._exec("UPDATE postagens SET status='erro', erro='O programa fechou durante o envio' "
                   "WHERE status='enviando'")

    # ----- eventos (log geral do sistema)
    def evento(self, nivel, texto):
        self._exec("INSERT INTO eventos (quando, nivel, texto) VALUES (?,?,?)", (time.time(), nivel, texto[:500]))
        self._exec("DELETE FROM eventos WHERE id <= (SELECT MAX(id) FROM eventos) - 2000")

    def eventos(self, limite=150):
        return self._todos("SELECT quando, nivel, texto FROM eventos ORDER BY id DESC LIMIT ?", (limite,))


def _corte(r: dict) -> dict:
    r = dict(r)
    for k in ("chat", "hashtags"):
        try:
            r[k] = json.loads(r.get(k) or "[]")
        except ValueError:
            r[k] = []
    return r
