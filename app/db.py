"""Armazenamento: jogos recolhidos, classificações, estado da recolha e registo de pedidos."""
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from . import config

engine = create_engine(config.DATABASE_URL, future=True, pool_pre_ping=True)

COLUNAS_JOGO = ("id", "data_local", "inicio_utc", "liga_id", "liga_codigo", "liga_nome", "pais",
                "epoca", "casa_id", "casa", "fora_id", "fora", "golos_casa", "golos_fora",
                "golos_90_casa", "golos_90_fora", "estado", "estado_longo", "minuto",
                "atualizado_utc", "bruto")


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iniciar():
    with engine.begin() as c:
        # «partidas» substitui a antiga tabela «jogos» (formato da fonte anterior)
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS partidas (
                id INTEGER PRIMARY KEY,
                data_local TEXT NOT NULL,
                inicio_utc TEXT NOT NULL,
                liga_id INTEGER, liga_codigo TEXT, liga_nome TEXT, pais TEXT, epoca INTEGER,
                casa_id INTEGER, casa TEXT, fora_id INTEGER, fora TEXT,
                golos_casa INTEGER, golos_fora INTEGER,
                golos_90_casa INTEGER, golos_90_fora INTEGER,
                estado TEXT, estado_longo TEXT, minuto INTEGER,
                atualizado_utc TEXT NOT NULL,
                bruto TEXT
            )"""))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS estado (
                chave TEXT PRIMARY KEY,
                valor TEXT
            )"""))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS classificacoes (
                liga_id INTEGER NOT NULL,
                epoca INTEGER NOT NULL,
                equipa_id INTEGER NOT NULL,
                dados TEXT NOT NULL,
                atualizado_utc TEXT NOT NULL,
                PRIMARY KEY (liga_id, epoca, equipa_id)
            )"""))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS pedidos (
                quando_utc TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                http INTEGER,
                restantes INTEGER,
                erro TEXT
            )"""))


def guardar_estado(chave: str, valor):
    with engine.begin() as c:
        c.execute(text("DELETE FROM estado WHERE chave=:k"), {"k": chave})
        c.execute(text("INSERT INTO estado (chave, valor) VALUES (:k, :v)"),
                  {"k": chave, "v": json.dumps(valor)})


def ler_estado(chave: str, omissao=None):
    with engine.connect() as c:
        r = c.execute(text("SELECT valor FROM estado WHERE chave=:k"), {"k": chave}).first()
    return json.loads(r[0]) if r else omissao


