"""Odds de referência (The Odds API, plano gratuito) e probabilidades «justas».

Ideia (testada com 10 980 jogos reais de 2021/22 a 2026/27, ver LEIA-ME):
  - O mercado mais eficiente (Pinnacle; em alternativa a bolsa Betfair) dá a melhor
    estimativa disponível da probabilidade real de cada resultado, depois de lhe
    retirar a margem.
  - Só vale a pena apostar quando a casa onde se aposta (Placard) paga pelo menos
    VALOR_MINIMO acima da odd justa. Nos dados históricos, apostar assim deu lucro;
    apostar «normalmente» deu prejuízo.

Plano gratuito: 500 créditos por mês. Um pedido de odds com 2 mercados (1X2 e golos)
numa região custa 2 créditos; a lista de eventos é gratuita. Pede-se cada liga no
máximo uma vez por dia (e só se tiver jogos nas próximas 48 h), e uma segunda vez no
próprio dia de jogo se sobrarem créditos.
"""
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import text

from . import config, db

log = logging.getLogger("odds")

CHAVE = os.getenv("ODDS_API_KEY", "").strip()
BASE = os.getenv("ODDS_API_BASE", "https://api.the-odds-api.com/v4")
FONTE = "The Odds API (Pinnacle / Betfair)"
VALOR_MINIMO = float(os.getenv("VALOR_MINIMO", "0.03"))   # +3% acima da odd justa
CREDITOS_RESERVA = int(os.getenv("ODDS_CREDITOS_RESERVA", "60"))  # não gastar abaixo disto
HORAS_JANELA = 48

# código interno -> chave da The Odds API
LIGAS = {
    "PPL": ("soccer_portugal_primeira_liga", "Primeira Liga"),
    "PL": ("soccer_epl", "Premier League"),
    "PD": ("soccer_spain_la_liga", "La Liga"),
    "SA": ("soccer_italy_serie_a", "Serie A"),
    "BL1": ("soccer_germany_bundesliga", "Bundesliga"),
    "FL1": ("soccer_france_ligue_one", "Ligue 1"),
    "CL": ("soccer_uefa_champs_league", "Liga dos Campeões"),
    "EL": ("soccer_uefa_europa_league", "Liga Europa"),
    "ECL": ("soccer_uefa_europa_conference_league", "Liga Conferência"),
}
REFERENCIA = ("pinnacle", "betfair_ex_eu")   # por ordem de preferência
CASAS = "pinnacle,betfair_ex_eu,williamhill,unibet_fr,betclic_fr,marathonbet,sport888,betsson,nordicbet,tipico_de"


def agora() -> datetime:
    return db.agora_utc()


def data_local(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo(config.FUSO)).date().isoformat()


