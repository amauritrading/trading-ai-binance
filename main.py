from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/")
def home():
    return {"status": "online", "sistema": "trading-ai"}

def get_klines(symbol):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=5m&limit=50"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()

def calcular_ma(closes, periodo):
    return sum(closes[-periodo:]) / periodo

def gerar_analise(symbol):
    symbol = symbol.upper()
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

@app.get("/preco/{symbol}")
def get_preco(symbol: str):
    symbol = symbol.upper()

    url = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}"

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

@app.get("/decisao/{symbol}")
def decisao(symbol: str):
    try:
        dados = gerar_analise(symbol)

        tendencia = dados["tendencia"]
        volume = dados["volume"]
        forca_candle = dados["forca_candle"]

        if tendencia == "alta" and volume == "alto" and forca_candle == "forte":
            status = "operar"
            direcao = "compra"
            risco = "medio"
            explicacao = "Tendência de alta com volume acima da média e candle forte."

        elif tendencia == "baixa" and volume == "alto" and forca_candle == "forte":
            status = "operar"
            direcao = "venda"
            risco = "medio"
            explicacao = "Tendência de baixa com volume acima da média e candle forte."

        elif forca_candle == "fraca" or volume == "normal":
            status = "observar"
            direcao = "neutro"
            risco = "medio"
            explicacao = "Existe tendência, mas ainda sem confirmação forte de volume e candle."

        else:
            status = "nao_operar"
            direcao = "neutro"
            risco = "alto"
            explicacao = "Cenário sem confluência suficiente para entrada."

        return {
            "ativo": dados["ativo"],
            "preco": dados["preco"],
            "status": status,
            "direcao": direcao,
            "risco": risco,
            "explicacao": explicacao,
            "dados_tecnicos": dados
        }

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }
