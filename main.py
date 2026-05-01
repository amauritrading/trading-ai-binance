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

VALOR_POR_TRADE_USDT = 50

EXECUTOR_BASE_URL = os.getenv(
    "EXECUTOR_BASE_URL",
    "https://announcer-yippee-election.ngrok-free.dev"
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CONFIG_ATIVOS = {
    "BTCUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 6,
        "price_decimals": 2,
        "grupo": "CORE"
    },
    "ETHUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 5,
        "price_decimals": 2,
        "grupo": "CORE"
    },
    "XRPUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 1,
        "price_decimals": 4,
        "grupo": "ALT"
    },
    "LINKUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 2,
        "price_decimals": 2,
        "grupo": "ALT"
    },
}

GRUPOS = {
    "CORE": ["BTCUSDT", "ETHUSDT"],
    "ALT": ["XRPUSDT", "LINKUSDT"]
}

ATIVOS_MONITORADOS = ["BTCUSDT", "ETHUSDT", "XRPUSDT", "LINKUSDT"]

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
        "valor_por_trade_usdt": VALOR_POR_TRADE_USDT
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

def get_klines(symbol):
    url = f"{BINANCE_DATA_URL}/api/v3/klines?symbol={symbol}&interval=5m&limit=50"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


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


def calcular_score(dados, ia):
    score = 0

    if ia.get("status") != "operar":
        return 0

    if ia.get("direcao") != "compra":
        return 0

    if dados["tendencia"] == "alta":
        score += 20

    if dados["volume"] == "alto":
        score += 15

    if dados["forca_candle"] == "forte":
        score += 10

    if 40 <= dados["rsi"] <= 60:
        score += 15
    elif 60 < dados["rsi"] <= 65:
        score += 5

    if -0.004 <= dados["variacao_5"] <= 0.006:
        score += 15

    if abs(dados["distancia_ma7"]) <= 0.006:
        score += 10

    if dados["mercado_lateral"] is False:
        score += 10

    if dados["entrada_estendida"] is False:
        score += 10

    if ia.get("risco") == "baixo":
        score += 5

    return min(score, 100)


# =========================
# ANÁLISE TÉCNICA
# =========================

def gerar_analise(symbol):
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        raise ValueError("Ativo não permitido.")

    data = get_klines(symbol)

    closes = [float(c[4]) for c in data]
    highs = [float(c[2]) for c in data]
    lows = [float(c[3]) for c in data]
    volumes = [float(c[5]) for c in data]

    preco = closes[-1]
    ma7 = calcular_ma(closes, 7)
    ma25 = calcular_ma(closes, 25)

    tendencia = "alta" if ma7 > ma25 else "baixa"

    rsi = calcular_rsi(closes)

    variacao_5 = (closes[-1] - closes[-5]) / closes[-5]
    variacao_10 = (closes[-1] - closes[-10]) / closes[-10]

    distancia_ma7 = (preco - ma7) / ma7
    distancia_ma25 = (preco - ma25) / ma25

    volume_atual = volumes[-1]
    volume_medio = sum(volumes[-10:]) / 10
    volume_status = "alto" if volume_atual > volume_medio * 1.15 else "normal"

    ultima = data[-1]
    abertura = float(ultima[1])
    fechamento = float(ultima[4])
    maxima = float(ultima[2])
    minima = float(ultima[3])

    corpo = abs(fechamento - abertura)
    range_total = maxima - minima

    if range_total == 0:
        forca_candle = "indefinida"
    else:
        forca_candle = "forte" if corpo > (range_total * 0.6) else "fraca"

    ultimos = closes[-4:]
    subida_continua = ultimos[0] < ultimos[1] < ultimos[2] < ultimos[3]

    range_10 = max(highs[-10:]) - min(lows[-10:])
    range_percentual = range_10 / preco if preco else 0

    mercado_lateral = range_percentual < 0.006

    entrada_estendida = (
        variacao_5 > 0.008 or
        distancia_ma7 > 0.008 or
        rsi > 65 or
        subida_continua is True
    )

    suporte_curto = min(lows[-10:])
    resistencia_curta = max(highs[-10:])

    distancia_resistencia = (resistencia_curta - preco) / preco
    distancia_suporte = (preco - suporte_curto) / preco

    espaco_ate_alvo = distancia_resistencia >= 0.006

    return {
        "ativo": symbol,
        "grupo": obter_grupo(symbol),
        "preco": preco,
        "ma7": ma7,
        "ma25": ma25,
        "tendencia": tendencia,
        "volume": volume_status,
        "forca_candle": forca_candle,
        "rsi": round(rsi, 2),
        "variacao_5": round(variacao_5, 4),
        "variacao_10": round(variacao_10, 4),
        "distancia_ma7": round(distancia_ma7, 4),
        "distancia_ma25": round(distancia_ma25, 4),
        "subida_continua": subida_continua,
        "mercado_lateral": mercado_lateral,
        "entrada_estendida": entrada_estendida,
        "suporte_curto": round(suporte_curto, CONFIG_ATIVOS[symbol]["price_decimals"]),
        "resistencia_curta": round(resistencia_curta, CONFIG_ATIVOS[symbol]["price_decimals"]),
        "distancia_resistencia": round(distancia_resistencia, 4),
        "distancia_suporte": round(distancia_suporte, 4),
        "espaco_ate_alvo": espaco_ate_alvo
    }


