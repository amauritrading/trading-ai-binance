from fastapi import FastAPI
import requests

app = FastAPI()

@app.get("/")
def home():
    return {"status": "online", "sistema": "trading-ai"}

@app.get("/preco/{symbol}")
def get_preco(symbol: str):
    symbol = symbol.upper()

    url = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={symbol}"

    try:
        response = requests.get(url, timeout=10)

        return {
            "ativo": symbol,
            "status_code": response.status_code,
            "resposta_binance": response.text
        }

    except Exception as e:
        return {
            "ativo": symbol,
            "erro": str(e)
        }
