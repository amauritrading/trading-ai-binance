from fastapi import FastAPI
import requests

app = FastAPI()

# Rota teste
@app.get("/")
def home():
    return {"status": "online", "sistema": "trading-ai"}

# Buscar preço da Binance
@app.get("/preco/{symbol}")
def get_preco(symbol: str):
    url = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol.upper()}"
    response = requests.get(url)

    if response.status_code != 200:
        return {"erro": "falha ao buscar preço"}

    data = response.json()
    return {
        "ativo": symbol.upper(),
        "preco": data["price"]
    }
