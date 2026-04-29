import requests
import time

URL = "https://api.binance.com/api/v3/ticker/bookTicker"

TAXA = 0.001  # 0.1%

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

def calcular_arbitragem(precos):
    try:
        usdt_inicial = 10

        ask_ethusdt = precos["ETHUSDT"]["ask"]
        bid_ethbtc = precos["ETHBTC"]["bid"]
        bid_btcusdt = precos["BTCUSDT"]["bid"]

        # 1. USDT -> ETH
        eth = usdt_inicial / ask_ethusdt
        eth *= (1 - TAXA)

        # 2. ETH -> BTC
        btc = eth * bid_ethbtc
        btc *= (1 - TAXA)

        # 3. BTC -> USDT
        usdt_final = btc * bid_btcusdt
        usdt_final *= (1 - TAXA)

        lucro = usdt_final - usdt_inicial
        perc = (lucro / usdt_inicial) * 100

        print("\n--- ARBITRAGEM ---")
        print(f"Inicial: {usdt_inicial:.2f} USDT")
        print(f"Final: {usdt_final:.4f} USDT")
        print(f"Lucro: {lucro:.4f} USDT ({perc:.2f}%)")

    except Exception as e:
        print("Erro na arbitragem:", e)

if __name__ == "__main__":
    while True:
        precos = obter_precos()
        calcular_arbitragem(precos)
        time.sleep(2)
