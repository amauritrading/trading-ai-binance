import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN_ARBITRAGEM")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_ARBITRAGEM")

if not TELEGRAM_TOKEN:
    print("ERRO: TELEGRAM_TOKEN_ARBITRAGEM não encontrado")
    exit()

if not TELEGRAM_CHAT_ID:
    print("ERRO: TELEGRAM_CHAT_ID_ARBITRAGEM não encontrado")
    exit()

url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

payload = {
    "chat_id": TELEGRAM_CHAT_ID,
    "text": "✅ Teste Telegram Arbitragem funcionando"
}

try:
    response = requests.post(url, json=payload, timeout=10)

    print("Status code:", response.status_code)
    print("Resposta:", response.text)

    if response.status_code == 200:
        print("SUCESSO: mensagem enviada")
    else:
        print("ERRO: Telegram respondeu com falha")

except Exception as e:
    print("ERRO NA REQUEST:", str(e))
