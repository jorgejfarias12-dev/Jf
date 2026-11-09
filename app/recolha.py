"""Recolha de dados reais da football-data.org (plano gratuito, API v4).

Limites do plano gratuito: 10 pedidos por minuto, sem limite diário; resultados
com atraso (não são em tempo real). A recolha faz no máximo 1 pedido por minuto.

Estratégia:
  1. Calendário: 1 pedido a /matches de ontem até DIAS_CALENDARIO dias à frente
     traz os jogos de todas as competições do plano (incluindo os próximos jogos
     depois de pausas); guardam-se só as competições configuradas.
  2. Jogos a decorrer: o mesmo pedido, de INTERVALO_DIRETO em INTERVALO_DIRETO
     segundos, atualiza estado e resultado (incluindo os jogos que acabaram).
  2b. Próximos jogos: 1 pedido por competição e por dia a /competitions/{código}/matches
     (até DIAS_PROXIMOS dias), para mostrar a próxima jornada mesmo nas pausas.
  3. Classificações: 1 pedido por competição com jogos, uma vez por dia, só
     quando não há atualização de direto pendente.
  4. Erros: a recolha espera 15 minutos antes de tentar de novo (1 minuto se o
     erro for de limite por minuto), para nunca entrar em ciclo.
Nada é estimado: os valores gravados são os que a fonte devolve.
"""
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from . import config, db

log = logging.getLogger("recolha")

# Estados normalizados (códigos curtos usados pela página)
ESTADOS = {
    "SCHEDULED": ("NS", "Por começar"), "TIMED": ("NS", "Por começar"),
    "IN_PLAY": ("LIVE", "A decorrer"), "PAUSED": ("HT", "Intervalo"),
    "FINISHED": ("FT", "Terminado"), "POSTPONED": ("PST", "Adiado"),
    "SUSPENDED": ("SUSP", "Suspenso"), "CANCELLED": ("CANC", "Cancelado"),
    "AWARDED": ("AWD", "Atribuído"),
}
TERMINADOS = {"FT", "AET", "PEN", "PST", "CANC", "AWD"}
A_DECORRER = {"LIVE", "HT", "SUSP"}
JANELA_ANTES = timedelta(minutes=5)
JANELA_DEPOIS = timedelta(minutes=150)  # 90' + intervalo + descontos + eventual prolongamento
ESPERA_ERRO = timedelta(minutes=15)
ESPERA_LIMITE = timedelta(minutes=1)
DIAS_CALENDARIO = 8   # a fonte aceita no máximo 10 dias entre dateFrom e dateTo
DIAS_PROXIMOS = 28    # jogos marcados por competição (mostra a próxima jornada mesmo em pausas)


def data_local(agora: datetime) -> str:
    return agora.astimezone(ZoneInfo(config.FUSO)).date().isoformat()