# =========================
# IA
# =========================

def gerar_ia(symbol):
    dados = gerar_analise(symbol)

    prompt = f"""
Você é um analista quantitativo profissional de trading em criptomoedas spot, curto prazo.

OBJETIVO:
Identificar oportunidades de scalp com alvo curto aproximado de +1% e risco controlado de -0,6%.

CONTEXTO DO SISTEMA:
- O sistema opera com dinheiro real.
- Entrada final é manual via aprovação no Telegram.
- Execução real é feita por executor local.
- O robô permite no máximo 2 trades simultâneos.
- Grupos:
  CORE = BTCUSDT, ETHUSDT
  ALT = XRPUSDT, LINKUSDT
- A análise deste ativo deve ser individual, mas extremamente conservadora.
- Não buscar quantidade de sinais. Buscar qualidade.

REGRAS DE BLOQUEIO — NÃO OPERAR:

- RSI > 65
- RSI < 38
- candle fraco E volume normal
- subida_continua = true
- variacao_5 > 0.008
- distancia_ma7 > 0.008
- tendência de baixa
- tendência indefinida ou lateral
- preço muito esticado em relação à MA7
- movimento já realizado antes da entrada

REGRAS PARA OPERAR:

- tendência = alta
- RSI preferencialmente entre 40 e 60
- preço próximo da MA7
- variação recente neutra ou levemente negativa
- volume alto OU candle forte
- entrada com espaço real até o alvo de +1%
- risco de reversão baixo

REGRA CRÍTICA:
Se não houver clareza, responda nao_operar.

FORMATO DE RESPOSTA:
Responda somente JSON puro, sem markdown:

{{
"status": "operar | observar | nao_operar",
"direcao": "compra | neutro",
"risco": "baixo | medio | alto",
"qualidade": "alta | media | baixa",
"explicacao": "curta e técnica"
}}

Dados:
Ativo: {dados['ativo']}
Grupo: {dados['grupo']}
Preço: {dados['preco']}
MA7: {dados['ma7']}
MA25: {dados['ma25']}
Tendência: {dados['tendencia']}
Volume: {dados['volume']}
Força do candle: {dados['forca_candle']}
RSI: {dados['rsi']}
Variação recente: {dados['variacao_5']}
Distância da MA7: {dados['distancia_ma7']}
Subida contínua: {dados['subida_continua']}
"""

    try:
        resposta = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )

        texto = resposta.choices[0].message.content.strip()
        texto = texto.replace("```json", "").replace("```", "").replace("\n", "").strip()

        inicio = texto.find("{")
        fim = texto.rfind("}") + 1
        texto = texto[inicio:fim]

        analise_json = json.loads(texto)

    except Exception as e:
        print("ERRO_IA:", str(e))
        analise_json = {
            "status": "nao_operar",
            "direcao": "neutro",
            "risco": "alto",
            "qualidade": "baixa",
            "explicacao": "Erro na leitura da IA"
        }

    score = calcular_score(dados, analise_json)

    return {
        "dados": dados,
        "analise_ia": analise_json,
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

        data = gerar_ia(symbol)

        dados = data.get("dados", {})
        ia = data.get("analise_ia", {})
        score = data.get("score", 0)

        preco = dados.get("preco")

        if ia.get("status") != "operar":
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": "IA não validou entrada",
                "score": score,
                "analise_ia": ia
            }

        if ia.get("direcao") != "compra":
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": "IA não indicou compra",
                "score": score,
                "analise_ia": ia
            }

        if score < 85:
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": "Score baixo",
                "score": score,
                "analise_ia": ia
            }

        alvo_percentual = 0.01
        risco_percentual = 0.006

        entrada = preco
        stop = preco * (1 - risco_percentual)
        stop_limit = preco * (1 - risco_percentual - 0.001)
        alvo = preco * (1 + alvo_percentual)

        return {
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
            "pode_operar": True,
            "direcao": "compra",
            "entrada": round(entrada, CONFIG_ATIVOS[symbol]["price_decimals"]),
            "stop": round(stop, CONFIG_ATIVOS[symbol]["price_decimals"]),
            "stop_limit": round(stop_limit, CONFIG_ATIVOS[symbol]["price_decimals"]),
            "alvo": round(alvo, CONFIG_ATIVOS[symbol]["price_decimals"]),
            "score": score,
            "valor_usdt": VALOR_POR_TRADE_USDT,
            "confirmacao_necessaria": True,
            "analise_ia": ia
        }

    except Exception as e:
        return {"erro": str(e)}


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

        if preview_2.get("score", 0) < 85:
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

        preco_medio = float(compra_json["fills"][0]["price"])

        alvo = preco_medio * 1.01
        stop = preco_medio * 0.994
        stop_limit = preco_medio * 0.993

        qty_oco = executed_qty * 0.995
        qty_oco = arredondar(qty_oco, config["qty_decimals"])

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

                    registrar_evento("sinal_detectado", {
                        "symbol": symbol,
                        "grupo": preview.get("grupo"),
                        "score": preview.get("score"),
                        "entrada": preview.get("entrada"),
                        "valor_usdt": VALOR_POR_TRADE_USDT
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
