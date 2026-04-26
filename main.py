from fastapi import FastAPI
import requests
import os
from openai import OpenAI

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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


@app.get("/analise/{symbol}")
def analise(symbol: str):
    return gerar_analise(symbol)


@app.get("/ia/{symbol}")
def ia(symbol: str):
    dados = gerar_analise(symbol)

    prompt = f"""
Você é um analista profissional de trading.

Dados:
Preço: {dados['preco']}
MA7: {dados['ma7']}
MA25: {dados['ma25']}
Tendência: {dados['tendencia']}
Volume: {dados['volume']}
Força do candle: {dados['forca_candle']}

Responda em JSON:
status: operar / observar / nao_operar
direcao: compra / venda / neutro
risco: baixo / medio / alto
explicacao: curta e técnica
"""

    resposta = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )

    return {
        "dados": dados,
        "analise_ia": resposta.choices[0].message.content
    }
