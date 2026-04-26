from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/")
def home():
    return {"status": "online", "sistema": "trading-ai"}

def get_klines(symbol):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=5m&limit=50"
    response = requests.get(url)
    return response.json()

def calcular_ma(closes, periodo):
    return sum(closes[-periodo:]) / periodo

@app.get("/analise/{symbol}")
def analise(symbol: str):
    symbol = symbol.upper()

    data = get_klines(symbol)

    closes = [float(candle[4]) for candle in data]

    preco_atual = closes[-1]
    ma7 = calcular_ma(closes, 7)
    ma25 = calcular_ma(closes, 25)

    tendencia = "alta" if ma7 > ma25 else "baixa"

    return {
        "ativo": symbol,
        "preco": preco_atual,
        "ma7": ma7,
        "ma25": ma25,
        "tendencia": tendencia
    }
