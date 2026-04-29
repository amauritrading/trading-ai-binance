import requests
import time

URL = "https://api.binance.com/api/v3/ticker/bookTicker"

def obter_precos():
    try:
        response = requests.get(URL, timeout=5)
        data = response.json()

        precos = {}

        for item in data:
            symbol = item["symbol"]
            bid = float(item["bidPrice"])
            ask = float(item["askPrice"])

            precos[symbol] = {
                "bid": bid,
                "ask": ask
            }

        return precos

    except Exception as e:
        print("Erro ao obter preços:", e)
        return {}

def mostrar_pares(precos):
    pares_interesse = ["BTCUSDT", "ETHUSDT", "ETHBTC"]

    print("\n--- PREÇOS ATUALIZADOS ---")

    for par in pares_interesse:
        if par in precos:
            bid = precos[par]["bid"]
            ask = precos[par]["ask"]

            print(f"{par} | BID: {bid} | ASK: {ask}")
        else:
            print(f"{par} não encontrado")

if __name__ == "__main__":
    while True:
        precos = obter_precos()
        mostrar_pares(precos)
        time.sleep(2)
