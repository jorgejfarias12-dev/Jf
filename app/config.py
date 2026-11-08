"""Configuração lida das variáveis de ambiente (definidas no painel do Render).

Nenhuma chave é escrita no código. Se FOOTBALL_DATA_KEY faltar, a aplicação
arranca na mesma, mas não recolhe dados e mostra o aviso na página.
"""
import os

# Fonte de dados: football-data.org (plano gratuito, API v4).
# Chave secreta: painel do Render → Environment → FOOTBALL_DATA_KEY
API_KEY = os.getenv("FOOTBALL_DATA_KEY", "").strip()
API_BASE = os.getenv("FOOTBALL_DATA_BASE", "https://api.football-data.org/v4")
FONTE = "football-data.org"

# Competições acompanhadas (códigos da football-data.org), separadas por vírgulas.
# PPL Primeira Liga, PL Premier League, PD La Liga, SA Serie A, BL1 Bundesliga,
# FL1 Ligue 1, CL Liga dos Campeões, DED Eredivisie, ELC Championship.
# (A Liga Europa e a Liga Conferência não fazem parte do plano gratuito.)
COMPETICOES = [x.strip().upper() for x in
               os.getenv("COMPETICOES", "PPL,PL,PD,SA,BL1,FL1,CL").split(",") if x.strip()]

FUSO = os.getenv("FUSO_HORARIO", "Europe/Lisbon")

# Limite do plano gratuito: 10 pedidos por minuto (sem limite diário).
# A recolha faz no máximo 1 pedido por minuto, muito abaixo deste limite.
LIMITE_MINUTO = int(os.getenv("LIMITE_MINUTO", "10"))

# Intervalo entre atualizações enquanto há jogos a decorrer (segundos).
# No plano gratuito os resultados chegam com atraso, por isso não vale a pena menos.
INTERVALO_DIRETO = int(os.getenv("INTERVALO_DIRETO", "300"))
# Sem jogos a decorrer: atualizar o calendário de X em X segundos
INTERVALO_SEM_DIRETO = int(os.getenv("INTERVALO_SEM_DIRETO", str(3 * 3600)))

# Base de dados: Postgres se DATABASE_URL existir; caso contrário SQLite local
# (no Render gratuito, o SQLite perde-se ao reiniciar).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///dados.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

# Token para disparar a recolha a partir de um agendador externo
TOKEN_RECOLHA = os.getenv("TOKEN_RECOLHA", "").strip()

# Desligar o ciclo automático (útil em testes)
RECOLHA_AUTOMATICA = os.getenv("RECOLHA_AUTOMATICA", "1") == "1"
