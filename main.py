from fastapi import FastAPI
import requests
import os
import json
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

        if tendencia == "alta":
            if volume == "alto" and forca_candle == "forte":
                status = "operar"
                direcao = "compra"
                risco = "medio"
                explicacao = "Alta confirmada com volume e força."

            elif volume == "normal":
                status = "observar"
                direcao = "compra"
                risco = "medio"
                explicacao = "Tendência de alta sem volume forte."

            else:
                status = "nao_operar"
                direcao = "neutro"
                risco = "alto"
                explicacao = "Alta sem força suficiente."

        elif tendencia == "baixa":
            if volume == "alto" and forca_candle == "forte":
                status = "operar"
                direcao = "venda"
                risco = "medio"
                explicacao = "Baixa confirmada com volume e força."

            elif volume == "normal":
                status = "observar"
                direcao = "venda"
                risco = "medio"
                explicacao = "Tendência de baixa sem volume forte."

            else:
                status = "nao_operar"
                direcao = "neutro"
                risco = "alto"
                explicacao = "Baixa sem força suficiente."

        else:
            status = "nao_operar"
            direcao = "neutro"
            risco = "alto"
            explicacao = "Sem tendência definida."

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


@app.get("/ia/{symbol}")
def ia(symbol: str):
    try:
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

        # 🔥 limpeza forte
        texto = texto.replace("```json", "")
        texto = texto.replace("```", "")
        texto = texto.replace("\n", "")
        texto = texto.strip()

        # 🔥 extrair JSON puro
        inicio = texto.find("{")
        fim = texto.rfind("}") + 1
        texto = texto[inicio:fim]

        try:
            analise_json = json.loads(texto)
        except Exception:
            analise_json = {
                "erro": "falha ao interpretar IA",
                "resposta_bruta": texto
            }

        return {
            "dados": dados,
            "analise_ia": analise_json
        }

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }
