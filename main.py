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

        texto = texto.replace("```json", "")
        texto = texto.replace("```", "")
        texto = texto.replace("\n", "")
        texto = texto.strip()

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

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }
@app.get("/sinal/{symbol}")
def sinal(symbol: str):
    try:
        # chama sua própria rota IA internamente
        url = f"http://localhost:8000/ia/{symbol}"
        
        # ⚠️ IMPORTANTE: no Railway usar domínio público
        # substitua por:
        # url = f"https://trading-ai-binance-production.up.railway.app/ia/{symbol}"

        response = requests.get(url, timeout=10)
        data = response.json()

        ia = data.get("analise_ia", {})
        score = data.get("score", 0)

        status = ia.get("status", "observar")
        direcao = ia.get("direcao", "neutro")

        # 🎯 LÓGICA DE COR (visual)
        if score >= 80:
            cor = "verde_forte"
            mensagem = "Entrada forte confirmada"
        elif score >= 60:
            cor = "verde"
            mensagem = "Boa oportunidade"
        elif score >= 40:
            cor = "amarelo"
            mensagem = "Observar mercado"
        else:
            cor = "vermelho"
            mensagem = "Evitar operação"

        return {
            "ativo": symbol.upper(),
            "status": status,
            "direcao": direcao,
            "score": score,
            "cor": cor,
            "mensagem": mensagem
        }

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }
@app.get("/simular/{symbol}")
def simular(symbol: str):
    try:
        url = f"https://trading-ai-binance-production.up.railway.app/ia/{symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()

        dados = data.get("dados", {})
        ia = data.get("analise_ia", {})
        score = data.get("score", 0)

        preco = dados.get("preco")
        direcao = ia.get("direcao")
        status = ia.get("status")

        if status != "operar":
            return {
                "ativo": symbol.upper(),
                "acao": "sem_operacao",
                "motivo": "IA não indicou entrada",
                "score": score
            }

        # 🎯 Lógica simples de trade
        risco_percentual = 0.003  # 0.3%
        alvo_percentual = 0.006   # 0.6%

        if direcao == "compra":
            entrada = preco
            stop = preco * (1 - risco_percentual)
            alvo = preco * (1 + alvo_percentual)

        elif direcao == "venda":
            entrada = preco
            stop = preco * (1 + risco_percentual)
            alvo = preco * (1 - alvo_percentual)

        else:
            return {
                "ativo": symbol.upper(),
                "acao": "indefinido",
                "score": score
            }

        return {
            "ativo": symbol.upper(),
            "acao": "simulado",
            "direcao": direcao,
            "entrada": round(entrada, 2),
            "stop": round(stop, 2),
            "alvo": round(alvo, 2),
            "risco_percentual": risco_percentual,
            "alvo_percentual": alvo_percentual,
            "score": score
        }

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }
@app.get("/ordem-preview/{symbol}")
def ordem_preview(symbol: str):
    try:
        url = f"https://trading-ai-binance-production.up.railway.app/ia/{symbol}"
        response = requests.get(url, timeout=10)
        data = response.json()

        dados = data.get("dados", {})
        ia = data.get("analise_ia", {})
        score = data.get("score", 0)

        preco = dados.get("preco")
        direcao = ia.get("direcao")
        status = ia.get("status")

        # 🚫 TRAVA PRINCIPAL
        if status != "operar" or score < 60:
            return {
                "ativo": symbol.upper(),
                "pode_operar": False,
                "motivo": "Score baixo ou IA não validou entrada",
                "score": score
            }

        # 🎯 RISCO CONTROLADO
        risco_percentual = 0.003
        alvo_percentual = 0.006

        if direcao == "compra":
            entrada = preco
            stop = preco * (1 - risco_percentual)
            alvo = preco * (1 + alvo_percentual)

        elif direcao == "venda":
            entrada = preco
            stop = preco * (1 + risco_percentual)
            alvo = preco * (1 - alvo_percentual)

        else:
            return {
                "ativo": symbol.upper(),
                "pode_operar": False,
                "motivo": "Direção indefinida",
                "score": score
            }

        return {
            "ativo": symbol.upper(),
            "pode_operar": True,
            "direcao": direcao,
            "entrada": round(entrada, 2),
            "stop": round(stop, 2),
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
import hmac
import hashlib
import time

@app.post("/executar/{symbol}")
def executar(symbol: str):
    try:
        url_preview = f"https://trading-ai-binance-production.up.railway.app/ordem-preview/{symbol}"
        preview = requests.get(url_preview).json()

        if not preview.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": preview.get("motivo")
            }

        api_key = os.getenv("BINANCE_API_KEY")
        secret = os.getenv("BINANCE_API_SECRET")

        if not api_key or not secret:
            return {"erro": "API não configurada"}

        symbol = symbol.upper()
        side = "BUY" if preview["direcao"] == "compra" else "SELL"

        preco = preview["entrada"]

valor_usd = 10  # 🔥 valor fixo por trade (seguro)

quantity = round(valor_usd / preco, 6)

        timestamp = int(time.time() * 1000)

        params = f"symbol={symbol}&side={side}&type=MARKET&quantity={quantity}&timestamp={timestamp}"

        signature = hmac.new(
            secret.encode(),
            params.encode(),
            hashlib.sha256
        ).hexdigest()

        url = f"https://api.binance.com/api/v3/order?{params}&signature={signature}"

        headers = {
            "X-MBX-APIKEY": api_key
        }

        response = requests.post(url, headers=headers)

        return {
            "status": "executado",
            "resposta_binance": response.json()
        }

    except Exception as e:
        return {
            "erro": str(e)
        }
