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
# 👉 PRIMEIRO: função

def enviar_telegram(mensagem, symbol=None, preco=None, tempo=None):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    reply_markup = None

    if symbol and preco:
        if tempo is None:
            tempo = int(time.time())

        approval_url = (
            f"https://announcer-yippee-election.ngrok-free.dev/aprovar/{symbol}"
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
            f"https://announcer-yippee-election.ngrok-free.dev/aprovar/{symbol}"
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

    resposta = requests.post(url, json=payload, timeout=10)
    print("TELEGRAM_STATUS:", resposta.status_code)
    print("TELEGRAM_RESPOSTA:", resposta.text)
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


def gerar_analise(symbol):
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        raise ValueError("Ativo não permitido.")

    data = get_klines(symbol)

    closes = [float(c[4]) for c in data]
    volumes = [float(c[5]) for c in data]

    preco = closes[-1]
    ma7 = calcular_ma(closes, 7)
    ma25 = calcular_ma(closes, 25)

    tendencia = "alta" if ma7 > ma25 else "baixa"

    # 📊 RSI
    rsi = calcular_rsi(closes)

    # 📈 Variação recente
    variacao_5 = (closes[-1] - closes[-5]) / closes[-5]

    # 📉 Distância da média
    distancia_ma7 = (preco - ma7) / ma7

    # 📊 Volume
    volume_atual = volumes[-1]
    volume_medio = sum(volumes[-10:]) / 10
    volume_status = "alto" if volume_atual > volume_medio else "normal"

    # 📉 Candle atual
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

    # 📉 Sequência de candles
    ultimos = closes[-4:]
    subida_continua = ultimos[0] < ultimos[1] < ultimos[2] < ultimos[3]

    return {
        "ativo": symbol,
        "preco": preco,
        "ma7": ma7,
        "ma25": ma25,
        "tendencia": tendencia,
        "volume": volume_status,
        "forca_candle": forca_candle,
        "rsi": round(rsi, 2),
        "variacao_5": round(variacao_5, 4),
        "distancia_ma7": round(distancia_ma7, 4),
        "subida_continua": subida_continua
    }

def gerar_ia(symbol):
    dados = gerar_analise(symbol)

    prompt = f"""
    Você é um analista profissional de trading focado em operações curtas (scalp/curto prazo).

    OBJETIVO:
    Identificar oportunidades com potencial de aproximadamente +2% de movimento, com risco controlado.

    REGRAS IMPORTANTES:

    1. NÃO recomendar entrada se:
    - RSI > 70 (sobrecompra)
    - candle fraco E volume normal → evitar entrada
    - subida_continua = true (mercado esticado)
    - variacao_5 muito positiva (> 0.01) → possível topo
    - preço muito distante da MA7 (> 0.01)

    2. PRIORIDADE para compra quando:
    - tendência = alta
    - RSI entre 40 e 65
    - preço próximo da MA7
    - leve correção recente (variacao_5 negativa ou neutra)
    - volume alto OU candle forte

    3. Se houver dúvida → NÃO operar

    4. Seja conservador:
    Prefira perder uma oportunidade do que entrar errado.

    RESPONDA APENAS JSON VÁLIDO.
    SEM markdown.
    SEM uso de markdown.

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
    RSI: {dados['rsi']}
    Variação recente (5 candles): {dados['variacao_5']}
    Distância da MA7: {dados['distancia_ma7']}
    Subida contínua: {dados['subida_continua']}
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

        risco_percentual = 0.01
        alvo_percentual = 0.02

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
            return {"erro": "API Binance não configurada"}

        # 🔍 VALIDAÇÃO DUPLA (mantido)
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

        if preview_2.get("score", 0) < 60:
            return {
                "status": "bloqueado",
                "motivo": "Score caiu antes da execução.",
                "preview": preview_2
            }

        preview = preview_2
        config = CONFIG_ATIVOS[symbol]
        valor_usd = config["valor_usd"]

        headers = {
            "X-MBX-APIKEY": api_key
        }

        # 🚀 COMPRA MARKET
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

        # 🔒 PROTEÇÃO (STOP + ALVO)
        preco_medio = float(compra_json["fills"][0]["price"])

        alvo = preco_medio * 1.02   # +2%
        stop = preco_medio * 0.99   # -1%
        stop_limit = preco_medio * 0.989

        qty_oco = executed_qty * 0.995
        qty_oco = arredondar(qty_oco, config["qty_decimals"])

        # 🎯 OCO AUTOMÁTICO
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
            "entrada": preco_medio,
            "alvo": alvo,
            "stop": stop,
            "quantidade": qty_oco,
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
@app.get("/teste-botao")
def teste_botao():
    symbol = "BTCUSDT"
    dados = gerar_analise(symbol)
    preco_atual = dados["preco"]

    mensagem = f"""🚨 TESTE COM BOTÃO

Ativo: {symbol}
Preço sinal: {preco_atual}
"""

    enviar_telegram(
        mensagem,
        symbol=symbol,
        preco=preco_atual
    )

    return {
        "status": "enviado",
        "ativo": symbol,
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
Direção: {preview['direcao']}
Score: {preview['score']}
Entrada: {preview['entrada']}
Stop: {preview['stop']}
Alvo: {preview['alvo']}

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
import threading

ultimos_sinais = {}
ATIVOS_MONITORADOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


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
                        "score": preview.get("score"),
                        "entrada": preview.get("entrada")
                    })
                
                    mensagem = f"""🚨 OPORTUNIDADE DETECTADA

Ativo: {preview['ativo']}
Direção: {preview['direcao']}
Score: {preview['score']}
Entrada: {preview['entrada']}
Stop: {preview['stop']}
Alvo: {preview['alvo']}

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