def registar_pedido(endpoint: str, http: int | None, restantes: int | None, erro: str | None):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pedidos (quando_utc, endpoint, http, restantes, erro)
                          VALUES (:q, :e, :h, :r, :x)"""),
                  {"q": agora_utc().isoformat(), "e": endpoint, "h": http, "r": restantes, "x": erro})


def pedidos_hoje_utc() -> int:
    """Pedidos feitos desde a meia-noite UTC."""
    inicio = agora_utc().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with engine.connect() as c:
        return c.execute(text("SELECT COUNT(*) FROM pedidos WHERE quando_utc >= :i"), {"i": inicio}).scalar_one()


def ultimos_pedidos(n: int = 15):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(
            text("SELECT * FROM pedidos ORDER BY quando_utc DESC LIMIT :n"), {"n": n})]


def guardar_jogo(reg: dict):
    """Grava um jogo já normalizado (ver recolha.normalizar_jogo); os valores vêm da fonte, sem alterações."""
    linha = {k: reg.get(k) for k in COLUNAS_JOGO}
    linha["atualizado_utc"] = agora_utc().isoformat()
    with engine.begin() as c:
        c.execute(text("DELETE FROM partidas WHERE id=:id"), {"id": linha["id"]})
        c.execute(text(f"INSERT INTO partidas ({', '.join(COLUNAS_JOGO)}) "
                       f"VALUES ({', '.join(':' + k for k in COLUNAS_JOGO)})"), linha)


def guardar_classificacao(liga_id: int, epoca: int, equipa_id: int, dados: dict):
    """Grava a linha de classificação de uma equipa (valores tal como vieram da fonte)."""
    with engine.begin() as c:
        c.execute(text("DELETE FROM classificacoes WHERE liga_id=:l AND epoca=:e AND equipa_id=:t"),
                  {"l": liga_id, "e": epoca, "t": equipa_id})
        c.execute(text("""INSERT INTO classificacoes (liga_id, epoca, equipa_id, dados, atualizado_utc)
                          VALUES (:l, :e, :t, :d, :a)"""),
                  {"l": liga_id, "e": epoca, "t": equipa_id,
                   "d": json.dumps(dados), "a": agora_utc().isoformat()})


def classificacao(liga_id: int, epoca: int, equipa_id: int) -> dict | None:
    with engine.connect() as c:
        r = c.execute(text("""SELECT dados, atualizado_utc FROM classificacoes
                              WHERE liga_id=:l AND epoca=:e AND equipa_id=:t"""),
                      {"l": liga_id, "e": epoca, "t": equipa_id}).first()
    if not r:
        return None
    return {**json.loads(r[0]), "_atualizado": r[1]}


def jogos_do_dia(data_local: str):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "SELECT * FROM partidas WHERE data_local=:d ORDER BY inicio_utc, liga_nome"), {"d": data_local})]
"""Armazenamento: jogos recolhidos, estado da quota e registo de pedidos."""
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

from . import config

engine = create_engine(config.DATABASE_URL, future=True, pool_pre_ping=True)


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iniciar():
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS jogos (
                id INTEGER PRIMARY KEY,
                data_local TEXT NOT NULL,
                inicio_utc TEXT NOT NULL,
                liga_id INTEGER, liga_nome TEXT, pais TEXT,
                casa TEXT, fora TEXT,
                golos_casa INTEGER, golos_fora INTEGER,
                estado TEXT, estado_longo TEXT, minuto INTEGER,
                atualizado_utc TEXT NOT NULL,
                bruto TEXT
            )"""))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS estado (
                chave TEXT PRIMARY KEY,
                valor TEXT
            )"""))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS classificacoes (
                liga_id INTEGER NOT NULL,
                epoca INTEGER NOT NULL,
                equipa_id INTEGER NOT NULL,
                dados TEXT NOT NULL,
                atualizado_utc TEXT NOT NULL,
                PRIMARY KEY (liga_id, epoca, equipa_id)
            )"""))
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS pedidos (
                quando_utc TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                http INTEGER,
                restantes INTEGER,
                erro TEXT
            )"""))


def guardar_estado(chave: str, valor):
    with engine.begin() as c:
        c.execute(text("DELETE FROM estado WHERE chave=:k"), {"k": chave})
        c.execute(text("INSERT INTO estado (chave, valor) VALUES (:k, :v)"),
                  {"k": chave, "v": json.dumps(valor)})


def ler_estado(chave: str, omissao=None):
    with engine.connect() as c:
        r = c.execute(text("SELECT valor FROM estado WHERE chave=:k"), {"k": chave}).first()
    return json.loads(r[0]) if r else omissao


def registar_pedido(endpoint: str, http: int | None, restantes: int | None, erro: str | None):
    with engine.begin() as c:
        c.execute(text("""INSERT INTO pedidos (quando_utc, endpoint, http, restantes, erro)
                          VALUES (:q, :e, :h, :r, :x)"""),
                  {"q": agora_utc().isoformat(), "e": endpoint, "h": http, "r": restantes, "x": erro})


def pedidos_hoje_utc() -> int:
    """Pedidos feitos desde a meia-noite UTC (a quota da API reinicia às 00:00 UTC)."""
    inicio = agora_utc().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with engine.connect() as c:
        return c.execute(text("SELECT COUNT(*) FROM pedidos WHERE quando_utc >= :i"), {"i": inicio}).scalar_one()


def ultimos_pedidos(n: int = 15):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(
            text("SELECT * FROM pedidos ORDER BY quando_utc DESC LIMIT :n"), {"n": n})]


def guardar_jogo(f: dict):
    """Grava um jogo tal como veio da API (sem alterar nem completar valores)."""
    fx, lg, tm, gl = f["fixture"], f["league"], f["teams"], f["goals"]
    reg = {
        "id": fx["id"],
        "data_local": fx["date"][:10],
        "inicio_utc": datetime.fromtimestamp(fx["timestamp"], timezone.utc).isoformat(),
        "liga_id": lg.get("id"), "liga_nome": lg.get("name"), "pais": lg.get("country"),
        "casa": tm["home"]["name"], "fora": tm["away"]["name"],
        "golos_casa": gl.get("home"), "golos_fora": gl.get("away"),
        "estado": fx["status"].get("short"), "estado_longo": fx["status"].get("long"),
        "minuto": fx["status"].get("elapsed"),
        "atualizado_utc": agora_utc().isoformat(),
        "bruto": json.dumps(f),
    }
    with engine.begin() as c:
        c.execute(text("DELETE FROM jogos WHERE id=:id"), {"id": reg["id"]})
        c.execute(text("""INSERT INTO jogos (id, data_local, inicio_utc, liga_id, liga_nome, pais,
                          casa, fora, golos_casa, golos_fora, estado, estado_longo, minuto,
                          atualizado_utc, bruto)
                          VALUES (:id, :data_local, :inicio_utc, :liga_id, :liga_nome, :pais,
                          :casa, :fora, :golos_casa, :golos_fora, :estado, :estado_longo, :minuto,
                          :atualizado_utc, :bruto)"""), reg)


def guardar_classificacao(liga_id: int, epoca: int, linha: dict):
    """Grava uma linha da classificação tal como veio da API."""
    with engine.begin() as c:
        c.execute(text("DELETE FROM classificacoes WHERE liga_id=:l AND epoca=:e AND equipa_id=:t"),
                  {"l": liga_id, "e": epoca, "t": linha["team"]["id"]})
        c.execute(text("""INSERT INTO classificacoes (liga_id, epoca, equipa_id, dados, atualizado_utc)
                          VALUES (:l, :e, :t, :d, :a)"""),
                  {"l": liga_id, "e": epoca, "t": linha["team"]["id"],
                   "d": json.dumps(linha), "a": agora_utc().isoformat()})


def classificacao(liga_id: int, epoca: int, equipa_id: int) -> dict | None:
    with engine.connect() as c:
        r = c.execute(text("""SELECT dados, atualizado_utc FROM classificacoes
                              WHERE liga_id=:l AND epoca=:e AND equipa_id=:t"""),
                      {"l": liga_id, "e": epoca, "t": equipa_id}).first()
    if not r:
        return None
    return {**json.loads(r[0]), "_atualizado": r[1]}


def jogos_do_dia(data_local: str):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(
            "SELECT * FROM jogos WHERE data_local=:d ORDER BY inicio_utc, liga_nome"), {"d": data_local})]
