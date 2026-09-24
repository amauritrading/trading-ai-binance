# DEPLOY_MARKER_VERTICAL_HIBRIDO_LATERAL_V3_20260920
from fastapi import FastAPI, Query
import requests
import os
import json
import time
import hmac
import hashlib
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode
from openai import OpenAI
import threading

app = FastAPI()

# =========================
# CONFIGURAÇÕES GERAIS
# =========================

BINANCE_API_URL = "https://api.binance.com"
BINANCE_DATA_URL = "https://data-api.binance.vision"

VALOR_POR_TRADE_USDT = 10
ESTRATEGIA_VERSAO = "vertical_hibrido_v3_1_sol_janela6_20260924"

# Parâmetros reais do executor local principal (porta 8001).
# Mantidos centralizados para evitar divergência entre preview, mensagem e execução.
ALVO_EXECUTOR_PERCENTUAL = 0.007       # +0,70%
STOP_EXECUTOR_PERCENTUAL = 0.0055      # -0,55%
STOP_LIMIT_EXECUTOR_PERCENTUAL = 0.0065  # -0,65%

EXECUTOR_BASE_URL = os.getenv(
    "EXECUTOR_BASE_URL",
    "https://trader-jundiai.ngrok.app"
).rstrip("/")

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============================================================
# CONFIGURAÇÃO DOS ATIVOS
#
# Motor vertical adaptado da arquitetura Híbrido V2 do lateral.
# Compartilha a lógica de localização/anti-atraso/alvo plausível,
# mas mantém tendência de alta como identidade do vertical.
# ============================================================

CONFIG_ATIVOS = {
    "BTCUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 6,
        "price_decimals": 2,
        "grupo": "CORE",
        "entrada": {
            # Base adaptada do lateral: localização + anti-atraso + alvo plausível.
            # BTC recebe a faixa mais curta por ter menor ruído percentual.
            "janela_range_local": 12,
            "posicao_range_local_max": 0.55,
            "impulso_desde_fundo_max": 0.0055,
            "rompimento_max_para_tp": 0.0025,
            "distancia_preco_ema9_max": 0.0047,
            "rejeicao_minima": 0.0010,
            "volume_minimo_relativo": 0.88,
            "score_minimo": 80,
            "qualidade_ia_minima": 68,
            "corpo_pressao_min": 0.62,
            "volume_pressao_relativo": 1.20,
            "amplitude_minima": 0.0055
        }
    },
    "ETHUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 5,
        "price_decimals": 2,
        "grupo": "CORE",
        "entrada": {
            "janela_range_local": 12,
            "posicao_range_local_max": 0.52,
            "impulso_desde_fundo_max": 0.0065,
            "rompimento_max_para_tp": 0.0030,
            "distancia_preco_ema9_max": 0.0050,
            "rejeicao_minima": 0.0011,
            "volume_minimo_relativo": 0.90,
            "score_minimo": 80,
            "qualidade_ia_minima": 70,
            "corpo_pressao_min": 0.60,
            "volume_pressao_relativo": 1.20,
            "amplitude_minima": 0.0060
        }
    },
    "SOLUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 3,
        "price_decimals": 2,
        "grupo": "CORE",
        "entrada": {
            # SOL tolera um pouco mais de deslocamento/ruído, mas exige
            # posição mais baixa no range para não perseguir candle explosivo.
            "janela_range_local": 6,
            "posicao_range_local_max": 0.50,
            "impulso_desde_fundo_max": 0.0070,
            "rompimento_max_para_tp": 0.0035,
            "distancia_preco_ema9_max": 0.0055,
            "rejeicao_minima": 0.0013,
            "volume_minimo_relativo": 0.92,
            "score_minimo": 82,
            "qualidade_ia_minima": 72,
            "corpo_pressao_min": 0.58,
            "volume_pressao_relativo": 1.20,
            "amplitude_minima": 0.0065
        }
    }
}

GRUPOS = {
    "CORE": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "ALT": []
}

ATIVOS_MONITORADOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

ultimos_sinais = {}

# =========================
# TELEGRAM
# =========================

