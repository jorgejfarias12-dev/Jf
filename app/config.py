"""Configuração lida das variáveis de ambiente (definidas no painel do Render).

Nenhuma chave é escrita no código. Se API_FOOTBALL_KEY faltar, a aplicação
arranca na mesma, mas não recolhe dados e mostra o aviso na página.
"""
import os

# Chave secreta da API-Football (painel do Render → Environment)
API_KEY = os.getenv("API_FOOTBALL_KEY", "").strip()
API_BASE = os.getenv("API_FOOTBALL_BASE", "https://v3.football.api-sports.io")

# Ligas acompanhadas (IDs da API-Football), separadas por vírgulas.
# 94 Liga Portugal, 39 Premier League, 140 La Liga, 135 Serie A,
# 78 Bundesliga, 61 Ligue 1, 2 Liga dos Campeões, 3 Liga Europa,
# 848 Liga Conferência
LIGAS = [int(x) for x in os.getenv("LIGAS", "94,39,140,135,78,61,2,3,848").split(",") if x.strip()]

FUSO = os.getenv("FUSO_HORARIO", "Europe/Lisbon")

# Limite diário do plano (gratuito = 100). Ajustar se mudar de plano.
LIMITE_DIARIO = int(os.getenv("LIMITE_DIARIO", "100"))
# Pedidos que ficam sempre de reserva (nunca são gastos pela recolha automática)
RESERVA = int(os.getenv("RESERVA_PEDIDOS", "5"))

# Intervalo mínimo entre consultas de jogos em direto, em segundos.
# No plano gratuito, 900 s (15 min). Num plano pago pode descer até 15 s,
# a cadência de atualização indicada pela API-Football.
INTERVALO_MIN_DIRETO = int(os.getenv("INTERVALO_MIN_DIRETO", "900"))
# Classificações (posição, forma, golos) só se pedem com mais do que este
# número de pedidos disponíveis, para nunca prejudicar os jogos em direto.
LIMIAR_CLASSIFICACOES = int(os.getenv("LIMIAR_CLASSIFICACOES", "30"))
# Sem jogos em direto: atualizar o calendário do dia de X em X segundos
INTERVALO_SEM_DIRETO = int(os.getenv("INTERVALO_SEM_DIRETO", str(6 * 3600)))

# Base de dados: Postgres se DATABASE_URL existir; caso contrário SQLite local
# (no Render gratuito, o SQLite perde-se ao reiniciar).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///dados.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Token para disparar a recolha a partir de um agendador externo (cron-job.org, etc.)
TOKEN_RECOLHA = os.getenv("TOKEN_RECOLHA", "").strip()

# Desligar o ciclo automático (útil em testes)
RECOLHA_AUTOMATICA = os.getenv("RECOLHA_AUTOMATICA", "1") == "1"
