"""Recolha de dados reais da API-Football, com gestão da quota diária.

Estratégia (pensada para o plano gratuito de 100 pedidos/dia):
  1. Calendário do dia: 1 pedido a /fixtures?date=... devolve todos os jogos
     do dia; guardam-se só os das ligas configuradas.
  2. Jogos a decorrer: 1 pedido a /fixtures?ids=... (até 20 jogos por pedido)
     devolve o estado e o resultado desses jogos, incluindo os que acabaram
     de terminar (ao contrário de ?live=all, onde desaparecem).
  3. O intervalo entre consultas em direto é calculado a partir dos pedidos
     que restam e do tempo de jogo que falta, nunca abaixo de INTERVALO_MIN_DIRETO.
  4. Sem jogos a decorrer, só se atualiza o calendário de 6 em 6 horas.
  5. Abaixo da reserva, a recolha automática pára até a quota reiniciar.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from . import config, db

log = logging.getLogger("recolha")

TERMINADOS = {"FT", "AET", "PEN", "PST", "CANC", "ABD", "AWD", "WO"}
A_DECORRER = {"1H", "HT", "2H", "ET", "BT", "P", "SUSP", "INT", "LIVE"}
JANELA_ANTES = timedelta(minutes=5)
JANELA_DEPOIS = timedelta(minutes=150)  # 90' + intervalo + descontos + eventual prolongamento
MAX_IDS = 20
erro_ultimo_pedido: str | None = None  # erro devolvido pelo pedido mais recente (ou None)


def data_local(agora: datetime) -> str:
    return agora.astimezone(ZoneInfo(config.FUSO)).date().isoformat()


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


def pedidos_disponiveis(restantes_api: int | None, feitos_hoje: int) -> int:
    """Pedidos que a recolha automática ainda pode gastar hoje."""
    pelo_registo = config.LIMITE_DIARIO - feitos_hoje
    base = pelo_registo if restantes_api is None else min(restantes_api, pelo_registo)
    return max(0, base - config.RESERVA)


def intervalo_direto(ativos: list[dict], agora: datetime, disponiveis: int) -> float:
    """Segundos entre consultas em direto, repartindo os pedidos pelo tempo de jogo que falta."""
    fim = max(datetime.fromisoformat(j["inicio_utc"]) + JANELA_DEPOIS for j in ativos)
    reinicio_quota = (agora + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    fim = min(fim, reinicio_quota)
    segundos = max(60.0, (fim - agora).total_seconds())
    consultas_possiveis = max(1, disponiveis - 1)  # 1 pedido por consulta; guarda 1 para o fecho
    return max(float(config.INTERVALO_MIN_DIRETO), segundos / consultas_possiveis)


def epoca_do_jogo(j: dict) -> int | None:
    """Época (ano) do jogo, tal como a API a indica no registo original."""
    try:
        return json.loads(j["bruto"])["league"]["season"]
    except (KeyError, TypeError, ValueError):
        return None


def classificacao_pendente(jogos: list[dict], estado: dict, hoje: str) -> tuple[int, int] | None:
    """Primeira liga (com jogos hoje ou amanhã) cuja classificação ainda não foi pedida hoje."""
    feitas = estado.get("classificacoes") or {}
    ja = set(map(tuple, feitas.get("ligas", []))) if feitas.get("data") == hoje else set()
    for j in jogos:
        epoca = epoca_do_jogo(j)
        chave = (j["liga_id"], epoca)
        if epoca is not None and chave not in ja:
            return chave
    return None


def decidir(agora: datetime, jogos_hoje: list[dict], estado: dict, disponiveis: int,
            jogos_amanha: list[dict] | None = None):
    """Decide a próxima ação. Devolve (ação, dados) ou (None, motivo).

    Prioridades: calendário do dia → jogos em direto → classificações (só com
    folga na quota) → atualização periódica do calendário.
    """
    if not config.API_KEY:
        return None, "Falta configurar API_FOOTBALL_KEY no alojamento."
    if disponiveis <= 0:
        return None, "Quota diária esgotada (ou só resta a reserva). Retoma após as 00:00 UTC."

    hoje = data_local(agora)
    ultimo_cal = estado.get("ultimo_calendario")
    if estado.get("data_calendario") != hoje or ultimo_cal is None:
        return "calendario", None

    espera_msg = None
    ativos = jogos_ativos(jogos_hoje, agora)
    if ativos:
        ultimo_dir = estado.get("ultimo_direto")
        espera = intervalo_direto(ativos, agora, disponiveis)
        if ultimo_dir is None or (agora - datetime.fromisoformat(ultimo_dir)).total_seconds() >= espera:
            return "direto", [j["id"] for j in ativos]
        espera_msg = f"Jogos a decorrer; próxima consulta dentro de {int(espera // 60)} min."

    if disponiveis > config.LIMIAR_CLASSIFICACOES and estado.get("classificacoes_bloqueadas") != hoje:
        pendente = classificacao_pendente(jogos_hoje + (jogos_amanha or []), estado, hoje)
        if pendente:
            return "classificacao", pendente

    if espera_msg:
        return None, espera_msg
    if (agora - datetime.fromisoformat(ultimo_cal)).total_seconds() >= config.INTERVALO_SEM_DIRETO:
        return "calendario", None
    return None, "Sem jogos a decorrer; calendário atualizado recentemente."


async def pedir(cliente: httpx.AsyncClient, endpoint: str, params: dict) -> list[dict] | None:
    """Faz um pedido à API, regista-o e devolve 'response' (ou None se houver erro)."""
    global erro_ultimo_pedido
    try:
        r = await cliente.get(f"{config.API_BASE}{endpoint}", params=params,
                              headers={"x-apisports-key": config.API_KEY}, timeout=30)
    except httpx.HTTPError as e:
        erro_ultimo_pedido = f"Falha de ligação: {e}"
        db.registar_pedido(endpoint, None, None, erro_ultimo_pedido)
        return None

    restantes = r.headers.get("x-ratelimit-requests-remaining")
    restantes = int(restantes) if restantes and restantes.isdigit() else None
    if restantes is not None:
        db.guardar_estado("restantes_api", restantes)

    erro = None
    dados = None
    try:
        corpo = r.json()
        erros = corpo.get("errors")
        if erros:  # a API devolve erros como lista vazia ou dicionário
            erro = "; ".join(f"{k}: {v}" for k, v in erros.items()) if isinstance(erros, dict) else str(erros)
        else:
            dados = corpo.get("response", [])
    except ValueError:
        erro = f"Resposta inválida (HTTP {r.status_code})"
    if r.status_code != 200 and not erro:
        erro = f"HTTP {r.status_code}"

    db.registar_pedido(endpoint, r.status_code, restantes, erro)
    erro_ultimo_pedido = erro
    if erro:
        db.guardar_estado("ultimo_erro", {"quando": db.agora_utc().isoformat(), "erro": erro})
        log.warning("Erro da API em %s: %s", endpoint, erro)
        return None
    return dados


async def executar(acao: str, ids: list[int] | None, cliente: httpx.AsyncClient) -> int:
    """Executa a ação decidida. Devolve o número de jogos gravados."""
    agora = db.agora_utc()
    gravados = 0
    if acao == "calendario":
        hoje = data_local(agora)
        amanha = data_local(agora + timedelta(days=1))
        dados = await pedir(cliente, "/fixtures", {"date": hoje, "timezone": config.FUSO})
        if dados is not None:
            # Os jogos de amanhã servem para preparar boletins com antecedência
            # (só se pede se o pedido de hoje correu bem, para não gastar quota em erro).
            seguintes = await pedir(cliente, "/fixtures", {"date": amanha, "timezone": config.FUSO})
            for f in dados + (seguintes or []):
                if f["league"]["id"] in config.LIGAS:
                    db.guardar_jogo(f)
                    gravados += 1
        # Regista a tentativa mesmo com erro, para não repetir pedidos em ciclo
        db.guardar_estado("data_calendario", hoje)
        db.guardar_estado("ultimo_calendario", agora.isoformat())
    elif acao == "direto":
        # O plano gratuito não aceita ?ids=...; o calendário do dia traz o estado e o
        # resultado atual de todos os jogos (incluindo os que acabaram), num só pedido.
        dados = await pedir(cliente, "/fixtures", {"date": data_local(agora), "timezone": config.FUSO})
        for f in dados or []:
            if f["league"]["id"] in config.LIGAS:
                db.guardar_jogo(f)
                gravados += 1 if f["fixture"]["id"] in ids else 0
        db.guardar_estado("ultimo_direto", agora.isoformat())
    elif acao == "classificacao":
        liga, epoca = ids
        dados = await pedir(cliente, "/standings", {"league": liga, "season": epoca})
        if dados is None and "do not have access" in (erro_ultimo_pedido or ""):
            # O plano não inclui classificações desta época: não insistir hoje (poupa quota)
            db.guardar_estado("classificacoes_bloqueadas", data_local(agora))
        for item in dados or []:
            for grupo in item["league"].get("standings", []):
                for linha in grupo:
                    db.guardar_classificacao(liga, epoca, linha)
                    gravados += 1
        # Marca a liga como tratada hoje, mesmo com erro, para não repetir em ciclo
        hoje = data_local(agora)
        feitas = db.ler_estado("classificacoes") or {}
        ligas = feitas.get("ligas", []) if feitas.get("data") == hoje else []
        db.guardar_estado("classificacoes", {"data": hoje, "ligas": ligas + [[liga, epoca]]})
    return gravados


def estado_atual() -> dict:
    return {k: db.ler_estado(k) for k in
            ("data_calendario", "ultimo_calendario", "ultimo_direto", "restantes_api",
             "ultimo_erro", "classificacoes", "classificacoes_bloqueadas")}


MENSAGENS = {
    "calendario": "Calendário de hoje e amanhã atualizado ({n} jogos).",
    "direto": "Jogos em direto atualizados ({n} jogos).",
    "classificacao": "Classificação atualizada ({n} equipas).",
}


async def passo(cliente: httpx.AsyncClient) -> str:
    """Um passo do ciclo: decide e, se for caso disso, executa."""
    agora = db.agora_utc()
    estado = estado_atual()
    disponiveis = pedidos_disponiveis(estado["restantes_api"], db.pedidos_hoje_utc())
    acao, info = decidir(agora, db.jogos_do_dia(data_local(agora)), estado, disponiveis,
                         db.jogos_do_dia(data_local(agora + timedelta(days=1))))
    if acao is None:
        db.guardar_estado("ultima_decisao", info)
        return info
    n = await executar(acao, info, cliente)
    msg = MENSAGENS[acao].format(n=n)
    db.guardar_estado("ultima_decisao", msg)
    return msg