def enviar_telegram(mensagem, symbol=None, preco=None, tempo=None):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("TELEGRAM_NAO_CONFIGURADO")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    reply_markup = None

    if symbol and preco:
        if tempo is None:
            tempo = int(time.time())

        approval_url = (
            f"{EXECUTOR_BASE_URL}/aprovar/{symbol}"
            f"?token={os.getenv('APPROVAL_TOKEN')}"
            f"&preco={preco}"
            f"&tempo={tempo}"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprovar compra", "url": approval_url}
                ]
            ]
        }

    elif symbol:
        approval_url = (
            f"{EXECUTOR_BASE_URL}/aprovar/{symbol}"
            f"?token={os.getenv('APPROVAL_TOKEN')}"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprovar compra", "url": approval_url}
                ]
            ]
        }

    payload = {
        "chat_id": chat_id,
        "text": mensagem
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resposta = requests.post(url, json=payload, timeout=10)
        print("TELEGRAM_STATUS:", resposta.status_code)
        print("TELEGRAM_RESPOSTA:", resposta.text)
    except Exception as e:
        print("ERRO_TELEGRAM:", str(e))


@app.get("/teste-telegram")
def teste_telegram():
    enviar_telegram("🚀 Teste de mensagem do sistema Railway!")
    return {"status": "mensagem enviada"}


# =========================
# ROTAS BASE
# =========================

@app.get("/")
def home():
    return {
        "status": "online",
        "sistema": "trading-ai",
        "ativos_monitorados": ATIVOS_MONITORADOS,
        "valor_por_trade_usdt": VALOR_POR_TRADE_USDT,
        "estrategia_versao": ESTRATEGIA_VERSAO
    }


# =========================
# UTILITÁRIOS
# =========================

def obter_grupo(symbol):
    symbol = symbol.upper()
    return CONFIG_ATIVOS.get(symbol, {}).get("grupo")


def assinar_params(params: dict, secret: str):
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


def arredondar(valor, casas):
    quant = Decimal("1") / (Decimal("10") ** casas)
    return str(Decimal(str(valor)).quantize(quant, rounding=ROUND_DOWN))


def registrar_evento(tipo, dados):
    try:
        evento = {
            "tipo": tipo,
            "timestamp": int(time.time()),
            **dados
        }

        with open("trades_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(evento, ensure_ascii=False) + "\n")

    except Exception as e:
        print("ERRO_LOG:", str(e))


# =========================
# BINANCE DATA
# =========================

def get_klines(symbol, interval="5m", limit=50):
    url = f"{BINANCE_DATA_URL}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def get_preco_atual(symbol):
    url = f"{BINANCE_DATA_URL}/api/v3/ticker/price?symbol={symbol}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    dados = response.json()
    return float(dados["price"])


def calcular_ma(closes, periodo):
    return sum(closes[-periodo:]) / periodo


def calcular_rsi(closes, periodo=14):
    ganhos = []
    perdas = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            ganhos.append(diff)
            perdas.append(0)
        else:
            ganhos.append(0)
            perdas.append(abs(diff))

    media_ganhos = sum(ganhos[-periodo:]) / periodo
    media_perdas = sum(perdas[-periodo:]) / periodo

    if media_perdas == 0:
        return 100

    rs = media_ganhos / media_perdas
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calcular_edge_contexto(data):
    try:
        closes = [float(c[4]) for c in data]
        highs = [float(c[2]) for c in data]
        lows = [float(c[3]) for c in data]

        preco = closes[-1]

        # =========================
        # 1. PRESSÃO DE ROMPIMENTO
        # =========================
        suporte = min(lows[-10:])
        resistencia = max(highs[-10:])

        toques_resistencia = sum(1 for h in highs[-5:] if abs(h - resistencia) / resistencia < 0.002)
        toques_suporte = sum(1 for l in lows[-5:] if abs(l - suporte) / suporte < 0.002)

        pressao_rompimento = False

        if toques_resistencia >= 3:
            pressao_rompimento = "resistencia"
        elif toques_suporte >= 3:
            pressao_rompimento = "suporte"

        # =========================
        # 2. REJEIÇÃO (FORÇA REAL)
        # =========================
        ultima = data[-1]

        abertura = float(ultima[1])
        fechamento = float(ultima[4])
        maxima = float(ultima[2])
        minima = float(ultima[3])

        corpo = abs(fechamento - abertura)
        range_total = maxima - minima

        rejeicao = "neutra"

        if range_total > 0:
            sombra_superior = maxima - max(abertura, fechamento)
            sombra_inferior = min(abertura, fechamento) - minima

            if sombra_inferior > corpo * 1.2:
                rejeicao = "compra"
            elif sombra_superior > corpo * 1.2:
                rejeicao = "venda"

        return {
            "pressao_rompimento": pressao_rompimento,
            "rejeicao": rejeicao
        }

    except Exception as e:
        print("ERRO_EDGE:", str(e))
        return {
            "pressao_rompimento": None,
            "rejeicao": "neutra"
        }
def calcular_contexto_4h(symbol):
    try:
        data_4h_bruta = get_klines(symbol, interval="4h", limit=120)

        # A ultima kline da Binance pode estar em formacao.
        # O contexto 4h usa somente candles concluidos para evitar
        # que a classificacao macro mude no meio do candle.
        data_4h = data_4h_bruta[:-1]

        if len(data_4h) < 99:
            raise ValueError("Historico 4h insuficiente para MA99 com candle fechado")

        closes = [float(c[4]) for c in data_4h]
        volumes = [float(c[5]) for c in data_4h]

        preco_fechado_4h = closes[-1]
        ma7_4h = calcular_ma(closes, 7)
        ma25_4h = calcular_ma(closes, 25)
        ma99_4h = calcular_ma(closes, 99)

        volume_atual_4h = volumes[-1]
        volume_medio_4h = sum(volumes[-10:]) / 10

        macro_baixista = (
            preco_fechado_4h < ma25_4h
            and ma25_4h < ma99_4h
        )

        perto_resistencia_4h = (
            preco_fechado_4h < ma25_4h
            and ((ma25_4h - preco_fechado_4h) / preco_fechado_4h) < 0.012
        )

        volume_4h_fraco = volume_atual_4h < volume_medio_4h

        return {
            "ma7_4h": ma7_4h,
            "ma25_4h": ma25_4h,
            "ma99_4h": ma99_4h,
            "macro_baixista": macro_baixista,
            "perto_resistencia_4h": perto_resistencia_4h,
            "volume_4h_fraco": volume_4h_fraco,
            "contexto_4h_candle_fechado": True
        }

    except Exception as e:
        print("ERRO_CONTEXTO_4H:", str(e))
        return {
            "ma7_4h": None,
            "ma25_4h": None,
            "ma99_4h": None,
            "macro_baixista": False,
            "perto_resistencia_4h": False,
            "volume_4h_fraco": False,
            "contexto_4h_candle_fechado": False
        }

def calcular_ema(valores, periodo):
    if not valores:
        return 0
    ema = float(valores[0])
    k = 2.0 / (float(periodo) + 1.0)
    for valor in valores[1:]:
        ema = (float(valor) * k) + (ema * (1.0 - k))
    return ema


def calcular_score(dados, ia=None):
    # Score de ranking, NÃO representa probabilidade de WIN.
    score = int(dados.get("score_tecnico", 0))
    if ia:
        if ia.get("aprovar") is True:
            score += 5
        if ia.get("risco") == "baixo":
            score += 3
    return max(0, min(score, 100))

# =========================
# ANÁLISE TÉCNICA
# =========================

def gerar_analise(symbol):
    """
    VERTICAL HÍBRIDO V3 — base estrutural adaptada do lateral Híbrido V2.

    O que foi trazido do lateral:
    - localização da entrada dentro do range local;
    - anti-atraso pelo impulso desde o fundo recente;
    - alvo plausível sem depender de grande rompimento;
    - confirmação por reação / rejeição / qualidade de candle;
    - pressão vendedora como veto contextual, não empilhamento infinito de filtros.

    O que permanece específico do vertical:
    - tendência de alta em 5m é obrigatória;
    - contexto 4h continua sendo observado;
    - setup usa candles FECHADOS e preço atual apenas para localização/gatilho;
    - TP/SL não são alterados aqui; continuam alinhados ao executor vertical.
    """
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        raise ValueError("Ativo não permitido.")

    config = CONFIG_ATIVOS[symbol]
    p = config["entrada"]

    data_bruta = get_klines(symbol, limit=50)
    data = data_bruta[:-1]  # estrutura somente com candles fechados

    if len(data) < 30:
        raise ValueError("Historico 5m insuficiente para analise vertical hibrida")

    preco = float(get_preco_atual(symbol))
    contexto_4h = calcular_contexto_4h(symbol)

    closes = [float(c[4]) for c in data]
    highs = [float(c[2]) for c in data]
    lows = [float(c[3]) for c in data]
    volumes = [float(c[5]) for c in data]

    ultimo_close = closes[-1]
    penultimo_close = closes[-2]
    ultimo_open = float(data[-1][1])
    ultimo_high = highs[-1]
    ultimo_low = lows[-1]
    penultimo_high = highs[-2]

    ema9 = calcular_ema(closes[-20:], 9)
    ema21 = calcular_ema(closes[-30:], 21)
    ma7 = calcular_ma(closes, 7)
    ma25 = calcular_ma(closes, 25)
    rsi = calcular_rsi(closes)

    tendencia_alta = ema9 > ema21
    tendencia_baixa = ema9 < ema21
    tendencia = "alta" if tendencia_alta else "baixa"

    distancia_preco_ema9 = abs(preco - ema9) / ema9 if ema9 else 1
    distancia_ema = abs(ema9 - ema21) / ema21 if ema21 else 1
    distancia_ma7 = (preco - ma7) / ma7 if ma7 else 0
    distancia_ma25 = (preco - ma25) / ma25 if ma25 else 0

    variacao_5 = (closes[-1] - closes[-5]) / closes[-5]
    variacao_10 = (closes[-1] - closes[-10]) / closes[-10]

    corpo_candle = abs(ultimo_close - ultimo_open)
    range_candle = ultimo_high - ultimo_low

    if range_candle > 0:
        pavio_inferior = min(ultimo_open, ultimo_close) - ultimo_low
        pavio_superior = ultimo_high - max(ultimo_open, ultimo_close)
        corpo_percentual = corpo_candle / range_candle
        pavio_inferior_percentual = pavio_inferior / range_candle
        pavio_superior_percentual = pavio_superior / range_candle
    else:
        corpo_percentual = 0
        pavio_inferior_percentual = 0
        pavio_superior_percentual = 0

    candle_verde = ultimo_close > ultimo_open
    candle_estendido = (
        corpo_percentual > 0.75
        and pavio_superior_percentual > 0.20
    )

    rejeicao_pavio_comprador = (
        candle_verde
        and pavio_inferior_percentual > 0.35
        and corpo_percentual > 0.35
    )

    rompeu_maxima_anterior = ultimo_close > penultimo_high
    sequencia_fechamentos_alta = closes[-3] < closes[-2] < closes[-1]

    volume_atual = volumes[-1]
    media_volume_10 = sum(volumes[-10:]) / 10
    media_volume_30 = sum(volumes[-30:]) / 30
    volume_candle_forte = volume_atual > media_volume_10
    volume_ok = media_volume_10 >= media_volume_30 * p["volume_minimo_relativo"]

    qualidade_candle = (
        candle_verde
        and not candle_estendido
        and (
            rejeicao_pavio_comprador
            or rompeu_maxima_anterior
            or sequencia_fechamentos_alta
        )
        and volume_candle_forte
    )

    rejeicao_compra = (
        penultimo_close < ultimo_close
        and ultimo_close > ema9
        and ((ultimo_close - penultimo_close) / penultimo_close) > p["rejeicao_minima"]
    )

    # Reação atual simples: não esperamos outro candle inteiro fechar.
    # O preço atual só confirma que a correção parou e começou a reagir.
    reacao_atual = (
        preco > ultimo_close
        and preco >= ema9
    )

    # V3.1 — correção fiel ao princípio do lateral:
    # uma simples reação intrabar não aprova sozinha.
    # Se a confirmação vier apenas do preço atual, exigimos também
    # volume do último candle fechado acima da média de 10 candles.
    reacao_atual_com_volume = bool(
        reacao_atual
        and volume_candle_forte
    )

    confirmacao_compra = bool(
        rejeicao_compra
        or qualidade_candle
        or reacao_atual_com_volume
    )

    # Range macro apenas para identificar mercado morto / volatilidade mínima.
    suporte_macro = min(lows[-30:])
    resistencia_macro = max(highs[-30:])
    amplitude_macro = (
        (resistencia_macro - suporte_macro) / suporte_macro
        if suporte_macro > 0 else 0
    )
    volatilidade_minima = amplitude_macro >= p["amplitude_minima"]

    # RANGE LOCAL: mesma ideia que vem funcionando no lateral.
    janela = int(p["janela_range_local"])
    highs_local = highs[-(janela + 1):-1]
    lows_local = lows[-(janela + 1):-1]

    if not highs_local or not lows_local:
        raise ValueError("Range local insuficiente")

    suporte_local = min(lows_local)
    resistencia_local = max(highs_local)
    largura_range_local = resistencia_local - suporte_local

    if largura_range_local > 0:
        posicao_range_local = (preco - suporte_local) / largura_range_local
    else:
        posicao_range_local = 1

    entrada_na_zona_baixa = (
        posicao_range_local >= -0.05
        and posicao_range_local <= p["posicao_range_local_max"]
    )

    # Fundo recente exclui o último candle fechado, igual ao princípio do lateral.
    lows_fundo_recente = lows[-7:-1]
    fundo_recente = min(lows_fundo_recente) if lows_fundo_recente else suporte_local

    impulso_desde_fundo = (
        (preco - fundo_recente) / fundo_recente
        if fundo_recente > 0 else 1
    )
    entrada_estendida = impulso_desde_fundo > p["impulso_desde_fundo_max"]

    distancia_preco_ema9_ok = distancia_preco_ema9 <= p["distancia_preco_ema9_max"]

    alvo = preco * (1 + ALVO_EXECUTOR_PERCENTUAL)
    stop = preco * (1 - STOP_EXECUTOR_PERCENTUAL)
    stop_limit = preco * (1 - STOP_LIMIT_EXECUTOR_PERCENTUAL)

    if alvo > resistencia_local and resistencia_local > 0:
        rompimento_necessario_para_tp = (
            (alvo - resistencia_local) / resistencia_local
        )
    else:
        rompimento_necessario_para_tp = 0

    alvo_plausivel = (
        rompimento_necessario_para_tp <= p["rompimento_max_para_tp"]
    )

    espaco_resistencia_local = (
        (resistencia_local - preco) / preco
        if preco > 0 else -1
    )

    pressao_vendedora_forte = (
        ultimo_close < ultimo_open
        and corpo_percentual >= p["corpo_pressao_min"]
        and volume_atual >= (media_volume_10 * p["volume_pressao_relativo"])
    )

    veto_pressao_vendedora = (
        pressao_vendedora_forte
        and not reacao_atual
        and not rejeicao_compra
        and not qualidade_candle
    )

    # Contexto 4h não é mais um empilhamento de bloqueios isolados.
    # Vira penalidade e só se torna veto quando coincide com pressão vendedora forte.
    veto_macro = bool(
        contexto_4h.get("macro_baixista") is True
        and veto_pressao_vendedora
    )

    score_tecnico = 0
    ajustes_score = []

    if tendencia_alta:
        score_tecnico += 25
    if preco >= ema21:
        score_tecnico += 10
    if distancia_preco_ema9_ok:
        score_tecnico += 15
    if entrada_na_zona_baixa:
        score_tecnico += 20
    if not entrada_estendida:
        score_tecnico += 15
    if alvo_plausivel:
        score_tecnico += 20
    if confirmacao_compra:
        score_tecnico += 15
    if volume_ok:
        score_tecnico += 10
    if qualidade_candle:
        score_tecnico += 5
    if not contexto_4h.get("macro_baixista"):
        score_tecnico += 5

    if pressao_vendedora_forte:
        score_tecnico -= 15
        ajustes_score.append("penalidade_pressao_vendedora:-15")
    if contexto_4h.get("macro_baixista"):
        score_tecnico -= 8
        ajustes_score.append("penalidade_macro_4h:-8")
    if contexto_4h.get("perto_resistencia_4h"):
        score_tecnico -= 5
        ajustes_score.append("penalidade_resistencia_4h:-5")

    score_tecnico = max(0, min(int(score_tecnico), 100))
    score_minimo = int(p["score_minimo"])

    setup_tecnico_ok = bool(
        tendencia_alta
        and preco >= ema21
        and volatilidade_minima
        and distancia_preco_ema9_ok
        and entrada_na_zona_baixa
        and not entrada_estendida
        and alvo_plausivel
        and confirmacao_compra
        and not veto_pressao_vendedora
        and not veto_macro
        and score_tecnico >= score_minimo
    )

    motivos_bloqueio = []
    if not tendencia_alta:
        motivos_bloqueio.append("tendencia_5m_nao_alta")
    if preco < ema21:
        motivos_bloqueio.append("preco_abaixo_ema21")
    if not volatilidade_minima:
        motivos_bloqueio.append("volatilidade_insuficiente")
    if not distancia_preco_ema9_ok:
        motivos_bloqueio.append("preco_longe_ema9")
    if not entrada_na_zona_baixa:
        motivos_bloqueio.append("entrada_alta_no_range_local")
    if entrada_estendida:
        motivos_bloqueio.append("entrada_tardia_apos_subida")
    if not alvo_plausivel:
        motivos_bloqueio.append("tp_exige_rompimento_excessivo")
    if not confirmacao_compra:
        motivos_bloqueio.append("sem_reacao_compradora")
    if veto_pressao_vendedora:
        motivos_bloqueio.append("pressao_vendedora_forte")
    if veto_macro:
        motivos_bloqueio.append("macro_4h_baixista_com_pressao")
    if score_tecnico < score_minimo:
        motivos_bloqueio.append(f"score_abaixo_minimo:{score_tecnico}<{score_minimo}")

    if setup_tecnico_ok:
        grau_setup = "APROVADO"
    elif (
        tendencia_alta
        and entrada_na_zona_baixa
        and not entrada_estendida
        and alvo_plausivel
    ):
        grau_setup = "QUASE_APROVADO"
    elif score_tecnico >= 65:
        grau_setup = "PROXIMO"
    else:
        grau_setup = "FRACO"

    return {
        "ativo": symbol,
        "grupo": obter_grupo(symbol),
        "estrategia_versao": ESTRATEGIA_VERSAO,
        "estrategia_entrada": "vertical_hibrida_trend_localizacao_range",
        "setup_tecnico_ok": setup_tecnico_ok,
        "grau_setup": grau_setup,
        "score_tecnico": score_tecnico,
        "score_minimo": score_minimo,
        "motivos_bloqueio": motivos_bloqueio,

        "preco": round(preco, config["price_decimals"]),
        "preco_ultimo_fechamento_5m": ultimo_close,
        "setup_candles_fechados": True,
        "gatilho_preco_atual": True,

        "tendencia": tendencia,
        "tendencia_alta": tendencia_alta,
        "tendencia_baixa": tendencia_baixa,
        "ema9": ema9,
        "ema21": ema21,
        "distancia_ema": round(distancia_ema, 6),
        "distancia_preco_ema9": round(distancia_preco_ema9, 6),
        "distancia_preco_ema9_ok": distancia_preco_ema9_ok,
        "ma7": ma7,
        "ma25": ma25,
        "distancia_ma7": round(distancia_ma7, 6),
        "distancia_ma25": round(distancia_ma25, 6),
        "rsi": round(rsi, 2),
        "variacao_5": round(variacao_5, 6),
        "variacao_10": round(variacao_10, 6),

        "suporte_local": round(suporte_local, config["price_decimals"]),
        "resistencia_local": round(resistencia_local, config["price_decimals"]),
        "posicao_range_local": round(posicao_range_local, 6),
        "posicao_range_local_pct": round(posicao_range_local * 100, 3),
        "entrada_na_zona_baixa": entrada_na_zona_baixa,
        "fundo_recente": round(fundo_recente, config["price_decimals"]),
        "impulso_desde_fundo": round(impulso_desde_fundo, 6),
        "impulso_desde_fundo_pct": round(impulso_desde_fundo * 100, 3),
        "entrada_estendida": entrada_estendida,
        "espaco_resistencia_local": round(espaco_resistencia_local, 6),
        "rompimento_necessario_para_tp": round(rompimento_necessario_para_tp, 6),
        "rompimento_necessario_para_tp_pct": round(rompimento_necessario_para_tp * 100, 3),
        "alvo_plausivel": alvo_plausivel,

        "reacao_atual": reacao_atual,
        "reacao_atual_com_volume": reacao_atual_com_volume,
        "rejeicao_compra": rejeicao_compra,
        "rejeicao_pavio_comprador": rejeicao_pavio_comprador,
        "qualidade_candle": qualidade_candle,
        "candle_estendido": candle_estendido,
        "confirmacao_compra": confirmacao_compra,
        "volume_ok": volume_ok,
        "volume_candle_forte": volume_candle_forte,
        "pressao_vendedora_forte": pressao_vendedora_forte,
        "veto_pressao_vendedora": veto_pressao_vendedora,
        "veto_macro": veto_macro,
        "volatilidade_minima": volatilidade_minima,
        "amplitude_macro": round(amplitude_macro, 6),

        "alvo": round(alvo, config["price_decimals"]),
        "stop": round(stop, config["price_decimals"]),
        "stop_limit": round(stop_limit, config["price_decimals"]),

        "ma7_4h": contexto_4h["ma7_4h"],
        "ma25_4h": contexto_4h["ma25_4h"],
        "ma99_4h": contexto_4h["ma99_4h"],
        "macro_baixista": contexto_4h["macro_baixista"],
        "perto_resistencia_4h": contexto_4h["perto_resistencia_4h"],
        "volume_4h_fraco": contexto_4h["volume_4h_fraco"],
        "contexto_4h_candle_fechado": contexto_4h.get("contexto_4h_candle_fechado"),
        "ajustes_score": ajustes_score,
        "parametros_entrada": dict(p)
    }

# =========================
# IA
# =========================

def gerar_ia(symbol, dados=None):
    if dados is None:
        dados = gerar_analise(symbol)

    prompt = f"""
Você é um analista quantitativo profissional especializado em trading spot de continuação/pullback.

FUNÇÃO DESTE ROBÔ VERTICAL:
Comprar uma tendência de alta APÓS correção, em localização favorável, sem perseguir preço
nem depender de grande rompimento para alcançar o TP.

A lógica foi adaptada do motor lateral que prioriza localização da entrada:
- parte baixa do range local;
- pouco impulso já gasto desde o fundo;
- alvo plausível antes ou pouco além do teto local;
- reação compradora real;
- veto de pressão vendedora forte.

APROVE PRINCIPALMENTE QUANDO:
- setup_tecnico_ok = true;
- tendencia_alta = true;
- entrada_na_zona_baixa = true;
- entrada_estendida = false;
- alvo_plausivel = true;
- confirmacao_compra = true;
- não há veto_pressao_vendedora nem veto_macro.

REPROVE PRINCIPALMENTE QUANDO:
- preço já está alto no range local;
- entrada está estendida após subida;
- TP exige rompimento excessivo;
- não existe reação compradora;
- há pressão vendedora forte;
- contexto 4h baixista coincide com pressão de venda.

NÃO REPROVE AUTOMATICAMENTE APENAS POR:
- um único candle vermelho;
- volume normal isoladamente;
- existência de resistência local, se o alvo continua plausível.

IMPORTANTE:
Score não é probabilidade de WIN.
Se os dados técnicos não sustentarem a compra, reprove.

RESPONDA SOMENTE JSON PURO:
{{
  "aprovar": true ou false,
  "qualidade": número de 0 a 100,
  "tipo_mercado": "tendencia_pullback | tendencia_forte | falsa_reacao | entrada_atrasada | pressao_vendedora | indefinido",
  "risco": "baixo | medio | alto",
  "motivo": "explicação curta e técnica"
}}

DADOS:
{json.dumps(dados, ensure_ascii=False)}
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            timeout=8
        )

        texto = resposta.choices[0].message.content.strip()
        texto = texto.replace("```json", "").replace("```", "").strip()
        inicio = texto.find("{")
        fim = texto.rfind("}") + 1
        if inicio == -1 or fim <= inicio:
            raise ValueError("Resposta da IA sem JSON válido")
        ia = json.loads(texto[inicio:fim])

    except Exception as e:
        print("ERRO_IA:", str(e))
        ia = {
            "aprovar": False,
            "qualidade": 0,
            "tipo_mercado": "erro_ia",
            "risco": "alto",
            "motivo": f"IA falhou ou demorou demais: {str(e)}"
        }

    score = calcular_score(dados, ia)
    return {
        "dados": dados,
        "analise_ia": ia,
        "score": score
    }

# =========================
# ROTAS DE ANÁLISE
# =========================

@app.get("/preco/{symbol}")
def get_preco(symbol: str):
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        return {"erro": "Ativo não permitido"}

    url = f"{BINANCE_DATA_URL}/api/v3/ticker/price?symbol={symbol}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        return {
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
            "preco": data["price"]
        }

    except Exception as e:
        return {
            "ativo": symbol,
            "erro": str(e)
        }


@app.get("/analise/{symbol}")
def analise(symbol: str):
    try:
        return gerar_analise(symbol)

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }


@app.get("/ia/{symbol}")
def ia(symbol: str):
    try:
        return gerar_ia(symbol)

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }


@app.get("/ordem-preview/{symbol}")
def ordem_preview(symbol: str):
    try:
        symbol = symbol.upper()

        if symbol not in CONFIG_ATIVOS:
            return {
                "ativo": symbol,
                "pode_operar": False,
                "motivo": "Ativo não permitido"
            }

        dados = gerar_analise(symbol)
        score_tecnico = int(dados.get("score_tecnico", 0))

        # Igual ao princípio do lateral: IA só entra depois do pré-filtro técnico.
        # Isso reduz latência/custo e evita que IA tente "salvar" setup ruim.
        if not dados.get("setup_tecnico_ok"):
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": " | ".join(dados.get("motivos_bloqueio", [])) or "Setup técnico não aprovado",
                "score": score_tecnico,
                "dados": dados,
                "analise_ia": {
                    "aprovar": False,
                    "qualidade": 0,
                    "tipo_mercado": "prefiltro_tecnico",
                    "risco": "alto",
                    "motivo": "IA não chamada porque o setup técnico não passou"
                }
            }

        resultado_ia = gerar_ia(symbol, dados=dados)
        ia = resultado_ia.get("analise_ia", {})
        score = resultado_ia.get("score", score_tecnico)
        qualidade_minima = int(CONFIG_ATIVOS[symbol]["entrada"]["qualidade_ia_minima"])

        if not ia.get("aprovar"):
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": "IA não aprovou o setup técnico",
                "score": score,
                "dados": dados,
                "analise_ia": ia
            }

        if int(ia.get("qualidade", 0)) < qualidade_minima:
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": f"Qualidade IA abaixo do mínimo: {ia.get('qualidade', 0)}<{qualidade_minima}",
                "score": score,
                "dados": dados,
                "analise_ia": ia
            }

        return {
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
            "pode_operar": True,
            "direcao": "compra",
            "entrada": dados["preco"],
            "stop": dados["stop"],
            "stop_limit": dados["stop_limit"],
            "alvo": dados["alvo"],
            "score": score,
            "valor_usdt": VALOR_POR_TRADE_USDT,
            "confirmacao_necessaria": True,
            "dados": dados,
            "analise_ia": ia
        }

    except Exception as e:
        return {"erro": str(e)}

@app.get("/diagnostico/{symbol}")
def diagnostico(symbol: str):
    symbol = symbol.upper()

    try:
        dados = gerar_analise(symbol)
        ia = {
            "aprovar": False,
            "qualidade": 0,
            "tipo_mercado": "prefiltro_tecnico",
            "risco": "alto",
            "motivo": "IA não chamada porque o setup técnico não passou"
        }
        score = int(dados.get("score_tecnico", 0))

        if dados.get("setup_tecnico_ok"):
            resultado_ia = gerar_ia(symbol, dados=dados)
            ia = resultado_ia.get("analise_ia", ia)
            score = resultado_ia.get("score", score)

        qualidade_minima = int(CONFIG_ATIVOS[symbol]["entrada"]["qualidade_ia_minima"])
        pode_operar = bool(
            dados.get("setup_tecnico_ok")
            and ia.get("aprovar") is True
            and int(ia.get("qualidade", 0)) >= qualidade_minima
        )

        bloqueios = list(dados.get("motivos_bloqueio", []))
        if dados.get("setup_tecnico_ok") and ia.get("aprovar") is not True:
            bloqueios.append("ia_nao_aprovou")
        if dados.get("setup_tecnico_ok") and int(ia.get("qualidade", 0)) < qualidade_minima:
            bloqueios.append(f"qualidade_ia_baixa:{ia.get('qualidade', 0)}<{qualidade_minima}")

        return {
            "ativo": symbol,
            "estrategia_versao": ESTRATEGIA_VERSAO,
            "pode_operar": pode_operar,
            "score": score,
            "bloqueios": bloqueios,
            "dados": dados,
            "analise_ia": ia
        }

    except Exception as e:
        return {
            "ativo": symbol,
            "erro": str(e)
        }

# =========================
# EXECUÇÃO DIRETA — MANTIDA, MAS NÃO USAR COMO FLUXO PRINCIPAL
# =========================

@app.post("/executar/{symbol}")
def executar(
    symbol: str,
    confirmar: str = Query(default="NAO", description="Use SIM para executar ordem real")
):
    try:
        symbol = symbol.upper()

        if confirmar != "SIM":
            return {
                "status": "bloqueado",
                "motivo": "Confirmação ausente. Use ?confirmar=SIM para executar ordem real."
            }

        if symbol not in CONFIG_ATIVOS:
            return {
                "status": "bloqueado",
                "motivo": "Ativo não permitido"
            }

        api_key = os.getenv("BINANCE_API_KEY")
        secret = os.getenv("BINANCE_API_SECRET")

        if not api_key or not secret:
            return {"erro": "API Binance não configurada"}

        preview_1 = ordem_preview(symbol)

        if not preview_1.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": preview_1.get("motivo"),
                "preview": preview_1
            }

        time.sleep(1)

        preview_2 = ordem_preview(symbol)

        if not preview_2.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": "Cenário mudou antes da execução.",
                "preview_inicial": preview_1,
                "preview_atual": preview_2
            }

        if preview_2.get("score", 0) < 82:
            return {
                "status": "bloqueado",
                "motivo": "Score caiu antes da execução.",
                "preview": preview_2
            }

        config = CONFIG_ATIVOS[symbol]
        valor_usd = config["valor_usd"]

        headers = {
            "X-MBX-APIKEY": api_key
        }

        params_compra = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": str(valor_usd),
            "newOrderRespType": "FULL",
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }

        signed_compra = assinar_params(params_compra, secret)
        url_compra = f"{BINANCE_API_URL}/api/v3/order?{signed_compra}"

        resposta_compra = requests.post(url_compra, headers=headers, timeout=10)
        compra_json = resposta_compra.json()

        if resposta_compra.status_code >= 400:
            return {
                "status": "erro_compra",
                "resposta_binance": compra_json
            }

        executed_qty = float(compra_json.get("executedQty", 0))

        if executed_qty <= 0:
            return {
                "status": "erro_compra",
                "motivo": "Quantidade executada veio zerada"
            }

        cummulative_quote = float(compra_json.get("cummulativeQuoteQty", 0))

        if cummulative_quote > 0 and executed_qty > 0:
            preco_medio = cummulative_quote / executed_qty
        else:
            preco_medio = float(compra_json["fills"][0]["price"])

        alvo = preco_medio * (1 + ALVO_EXECUTOR_PERCENTUAL)
        stop = preco_medio * (1 - STOP_EXECUTOR_PERCENTUAL)
        stop_limit = preco_medio * (1 - STOP_LIMIT_EXECUTOR_PERCENTUAL)

        # Correção preservada também na rota direta de contingência:
        # não aplicar corte fixo de 0,5%. Descontar apenas eventual
        # comissão cobrada no próprio ativo-base.
        asset_base = symbol[:-4] if symbol.endswith("USDT") else symbol
        comissao_base = sum(
            float(fill.get("commission", 0) or 0)
            for fill in (compra_json.get("fills") or [])
            if str(fill.get("commissionAsset") or "").upper() == asset_base
        )
        quantidade_liquida = executed_qty - comissao_base

        if quantidade_liquida <= 0:
            return {
                "status": "erro_compra",
                "motivo": "Quantidade líquida inválida após comissão"
            }

        qty_oco = arredondar(quantidade_liquida, config["qty_decimals"])

        params_oco = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": qty_oco,
            "aboveType": "LIMIT_MAKER",
            "abovePrice": arredondar(alvo, config["price_decimals"]),
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": arredondar(stop, config["price_decimals"]),
            "belowPrice": arredondar(stop_limit, config["price_decimals"]),
            "belowTimeInForce": "GTC",
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }

        signed_oco = assinar_params(params_oco, secret)
        url_oco = f"{BINANCE_API_URL}/api/v3/orderList/oco?{signed_oco}"

        resposta_oco = requests.post(url_oco, headers=headers, timeout=10)
        oco_json = resposta_oco.json()

        if resposta_oco.status_code >= 400:
            return {
                "status": "compra_ok_sem_oco",
                "alerta": "Compra executada, mas OCO falhou",
                "compra": compra_json,
                "erro_oco": oco_json
            }

        return {
            "status": "executado_com_oco",
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
            "entrada": preco_medio,
            "alvo": alvo,
            "stop": stop,
            "quantidade": qty_oco,
            "valor_usdt": valor_usd,
            "compra": compra_json,
            "oco": oco_json
        }

    except Exception as e:
        return {
            "status": "erro",
            "erro": str(e)
        }


@app.get("/aprovar/{symbol}")
def aprovar(
    symbol: str,
    token: str,
    preco: float = None,
    tempo: int = None
):
    approval_token = os.getenv("APPROVAL_TOKEN")

    if token != approval_token:
        return {"status": "bloqueado", "motivo": "Token inválido"}

    return executar(symbol, confirmar="SIM")


# =========================
# TESTES E ALERTAS
# =========================

@app.get("/teste-botao")
def teste_botao():
    symbol = "BTCUSDT"
    dados = gerar_analise(symbol)
    preco_atual = dados["preco"]

    mensagem = f"""🚨 TESTE COM BOTÃO

Ativo: {symbol}
Grupo: {obter_grupo(symbol)}
Preço sinal: {preco_atual}
Valor planejado: {VALOR_POR_TRADE_USDT} USDT"""

    enviar_telegram(
        mensagem,
        symbol=symbol,
        preco=preco_atual
    )

    return {
        "status": "enviado",
        "ativo": symbol,
        "grupo": obter_grupo(symbol),
        "preco_sinal": preco_atual
    }


@app.get("/alerta-teste/{symbol}")
def alerta_teste(symbol: str):
    symbol = symbol.upper()

    preview = ordem_preview(symbol)

    if not preview.get("pode_operar"):
        return {
            "status": "sem_alerta",
            "ativo": symbol,
            "motivo": preview.get("motivo"),
            "preview": preview
        }

    mensagem = f"""🚨 OPORTUNIDADE DETECTADA

Ativo: {preview['ativo']}
Grupo: {preview['grupo']}
Direção: {preview['direcao']}
Score: {preview['score']}
Entrada: {preview['entrada']}
Stop: {preview['stop']}
Alvo: {preview['alvo']}
Valor planejado: {preview['valor_usdt']} USDT

⚠️ Sinal com validade curta. Aprove somente se fizer sentido."""

    enviar_telegram(
        mensagem,
        symbol=symbol,
        preco=preview["entrada"],
        tempo=int(time.time())
    )

    return {
        "status": "alerta_enviado",
        "ativo": symbol,
        "preview": preview
    }


# =========================
# MONITORAMENTO AUTOMÁTICO
# =========================

def monitorar_mercado():
    while True:
        try:
            for symbol in ATIVOS_MONITORADOS:
                agora = time.time()

                if symbol in ultimos_sinais:
                    if agora - ultimos_sinais[symbol] < 600:
                        continue

                preview = ordem_preview(symbol)

                if preview.get("pode_operar"):

                    dados_sinal = preview.get("dados", {})

                    registrar_evento("sinal_detectado", {
                        "symbol": symbol,
                        "grupo": preview.get("grupo"),
                        "score": preview.get("score"),
                        "entrada": preview.get("entrada"),
                        "valor_usdt": VALOR_POR_TRADE_USDT,
                        "score_tecnico": dados_sinal.get("score_tecnico"),
                        "grau_setup": dados_sinal.get("grau_setup"),
                        "posicao_range_local_pct": dados_sinal.get("posicao_range_local_pct"),
                        "impulso_desde_fundo_pct": dados_sinal.get("impulso_desde_fundo_pct"),
                        "rompimento_necessario_para_tp_pct": dados_sinal.get("rompimento_necessario_para_tp_pct"),
                        "entrada_na_zona_baixa": dados_sinal.get("entrada_na_zona_baixa"),
                        "entrada_estendida": dados_sinal.get("entrada_estendida"),
                        "alvo_plausivel": dados_sinal.get("alvo_plausivel"),
                        "confirmacao_compra": dados_sinal.get("confirmacao_compra"),
                        "reacao_atual": dados_sinal.get("reacao_atual"),
                        "pressao_vendedora_forte": dados_sinal.get("pressao_vendedora_forte")
                    })

                    mensagem = f"""🚨 OPORTUNIDADE DETECTADA

Ativo: {preview['ativo']}
Grupo: {preview['grupo']}
Direção: {preview['direcao']}
Score: {preview['score']}
Entrada: {preview['entrada']}
Stop: {preview['stop']}
Alvo: {preview['alvo']}
Valor planejado: {preview['valor_usdt']} USDT

⚠️ Sinal com validade curta. Aprove somente se fizer sentido."""

                    enviar_telegram(
                        mensagem,
                        symbol=symbol,
                        preco=preview["entrada"],
                        tempo=int(time.time())
                    )

                    ultimos_sinais[symbol] = agora

                time.sleep(3)

        except Exception as e:
            print("ERRO_MONITORAMENTO:", str(e))

        time.sleep(60)


@app.on_event("startup")
def iniciar_monitoramento():
    thread = threading.Thread(target=monitorar_mercado, daemon=True)
    thread.start()
