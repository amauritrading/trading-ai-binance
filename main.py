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

app = FastAPI()
@app.get("/teste-telegram")
def teste_telegram():
    enviar_telegram("🚀 Teste de mensagem do seu sistema!")
    return {"status": "mensagem enviada"}
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

BINANCE_API_URL = "https://api.binance.com"
BINANCE_DATA_URL = "https://data-api.binance.vision"

CONFIG_ATIVOS = {
    "BTCUSDT": {"valor_usd": 10, "qty_decimals": 6, "price_decimals": 2},
    "ETHUSDT": {"valor_usd": 10, "qty_decimals": 5, "price_decimals": 2},
    "SOLUSDT": {"valor_usd": 10, "qty_decimals": 3, "price_decimals": 2},
    "BNBUSDT": {"valor_usd": 10, "qty_decimals": 3, "price_decimals": 2},
}


@app.get("/")
def home():
    return {"status": "online", "sistema": "trading-ai"}


def assinar_params(params: dict, secret: str):
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


def arredondar(valor, casas):
    quant = Decimal("1") / (Decimal("10") ** casas)
    return str(Decimal(str(valor)).quantize(quant, rounding=ROUND_DOWN))


def get_klines(symbol):
    url = f"{BINANCE_DATA_URL}/api/v3/klines?symbol={symbol}&interval=5m&limit=50"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def calcular_ma(closes, periodo):
    return sum(closes[-periodo:]) / periodo


def calcular_score(dados, ia):
    score = 0

    if dados["tendencia"] == "alta" and ia.get("direcao") == "compra":
        score += 25
    elif dados["tendencia"] == "baixa" and ia.get("direcao") == "venda":
        score += 25

    if dados["volume"] == "alto":
        score += 20

    if dados["forca_candle"] == "forte":
        score += 15

    if ia.get("status") == "operar":
        score += 25

    if ia.get("direcao") != "neutro":
        score += 10

    if ia.get("risco") == "baixo":
        score += 5

    return min(score, 100)


def gerar_analise(symbol):
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        raise ValueError("Ativo não permitido. Use BTCUSDT, ETHUSDT, SOLUSDT ou BNBUSDT.")

    data = get_klines(symbol)

    closes = [float(candle[4]) for candle in data]
    volumes = [float(candle[5]) for candle in data]

    preco_atual = closes[-1]
    ma7 = calcular_ma(closes, 7)
    ma25 = calcular_ma(closes, 25)

    tendencia = "alta" if ma7 > ma25 else "baixa"

    volume_atual = volumes[-1]
    volume_medio = sum(volumes[-10:]) / 10
    volume_status = "alto" if volume_atual > volume_medio else "normal"

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

    return {
        "ativo": symbol,
        "preco": preco_atual,
        "ma7": ma7,
        "ma25": ma25,
        "tendencia": tendencia,
        "volume": volume_status,
        "forca_candle": forca_candle
    }