# ---------- normalização (só muda a forma, nunca os valores) ----------
def _soma(*vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def normalizar_jogo(m: dict) -> dict:
    comp, area = m.get("competition") or {}, m.get("area") or {}
    score = m.get("score") or {}
    ft = score.get("fullTime") or {}
    rt = score.get("regularTime") or {}   # só existe quando houve prolongamento
    et = score.get("extraTime") or {}
    curto, longo = ESTADOS.get(m.get("status"), (m.get("status"), m.get("status")))
    if curto == "FT" and score.get("duration") == "EXTRA_TIME":
        curto, longo = "AET", "Terminado após prolongamento"
    elif curto == "FT" and score.get("duration") == "PENALTY_SHOOTOUT":
        curto, longo = "PEN", "Terminado após penáltis"
    if rt:
        golos = (_soma(rt.get("home"), et.get("home")), _soma(rt.get("away"), et.get("away")))
        golos_90 = (rt.get("home"), rt.get("away"))
    else:
        golos = golos_90 = (ft.get("home"), ft.get("away"))
    inicio = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
    casa, fora = m.get("homeTeam") or {}, m.get("awayTeam") or {}
    return {
        "id": m["id"], "data_local": data_local(inicio), "inicio_utc": inicio.isoformat(),
        "liga_id": comp.get("id"), "liga_codigo": comp.get("code"), "liga_nome": comp.get("name"),
        "pais": area.get("name"), "epoca": (m.get("season") or {}).get("id"),
        "casa_id": casa.get("id"), "casa": casa.get("shortName") or casa.get("name"),
        "fora_id": fora.get("id"), "fora": fora.get("shortName") or fora.get("name"),
        "golos_casa": golos[0], "golos_fora": golos[1],
        "golos_90_casa": golos_90[0], "golos_90_fora": golos_90[1],
        "estado": curto, "estado_longo": longo, "minuto": m.get("minute"),
        "bruto": json.dumps(m),
    }


def normalizar_classificacao(resp: dict) -> list[tuple[int, dict]]:
    """Junta as tabelas TOTAL, HOME e AWAY numa linha por equipa: [(equipa_id, dados)]."""
    def bloco(lin):
        if not lin:
            return None
        return {"played": lin.get("playedGames"), "win": lin.get("won"), "draw": lin.get("draw"),
                "lose": lin.get("lost"),
                "goals": {"for": lin.get("goalsFor"), "against": lin.get("goalsAgainst")}}

    por_tipo: dict[str, dict[int, dict]] = {}
    grupo_de: dict[int, str | None] = {}
    for s in resp.get("standings") or []:
        tipo = s.get("type") or "TOTAL"
        for lin in s.get("table") or []:
            tid = (lin.get("team") or {}).get("id")
            if tid is None:
                continue
            por_tipo.setdefault(tipo, {})[tid] = lin
            if tipo == "TOTAL":
                grupo_de[tid] = s.get("group")
    linhas = []
    for tid, t in por_tipo.get("TOTAL", {}).items():
        forma = (t.get("form") or "").replace(",", "").replace(" ", "")
        linhas.append((tid, {
            "rank": t.get("position"), "points": t.get("points"), "group": grupo_de.get(tid),
            "form": forma or None, "all": bloco(t),
            "home": bloco(por_tipo.get("HOME", {}).get(tid)),
            "away": bloco(por_tipo.get("AWAY", {}).get(tid)),
        }))
    return linhas


# ---------- decisão ----------
def jogos_ativos(jogos: list[dict], agora: datetime) -> list[dict]:
    """Jogos que estão (ou podem estar) a decorrer agora."""
    ativos = []
    for j in jogos:
        if j["estado"] in TERMINADOS:
            continue
        inicio = datetime.fromisoformat(j["inicio_utc"])
        if j["estado"] in A_DECORRER or (inicio - JANELA_ANTES <= agora <= inicio + JANELA_DEPOIS):
            ativos.append(j)
    return ativos


def _passou(desde: str | None, agora: datetime, segundos: float) -> bool:
    return desde is None or (agora - datetime.fromisoformat(desde)).total_seconds() >= segundos


def classificacao_pendente(jogos: list[dict], estado: dict, hoje: str) -> str | None:
    """Primeira competição (com jogos nos próximos dias) cuja classificação ainda não foi pedida hoje."""
    feitas = estado.get("classificacoes") or {}
    ja = set(feitas.get("codigos", [])) if feitas.get("data") == hoje else set()
    for j in jogos:
        cod = j.get("liga_codigo")
        if cod and cod not in ja:
            return cod
    return None


def proximos_pendente(estado: dict, hoje: str) -> str | None:
    """Primeira competição cujos próximos jogos (até DIAS_PROXIMOS dias) ainda não foram pedidos hoje."""
    feitas = estado.get("proximos") or {}
    ja = set(feitas.get("codigos", [])) if feitas.get("data") == hoje else set()
    for cod in config.COMPETICOES:
        if cod not in ja:
            return cod
    return None


def decidir(agora: datetime, jogos_hoje: list[dict], estado: dict, jogos_amanha: list[dict] | None = None):
    """Decide a próxima ação. Devolve (ação, dados) ou (None, motivo).

    Prioridades: calendário do dia → jogos a decorrer → classificações → atualização periódica.
    """
    if not config.API_KEY:
        return None, "Falta configurar FOOTBALL_DATA_KEY no alojamento."
    espera = estado.get("proxima_tentativa")
    if espera and agora < datetime.fromisoformat(espera):
        return None, "A aguardar antes de tentar de novo, após um erro da fonte."

    hoje = data_local(agora)
    if estado.get("data_calendario") != hoje:
        return "calendario", None
    ativos = jogos_ativos(jogos_hoje, agora)
    if ativos and _passou(estado.get("ultimo_direto"), agora, config.INTERVALO_DIRETO):
        return "direto", None
    prox = proximos_pendente(estado, hoje)
    if prox:
        return "proximos", prox
    pendente = classificacao_pendente(jogos_hoje + (jogos_amanha or []), estado, hoje)
    if pendente:
        return "classificacao", pendente
    if ativos:
        return None, "Jogos a decorrer; próxima atualização dentro de poucos minutos."
    if _passou(estado.get("ultimo_calendario"), agora, config.INTERVALO_SEM_DIRETO):
        return "calendario", None
    return None, "Sem jogos a decorrer; calendário atualizado recentemente."


# ---------- pedidos ----------
def _falhou(erro: str, espera: timedelta, http: int | None = None):
    agora = db.agora_utc()
    db.guardar_estado("ultimo_erro", {"quando": agora.isoformat(), "erro": erro, "http": http})
    db.guardar_estado("proxima_tentativa", (agora + espera).isoformat())
    log.warning("Erro da fonte: %s", erro)


async def pedir(cliente: httpx.AsyncClient, endpoint: str, params: dict | None = None) -> dict | None:
    """Faz um pedido à fonte, regista-o e devolve o JSON (ou None se houver erro)."""
    try:
        r = await cliente.get(f"{config.API_BASE}{endpoint}", params=params or {},
                              headers={"X-Auth-Token": config.API_KEY}, timeout=30)
    except httpx.HTTPError as e:
        erro = f"Falha de ligação: {e}"
        db.registar_pedido(endpoint, None, None, erro)
        _falhou(erro, ESPERA_ERRO)
        return None

    restantes = r.headers.get("X-Requests-Available-Minute")
    restantes = int(restantes) if restantes and restantes.isdigit() else None
    if restantes is not None:
        db.guardar_estado("restantes_minuto", restantes)

    corpo, erro = None, None
    try:
        corpo = r.json()
    except ValueError:
        erro = f"Resposta inválida (HTTP {r.status_code})"
    if r.status_code != 200 and not erro:
        msg = corpo.get("message") if isinstance(corpo, dict) else None
        erro = f"HTTP {r.status_code}: {msg}" if msg else f"HTTP {r.status_code}"

    db.registar_pedido(endpoint, r.status_code, restantes, erro)
    if erro:
        _falhou(erro, ESPERA_LIMITE if r.status_code == 429 else ESPERA_ERRO, r.status_code)
        return None
    return corpo


async def atualizar_jogos(cliente: httpx.AsyncClient, agora: datetime) -> int | None:
    """Um pedido: jogos de ontem até DIAS_CALENDARIO dias à frente (a fonte usa datas UTC; a margem cobre o fuso)."""
    ini = agora.date()
    corpo = await pedir(cliente, "/matches", {"dateFrom": (ini - timedelta(days=1)).isoformat(),
                                              "dateTo": (ini + timedelta(days=DIAS_CALENDARIO)).isoformat()})
    if corpo is None:
        return None
    n = 0
    for m in corpo.get("matches") or []:
        if (m.get("competition") or {}).get("code") in config.COMPETICOES:
            db.guardar_jogo(normalizar_jogo(m))
            n += 1
    return n


async def executar(acao: str, info, cliente: httpx.AsyncClient) -> str:
    agora = db.agora_utc()
    hoje = data_local(agora)
    if acao in ("calendario", "direto"):
        n = await atualizar_jogos(cliente, agora)
        if n is None:
            return "Erro ao atualizar os jogos (ver último erro)."
        db.guardar_estado("ultimo_direto" if acao == "direto" else "ultimo_calendario", agora.isoformat())
        if acao == "calendario":
            db.guardar_estado("data_calendario", hoje)
            return f"Calendário dos próximos dias atualizado ({n} jogos)."
        return f"Jogos a decorrer atualizados ({n} jogos lidos)."

    if acao == "proximos":
        codigo = info
        ini = agora.date()
        corpo = await pedir(cliente, f"/competitions/{codigo}/matches",
                            {"dateFrom": ini.isoformat(), "dateTo": (ini + timedelta(days=DIAS_PROXIMOS)).isoformat()})
        feitas = db.ler_estado("proximos") or {}
        codigos = feitas.get("codigos", []) if feitas.get("data") == hoje else []
        db.guardar_estado("proximos", {"data": hoje, "codigos": codigos + [codigo]})
        if corpo is None:
            erro = db.ler_estado("ultimo_erro") or {}
            if erro.get("http") != 429:
                db.guardar_estado("proxima_tentativa", None)  # não atrasar o resto da recolha
            return f"Próximos jogos de {codigo} indisponíveis."
        n = 0
        for m in corpo.get("matches") or []:
            m.setdefault("competition", corpo.get("competition") or {})
            db.guardar_jogo(normalizar_jogo(m))
            n += 1
        return f"Próximos jogos de {codigo} atualizados ({n} jogos)."

    if acao == "classificacao":
        codigo = info
        corpo = await pedir(cliente, f"/competitions/{codigo}/standings")
        feitas = db.ler_estado("classificacoes") or {}
        codigos = feitas.get("codigos", []) if feitas.get("data") == hoje else []
        # Marca como tratada hoje mesmo com erro (ex.: fora do plano), para não repetir
        db.guardar_estado("classificacoes", {"data": hoje, "codigos": codigos + [codigo]})
        if corpo is None:
            bloq = db.ler_estado("classificacoes_bloqueadas") or {}
            lista = bloq.get("codigos", []) if bloq.get("data") == hoje else []
            db.guardar_estado("classificacoes_bloqueadas", {"data": hoje, "codigos": lista + [codigo]})
            erro = db.ler_estado("ultimo_erro") or {}
            if erro.get("http") != 429:
                db.guardar_estado("proxima_tentativa", None)  # não atrasar o resto da recolha
            return f"Classificação de {codigo} indisponível."
        liga_id = (corpo.get("competition") or {}).get("id")
        epoca = (corpo.get("season") or {}).get("id")
        linhas = normalizar_classificacao(corpo)
        for tid, dados in linhas:
            db.guardar_classificacao(liga_id, epoca, tid, dados)
        return f"Classificação de {codigo} atualizada ({len(linhas)} equipas)."
    return "Ação desconhecida."


def estado_atual() -> dict:
    return {k: db.ler_estado(k) for k in
            ("data_calendario", "ultimo_calendario", "ultimo_direto", "restantes_minuto",
             "ultimo_erro", "classificacoes", "classificacoes_bloqueadas", "proxima_tentativa", "proximos")}


async def passo(cliente: httpx.AsyncClient) -> str:
    """Um passo do ciclo (1 por minuto): decide e, se for caso disso, faz 1 pedido."""
    agora = db.agora_utc()
    proximos = db.jogos_entre(data_local(agora + timedelta(days=1)),
                              data_local(agora + timedelta(days=DIAS_PROXIMOS)))
    acao, info = decidir(agora, db.jogos_do_dia(data_local(agora)), estado_atual(), proximos)
    msg = info if acao is None else await executar(acao, info, cliente)
    db.guardar_estado("ultima_decisao", msg)
    return msg
