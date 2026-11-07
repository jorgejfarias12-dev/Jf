"""Aplicação web: mostra os jogos recolhidos e o estado da recolha."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from . import config, db, recolha

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
modelos = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
trinco = asyncio.Lock()  # impede duas recolhas em simultâneo


async def ciclo_protegido():
    async with httpx.AsyncClient() as cliente:
        while True:
            try:
                async with trinco:
                    logging.getLogger("recolha").info(await recolha.passo(cliente))
            except Exception:
                logging.getLogger("recolha").exception("Erro no ciclo de recolha")
            await asyncio.sleep(60)


@asynccontextmanager
async def vida(app: FastAPI):
    db.iniciar()
    tarefa = asyncio.create_task(ciclo_protegido()) if config.RECOLHA_AUTOMATICA else None
    yield
    if tarefa:
        tarefa.cancel()


app = FastAPI(title="Análise de Futebol", lifespan=vida)


def hora_local(iso: str | None) -> str:
    if not iso:
        return "—"
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(config.FUSO)).strftime("%d/%m %H:%M")


modelos.env.filters["hora"] = hora_local
modelos.env.filters["so_hora"] = lambda iso: hora_local(iso)[-5:] if iso else "—"


def resumo_estado() -> dict:
    estado = recolha.estado_atual()
    feitos = db.pedidos_hoje_utc()
    return {
        **estado,
        "chave_configurada": bool(config.API_KEY),
        "pedidos_hoje": feitos,
        "limite": config.LIMITE_DIARIO,
        "disponiveis": recolha.pedidos_disponiveis(estado["restantes_api"], feitos),
        "ultima_decisao": db.ler_estado("ultima_decisao"),
        "persistente": not config.DATABASE_URL.startswith("sqlite"),
    }


@app.get("/", response_class=HTMLResponse)
def app_movel(request: Request):
    """Página simples para o telemóvel: jogos, boletim e banca."""
    return modelos.TemplateResponse(request, "app.html", {})


@app.get("/estado", response_class=HTMLResponse)
def inicio(request: Request, data: str | None = None):
    """Página técnica: quota, pedidos e erros da recolha."""
    dia = data or recolha.data_local(db.agora_utc())
    jogos = db.jogos_do_dia(dia)
    ligas: dict[str, list] = {}
    for j in jogos:
        ligas.setdefault(f"{j['liga_nome']} ({j['pais']})", []).append(j)
    return modelos.TemplateResponse(request, "inicio.html", {
        "dia": dia, "ligas": ligas, "estado": resumo_estado(),
        "pedidos": db.ultimos_pedidos(10),
        "terminados": recolha.TERMINADOS, "a_decorrer": recolha.A_DECORRER,
    })


def resultado_90(bruto: str | None) -> tuple[int | None, int | None]:
    """Resultado aos 90 minutos (o que conta para 1X2), tal como vem da API."""
    if not bruto:
        return None, None
    ft = (json.loads(bruto).get("score") or {}).get("fulltime") or {}
    return ft.get("home"), ft.get("away")


@app.get("/api/jogos")
def api_jogos(data: str | None = None):
    dia = data or recolha.data_local(db.agora_utc())
    jogos = []
    for j in db.jogos_do_dia(dia):
        casa90, fora90 = resultado_90(j.get("bruto"))
        jogos.append({**{k: v for k, v in j.items() if k != "bruto"},
                      "golos_90_casa": casa90, "golos_90_fora": fora90})
    return {"data": dia, "fonte": "API-Football",
            "atualizado": db.ler_estado("ultimo_direto") or db.ler_estado("ultimo_calendario"),
            "terminados": sorted(recolha.TERMINADOS), "a_decorrer": sorted(recolha.A_DECORRER),
            "jogos": jogos}


@app.get("/manifest.webmanifest")
def manifesto():
    return JSONResponse({
        "name": "Futebol e Boletins", "short_name": "Boletins",
        "start_url": "/", "display": "standalone",
        "background_color": "#0f1412", "theme_color": "#0f1412", "lang": "pt-PT",
        "icons": [{"src": "/icone.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
    }, media_type="application/manifest+json")


ICONE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
         '<rect width="100" height="100" rx="22" fill="#14532d"/>'
         '<circle cx="50" cy="50" r="27" fill="none" stroke="#fff" stroke-width="6"/>'
         '<path d="M50 23v54M23 50h54" stroke="#fff" stroke-width="4"/></svg>')


@app.get("/icone.svg")
def icone():
    return Response(ICONE, media_type="image/svg+xml")


@app.get("/api/estado")
def api_estado():
    return resumo_estado()


@app.api_route("/api/recolher", methods=["GET", "POST"])
async def api_recolher(token: str = ""):
    """Dispara um passo de recolha. Para agendadores externos (mantêm o serviço acordado)."""
    if not config.TOKEN_RECOLHA or token != config.TOKEN_RECOLHA:
        raise HTTPException(status_code=403, detail="Token inválido.")
    if trinco.locked():
        return {"resultado": "Recolha já em curso."}
    async with trinco, httpx.AsyncClient() as cliente:
        return {"resultado": await recolha.passo(cliente)}


@app.get("/saude")
def saude():
    return {"ok": True}