# ---------- base de dados ----------
def iniciar():
    with db.engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS odds_eventos (
                id TEXT PRIMARY KEY,
                liga TEXT NOT NULL,
                inicio_utc TEXT NOT NULL,
                data_local TEXT NOT NULL,
                casa TEXT NOT NULL,
                fora TEXT NOT NULL,
                referencia TEXT,
                dados TEXT NOT NULL,
                atualizado_utc TEXT NOT NULL
            )"""))


def guardar_evento(ev: dict):
    with db.engine.begin() as c:
        c.execute(text("DELETE FROM odds_eventos WHERE id=:id"), {"id": ev["id"]})
        c.execute(text("""INSERT INTO odds_eventos (id, liga, inicio_utc, data_local, casa, fora, referencia, dados, atualizado_utc)
                          VALUES (:id, :liga, :inicio_utc, :data_local, :casa, :fora, :referencia, :dados, :atualizado_utc)"""),
                  {**{k: ev[k] for k in ("id", "liga", "inicio_utc", "data_local", "casa", "fora", "referencia")},
                   "dados": json.dumps(ev["mercados"]), "atualizado_utc": agora().isoformat()})


def eventos_futuros(horas: int = 72) -> list[dict]:
    a = agora()
    with db.engine.connect() as c:
        linhas = c.execute(text("""SELECT * FROM odds_eventos WHERE inicio_utc > :a AND inicio_utc < :b
                                   ORDER BY inicio_utc"""),
                           {"a": a.isoformat(), "b": (a + timedelta(hours=horas)).isoformat()})
        out = []
        for r in linhas:
            d = dict(r._mapping)
            d["mercados"] = json.loads(d.pop("dados"))
            out.append(d)
    return out


# ---------- cálculo ----------
def sem_margem(odds: dict[str, float]) -> dict[str, float] | None:
    """Probabilidades justas: inverso das odds, normalizado para somar 1 (retira a margem)."""
    if not odds or any(not o or o <= 1 for o in odds.values()):
        return None
    inv = {k: 1 / o for k, o in odds.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def _mercado(bm: dict, chave: str) -> list[dict] | None:
    for m in bm.get("markets") or []:
        if m.get("key") == chave:
            return m.get("outcomes") or []
    return None


def normalizar_evento(ev: dict, liga: str) -> dict | None:
    """Converte um evento da The Odds API em probabilidades justas por mercado."""
    casa, fora = ev.get("home_team"), ev.get("away_team")
    casas = {b.get("key"): b for b in ev.get("bookmakers") or []}
    ref = next((k for k in REFERENCIA if k in casas), None)

    def odds_1x2(bm):
        out = _mercado(bm, "h2h")
        if not out:
            return None
        d = {}
        for o in out:
            nome = o.get("name")
            d["1" if nome == casa else "2" if nome == fora else "X"] = o.get("price")
        return d if set(d) == {"1", "X", "2"} else None

    def odds_golos(bm):
        out = _mercado(bm, "totals")
        if not out:
            return None
        d = {}
        for o in out:
            if o.get("point") == 2.5:
                d["+2.5" if o.get("name") == "Over" else "-2.5"] = o.get("price")
        return d if set(d) == {"+2.5", "-2.5"} else None

    def justas(funcao):
        if ref and funcao(casas[ref]):
            return sem_margem(funcao(casas[ref])), ref
        # sem casa de referência: média das probabilidades sem margem de todas as casas
        lista = [p for p in (sem_margem(funcao(b) or {}) for b in casas.values()) if p]
        if not lista:
            return None, None
        chaves = lista[0].keys()
        return {k: sum(p[k] for p in lista) / len(lista) for k in chaves}, "média"

    p1x2, r1 = justas(odds_1x2)
    pgol, r2 = justas(odds_golos)
    if not p1x2 and not pgol:
        return None
    mercados = {}
    for probs in (p1x2, pgol):
        for k, p in (probs or {}).items():
            mercados[k] = {"prob": round(p, 4), "odd_justa": round(1 / p, 3)}
    if p1x2:  # dupla hipótese a partir do 1X2
        for k, (a, b) in {"1X": ("1", "X"), "X2": ("X", "2"), "12": ("1", "2")}.items():
            p = p1x2[a] + p1x2[b]
            mercados[k] = {"prob": round(p, 4), "odd_justa": round(1 / p, 3)}
    inicio = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
    return {"id": ev["id"], "liga": liga, "inicio_utc": inicio.isoformat(), "data_local": data_local(inicio),
            "casa": casa, "fora": fora, "referencia": r1 or r2, "mercados": mercados}


def avaliar(prob: float, odd_casa: float) -> dict:
    """Valor esperado de apostar 1 € à odd da casa, sabendo a probabilidade justa."""
    ve = prob * odd_casa - 1
    return {"valor_esperado": round(ve, 4), "tem_valor": ve >= VALOR_MINIMO,
            "odd_minima": round((1 + VALOR_MINIMO) / prob, 2)}


def montar_boletins(escolhas: list[dict], minimo: int = 3, maximo: int = 6,
                    odd_min: float = 1.5, odd_max: float = 3.8) -> list[dict]:
    """Agrupa escolhas COM VALOR em boletins de 3 a 6 jogos (um jogo por boletim no máximo uma vez).

    escolhas: [{evento, mercado, prob, odd}] — só entram as que têm valor e odds dentro da faixa.
    Preferem-se boletins mais curtos (menos risco): enche-se cada boletim até `minimo`
    e só se passa de `minimo` quando as escolhas que sobram não chegam para outro boletim.
    """
    boas = [e for e in escolhas if odd_min <= e["odd"] <= odd_max and e["prob"] * e["odd"] - 1 >= VALOR_MINIMO]
    # uma escolha por jogo (a de maior valor esperado)
    por_jogo = {}
    for e in boas:
        atual = por_jogo.get(e["evento"])
        if not atual or e["prob"] * e["odd"] > atual["prob"] * atual["odd"]:
            por_jogo[e["evento"]] = e
    lista = sorted(por_jogo.values(), key=lambda e: -(e["prob"] * e["odd"]))
    n = len(lista)
    if n < minimo:
        return []
    k = max(1, n // minimo)                     # número de boletins
    grupos = [lista[i::k] for i in range(k)]    # distribui alternadamente
    boletins = []
    for g in grupos:
        g = g[:maximo]
        if len(g) < minimo:
            continue
        odd = 1.0
        prob = 1.0
        for e in g:
            odd *= e["odd"]
            prob *= e["prob"]
        boletins.append({"escolhas": g, "odd_total": round(odd, 2), "prob_real": round(prob, 4),
                         "valor_esperado": round(prob * odd - 1, 4)})
    return boletins


# ---------- recolha ----------
def _estado(chave, omissao=None):
    return db.ler_estado("odds_" + chave, omissao)


def _guardar(chave, valor):
    db.guardar_estado("odds_" + chave, valor)


async def _pedir(cliente: httpx.AsyncClient, caminho: str, params: dict) -> list | None:
    try:
        r = await cliente.get(f"{BASE}{caminho}", params={**params, "apiKey": CHAVE}, timeout=30)
    except httpx.HTTPError as e:
        _guardar("ultimo_erro", {"quando": agora().isoformat(), "erro": f"Falha de ligação: {e}"})
        return None
    rest = r.headers.get("x-requests-remaining")
    if rest is not None:
        try:
            _guardar("creditos_restantes", int(float(rest)))
        except ValueError:
            pass
    db.registar_pedido("odds:" + caminho.split("/")[-2] if caminho.endswith("/odds") else "odds:" + caminho,
                       r.status_code, None, None if r.status_code == 200 else r.text[:200])
    if r.status_code != 200:
        _guardar("ultimo_erro", {"quando": agora().isoformat(), "erro": f"HTTP {r.status_code}: {r.text[:200]}"})
        return None
    try:
        return r.json()
    except ValueError:
        return None


def decidir(a: datetime, estado: dict) -> tuple[str | None, str | None]:
    """Próxima liga a atualizar (ou None). Uma liga por passo."""
    if not CHAVE:
        return None, "Falta configurar ODDS_API_KEY."
    hoje = data_local(a)
    feitas = estado.get("feitas") or {}
    feitas = feitas if feitas.get("data") == hoje else {"data": hoje, "ligas": {}}
    creditos = estado.get("creditos")
    sem_jogos = estado.get("sem_jogos") or {}
    sem_jogos = sem_jogos if sem_jogos.get("data") == hoje else {"data": hoje, "ligas": []}
    for cod in LIGAS:
        if cod in sem_jogos["ligas"]:
            continue
        vezes = feitas["ligas"].get(cod, 0)
        if vezes == 0:
            return cod, None
    # segunda atualização no dia de jogo, a partir das 12 h (hora local), se houver créditos
    if a.astimezone(ZoneInfo(config.FUSO)).hour >= 12 and (creditos is None or creditos > CREDITOS_RESERVA + 100):
        com_jogo_hoje = {e["liga"] for e in eventos_futuros(24) if e["data_local"] == hoje}
        for cod in LIGAS:
            if cod in com_jogo_hoje and feitas["ligas"].get(cod, 0) == 1:
                return cod, None
    return None, "Odds de referência atualizadas hoje."


async def passo(cliente: httpx.AsyncClient) -> str:
    a = agora()
    hoje = data_local(a)
    estado = {"feitas": _estado("feitas"), "creditos": _estado("creditos_restantes"),
              "sem_jogos": _estado("sem_jogos")}
    cod, motivo = decidir(a, estado)
    if cod is None:
        return motivo
    sport, nome = LIGAS[cod]
    feitas = estado["feitas"] if (estado["feitas"] or {}).get("data") == hoje else {"data": hoje, "ligas": {}}
    feitas["ligas"][cod] = feitas["ligas"].get(cod, 0) + 1
    _guardar("feitas", feitas)  # marca já, para não repetir em caso de erro

    # 1) lista de eventos (gratuita): só gasta créditos se houver jogos nas próximas 48 h
    evs = await _pedir(cliente, f"/sports/{sport}/events", {})
    limite = a + timedelta(hours=HORAS_JANELA)
    proximos = [e for e in (evs or []) if a < datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) < limite]
    if not proximos:
        sj = estado["sem_jogos"] if (estado["sem_jogos"] or {}).get("data") == hoje else {"data": hoje, "ligas": []}
        sj["ligas"].append(cod)
        _guardar("sem_jogos", sj)
        return f"{nome}: sem jogos nas próximas {HORAS_JANELA} h."
    creditos = estado["creditos"]
    if creditos is not None and creditos <= CREDITOS_RESERVA:
        return f"{nome}: créditos mensais quase esgotados ({creditos}); não pedi odds."

    # 2) odds (2 créditos: 1X2 + golos)
    dados = await _pedir(cliente, f"/sports/{sport}/odds",
                         {"regions": "eu", "markets": "h2h,totals", "oddsFormat": "decimal", "bookmakers": CASAS})
    if dados is None:
        return f"{nome}: erro ao pedir odds."
    n = 0
    for ev in dados:
        reg = normalizar_evento(ev, cod)
        if reg:
            guardar_evento(reg)
            n += 1
    _guardar("ultima_atualizacao", a.isoformat())
    return f"{nome}: odds de referência de {n} jogos atualizadas."


def estado_publico() -> dict:
    return {"chave_configurada": bool(CHAVE), "fonte": FONTE, "valor_minimo": VALOR_MINIMO,
            "creditos_restantes": _estado("creditos_restantes"), "ultima_atualizacao": _estado("ultima_atualizacao"),
            "ultimo_erro": _estado("ultimo_erro")}


# ---------- ligar aos jogos da football-data (nomes diferentes nas duas fontes) ----------
_RUIDO = {"fc", "cf", "sc", "ac", "afc", "ssc", "sv", "fk", "sk", "cd", "ud", "rc", "club", "de", "da", "do",
          "calcio", "football", "futebol", "clube", "real", "athletic", "1", "04", "05", "1899", "1846", "1907"}


def _tokens(nome: str) -> set[str]:
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _RUIDO and len(t) >= 3}


def mesma_equipa(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return (a or "").lower() == (b or "").lower()
    return bool(ta & tb) or any(x.startswith(y) or y.startswith(x) for x in ta for y in tb if min(len(x), len(y)) >= 4)