def gerar_ia(symbol):
    dados = gerar_analise(symbol)

    prompt = f"""
Você é um analista profissional de trading.

RESPONDA APENAS JSON VÁLIDO.
SEM markdown.
SEM ```.

Formato obrigatório:
{{
  "status": "operar | observar | nao_operar",
  "direcao": "compra | venda | neutro",
  "risco": "baixo | medio | alto",
  "explicacao": "curta e técnica"
}}

Dados:
Ativo: {dados['ativo']}
Preço: {dados['preco']}
MA7: {dados['ma7']}
MA25: {dados['ma25']}
Tendência: {dados['tendencia']}
Volume: {dados['volume']}
Força do candle: {dados['forca_candle']}
"""

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

    try:
        analise_json = json.loads(texto)
    except Exception:
        analise_json = {
            "erro": "falha ao interpretar IA",
            "resposta_bruta": texto,
            "status": "observar",
            "direcao": "neutro",
            "risco": "alto",
            "explicacao": "A IA respondeu fora do formato esperado."
        }

    score = calcular_score(dados, analise_json)

    return {
        "dados": dados,
        "analise_ia": analise_json,
        "score": score
    }


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
        direcao = ia.get("direcao")
        status = ia.get("status")

        if status != "operar" or score < 60:
            return {
                "ativo": symbol,
                "pode_operar": False,
                "motivo": "Score baixo ou IA não validou entrada",
                "status": status,
                "direcao": direcao,
                "score": score
            }

        # Spot simples: só compra. Venda/short fica bloqueado.
        if direcao != "compra":
            return {
                "ativo": symbol,
                "pode_operar": False,
                "motivo": "Spot assistido só permite compra com OCO de venda. Venda/short exige Futures ou Margin.",
                "status": status,
                "direcao": direcao,
                "score": score
            }

        risco_percentual = 0.003
        alvo_percentual = 0.006

        entrada = preco
        stop = preco * (1 - risco_percentual)
        stop_limit = preco * (1 - risco_percentual - 0.001)
        alvo = preco * (1 + alvo_percentual)

        return {
            "ativo": symbol,
            "pode_operar": True,
            "direcao": direcao,
            "entrada": round(entrada, 2),
            "stop": round(stop, 2),
            "stop_limit": round(stop_limit, 2),
            "alvo": round(alvo, 2),
            "risco_percentual": risco_percentual,
            "alvo_percentual": alvo_percentual,
            "score": score,
            "confirmacao_necessaria": True
        }

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }


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
            return {"erro": "API Binance não configurada no Railway"}

        # 1) PRIMEIRA VALIDAÇÃO
        preview_1 = ordem_preview(symbol)

        if not preview_1.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": preview_1.get("motivo"),
                "preview": preview_1
            }

        # 2) REVALIDAÇÃO IMEDIATA ANTES DA COMPRA
        time.sleep(1)

        preview_2 = ordem_preview(symbol)

        if not preview_2.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": "Cenário mudou antes da execução. Compra cancelada.",
                "preview_inicial": preview_1,
                "preview_atual": preview_2
            }

        if preview_1.get("direcao") != preview_2.get("direcao"):
            return {
                "status": "bloqueado",
                "motivo": "Direção mudou antes da execução. Compra cancelada.",
                "preview_inicial": preview_1,
                "preview_atual": preview_2
            }

        if preview_2.get("score", 0) < 60:
            return {
                "status": "bloqueado",
                "motivo": "Score caiu antes da execução. Compra cancelada.",
                "preview_inicial": preview_1,
                "preview_atual": preview_2
            }

        preview = preview_2

        config = CONFIG_ATIVOS[symbol]
        valor_usd = config["valor_usd"]

        headers = {
            "X-MBX-APIKEY": api_key
        }

        # 3) COMPRA MARKET usando quoteOrderQty = USDT fixo
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
                "resposta_binance": compra_json,
                "preview": preview
            }

        executed_qty = float(compra_json.get("executedQty", 0))

        if executed_qty <= 0:
            return {
                "status": "erro_compra",
                "motivo": "Quantidade executada veio zerada",
                "resposta_binance": compra_json,
                "preview": preview
            }

        # reduz levemente para evitar erro caso taxa tenha sido cobrada no ativo comprado
        qty_oco = executed_qty * 0.995
        qty_oco = arredondar(qty_oco, config["qty_decimals"])

        alvo = arredondar(preview["alvo"], config["price_decimals"])
        stop = arredondar(preview["stop"], config["price_decimals"])
        stop_limit = arredondar(preview["stop_limit"], config["price_decimals"])

        # 4) OCO DE VENDA: alvo + stop
        params_oco = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": qty_oco,
            "aboveType": "LIMIT_MAKER",
            "abovePrice": alvo,
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": stop,
            "belowPrice": stop_limit,
            "belowTimeInForce": "GTC",
            "newOrderRespType": "RESULT",
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }

        signed_oco = assinar_params(params_oco, secret)
        url_oco = f"{BINANCE_API_URL}/api/v3/orderList/oco?{signed_oco}"

        resposta_oco = requests.post(url_oco, headers=headers, timeout=10)
        oco_json = resposta_oco.json()

        if resposta_oco.status_code >= 400:
            return {
                "status": "compra_executada_mas_oco_falhou",
                "alerta": "A compra foi feita, mas o OCO falhou. Verifique a Binance manualmente.",
                "compra": compra_json,
                "erro_oco": oco_json,
                "preview": preview
            }

        return {
            "status": "executado_com_oco",
            "ativo": symbol,
            "valor_usd": valor_usd,
            "quantidade_comprada": compra_json.get("executedQty"),
            "quantidade_oco": qty_oco,
            "alvo": alvo,
            "stop": stop,
            "stop_limit": stop_limit,
            "compra": compra_json,
            "oco": oco_json,
            "preview": preview
        }

    except Exception as e:
        return {
            "status": "erro",
            "erro": str(e)
        }
import requests
import os


