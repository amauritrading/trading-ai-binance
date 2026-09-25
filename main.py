# DEPLOY_MARKER_VERTICAL_HIBRIDO_LATERAL_V3_20260920
from fastapi import FastAPI, Query
import requests
import os
import json
import time
import hmac
import hashlib
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode
from openai import OpenAI
import threading

app = FastAPI()

# =========================
# CONFIGURAÇÕES GERAIS
# =========================

BINANCE_API_URL = "https://api.binance.com"
BINANCE_DATA_URL = "https://data-api.binance.vision"

VALOR_POR_TRADE_USDT = 10
ESTRATEGIA_VERSAO = "vertical_adaptativo_v1_btc_eth_20260925"

# Parâmetros reais do executor local principal (porta 8001).
# Mantidos centralizados para evitar divergência entre preview, mensagem e execução.
ALVO_EXECUTOR_PERCENTUAL = 0.007       # +0,70%
STOP_EXECUTOR_PERCENTUAL = 0.0055      # -0,55%
STOP_LIMIT_EXECUTOR_PERCENTUAL = 0.0065  # -0,65%

EXECUTOR_BASE_URL = os.getenv(
    "EXECUTOR_BASE_URL",
    "https://trader-jundiai.ngrok.app"
).rstrip("/")

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============================================================
# CONFIGURAÇÃO DOS ATIVOS
#
# Motor vertical adaptado da arquitetura Híbrido V2 do lateral.
# Compartilha a lógica de localização/anti-atraso/alvo plausível,
# mas mantém tendência de alta como identidade do vertical.
# ============================================================

CONFIG_ATIVOS = {
    "BTCUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 6,
        "price_decimals": 2,
        "grupo": "CORE",
        "entrada": {
            # Base adaptada do lateral: localização + anti-atraso + alvo plausível.
            # BTC recebe a faixa mais curta por ter menor ruído percentual.
            "janela_range_local": 12,
            "posicao_range_local_max": 0.55,
            "impulso_desde_fundo_max": 0.0055,
            "rompimento_max_para_tp": 0.0025,
            "distancia_preco_ema9_max": 0.0047,
            "rejeicao_minima": 0.0010,
            "volume_minimo_relativo": 0.88,
            "score_minimo": 80,
            "qualidade_ia_minima": 68,
            "corpo_pressao_min": 0.62,
            "volume_pressao_relativo": 1.20,
            "amplitude_minima": 0.0055
        }
    },
    "ETHUSDT": {
        "valor_usd": VALOR_POR_TRADE_USDT,
        "qty_decimals": 5,
        "price_decimals": 2,
        "grupo": "CORE",
        "entrada": {
            "janela_range_local": 12,
            "posicao_range_local_max": 0.52,
            "impulso_desde_fundo_max": 0.0065,
            "rompimento_max_para_tp": 0.0030,
            "distancia_preco_ema9_max": 0.0050,
            "rejeicao_minima": 0.0011,
            "volume_minimo_relativo": 0.90,
            "score_minimo": 80,
            "qualidade_ia_minima": 70,
            "corpo_pressao_min": 0.60,
            "volume_pressao_relativo": 1.20,
            "amplitude_minima": 0.0060
        }
    }
}

GRUPOS = {
    "CORE": ["BTCUSDT", "ETHUSDT"],
    "ALT": []
}

ATIVOS_MONITORADOS = ["BTCUSDT", "ETHUSDT"]

ultimos_sinais = {}

# =========================
# TELEGRAM
# =========================

def enviar_telegram(mensagem, symbol=None, preco=None, tempo=None):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("TELEGRAM_NAO_CONFIGURADO")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    reply_markup = None

    if symbol and preco:
        if tempo is None:
            tempo = int(time.time())

        approval_url = (
            f"{EXECUTOR_BASE_URL}/aprovar/{symbol}"
            f"?token={os.getenv('APPROVAL_TOKEN')}"
            f"&preco={preco}"
            f"&tempo={tempo}"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprovar compra", "url": approval_url}
                ]
            ]
        }

    elif symbol:
        approval_url = (
            f"{EXECUTOR_BASE_URL}/aprovar/{symbol}"
            f"?token={os.getenv('APPROVAL_TOKEN')}"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Aprovar compra", "url": approval_url}
                ]
            ]
        }

    payload = {
        "chat_id": chat_id,
        "text": mensagem
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resposta = requests.post(url, json=payload, timeout=10)
        print("TELEGRAM_STATUS:", resposta.status_code)
        print("TELEGRAM_RESPOSTA:", resposta.text)
    except Exception as e:
        print("ERRO_TELEGRAM:", str(e))


@app.get("/teste-telegram")
def teste_telegram():
    enviar_telegram("🚀 Teste de mensagem do sistema Railway!")
    return {"status": "mensagem enviada"}


# =========================
# ROTAS BASE
# =========================

@app.get("/")
def home():
    return {
        "status": "online",
        "sistema": "trading-ai",
        "ativos_monitorados": ATIVOS_MONITORADOS,
        "valor_por_trade_usdt": VALOR_POR_TRADE_USDT,
        "estrategia_versao": ESTRATEGIA_VERSAO,
        "aprendizado_adaptativo": APRENDIZADO_ATIVO,
        "modelo_ia": OPENAI_MODEL
    }



# ============================================================
# APRENDIZADO ADAPTATIVO - V1
# ============================================================
# O Railway NÃO reescreve o próprio código-fonte.
# A cada 10 trades fechados, a IA pode propor no máximo 1 microajuste
# de entrada por ativo. O executor local persiste e versiona esses
# overrides em vertical_learning_state.json.
#
# Se o executor/ngrok ou a camada de aprendizado falhar, o robô
# continua usando o baseline abaixo. A falha do aprendizado nunca
# derruba o monitor de mercado.
APRENDIZADO_ATIVO = True
APRENDIZADO_CACHE_TTL = 60
APRENDIZADO_TIMEOUT = 1.0
APRENDIZADO_CACHE = {}
APRENDIZADO_CACHE_TS = {}
APRENDIZADO_CACHE_LOCK = threading.Lock()

PARAMETROS_ADAPTAVEIS = {
    "posicao_range_local_max",
    "impulso_desde_fundo_max",
    "rompimento_max_para_tp",
    "distancia_preco_ema9_max",
    "rejeicao_minima",
    "volume_minimo_relativo",
}

LIMITES_ADAPTATIVOS = {
    "posicao_range_local_max": (0.35, 0.60),
    "impulso_desde_fundo_max": (0.0040, 0.0090),
    "rompimento_max_para_tp": (0.0015, 0.0045),
    "distancia_preco_ema9_max": (0.0035, 0.0065),
    "rejeicao_minima": (0.0007, 0.0018),
    "volume_minimo_relativo": (0.80, 1.05),
}


def _token_executor():
    return os.getenv("APPROVAL_TOKEN", "")


def obter_contexto_aprendizado(symbol, forcar=False):
    """
    Busca overrides e métricas aprendidas no executor local.
    Fail-open para o baseline: se a consulta falhar, retorna {}.
    """
    symbol = symbol.upper()

    if not APRENDIZADO_ATIVO or symbol not in CONFIG_ATIVOS:
        return {}

    agora = time.time()
    with APRENDIZADO_CACHE_LOCK:
        if (
            not forcar
            and symbol in APRENDIZADO_CACHE
            and (agora - APRENDIZADO_CACHE_TS.get(symbol, 0)) < APRENDIZADO_CACHE_TTL
        ):
            return dict(APRENDIZADO_CACHE[symbol])

    token = _token_executor()
    if not token:
        return {}

    try:
        r = requests.get(
            f"{EXECUTOR_BASE_URL}/aprendizado-contexto/{symbol}",
            params={"token": token},
            timeout=APRENDIZADO_TIMEOUT
        )
        if r.status_code >= 400:
            return {}

        dados = r.json()
        if not isinstance(dados, dict) or dados.get("status") == "bloqueado":
            return {}

        with APRENDIZADO_CACHE_LOCK:
            APRENDIZADO_CACHE[symbol] = dados
            APRENDIZADO_CACHE_TS[symbol] = agora

        return dict(dados)
    except Exception as e:
        print("APRENDIZADO_CONTEXTO_INDISPONIVEL", symbol, str(e))
        return {}


def parametros_entrada_efetivos(symbol):
    """
    Retorna cópia do baseline + overrides aprovados/versionados.
    Nunca altera CONFIG_ATIVOS em memória.
    """
    base = dict(CONFIG_ATIVOS[symbol]["entrada"])
    contexto = obter_contexto_aprendizado(symbol)
    overrides = contexto.get("active_overrides") if isinstance(contexto, dict) else {}

    if not isinstance(overrides, dict):
        return base, contexto

    for chave, valor in overrides.items():
        if chave not in PARAMETROS_ADAPTAVEIS:
            continue
        try:
            valor = float(valor)
            minimo, maximo = LIMITES_ADAPTATIVOS[chave]
            if minimo <= valor <= maximo:
                base[chave] = valor
        except Exception:
            continue

    return base, contexto


def calcular_contexto_1m(symbol):
    """
    Microcontexto apenas para TIMING da IA.
    Não vira filtro duro e não substitui o setup 5m.
    Se falhar, retorna contexto indisponível sem bloquear o robô.
    """
    try:
        bruto = get_klines(symbol, interval="1m", limit=24)
        data = bruto[:-1]
        if len(data) < 12:
            raise ValueError("historico_1m_insuficiente")

        closes = [float(c[4]) for c in data]
        highs = [float(c[2]) for c in data]
        lows = [float(c[3]) for c in data]
        volumes = [float(c[5]) for c in data]

        ema5 = calcular_ema(closes[-12:], 5)
        ema9 = calcular_ema(closes[-16:], 9)
        ultimo_close = closes[-1]
        penultimo_close = closes[-2]
        ultimo_open = float(data[-1][1])

        janela = 10
        suporte = min(lows[-janela:])
        resistencia = max(highs[-janela:])
        largura = resistencia - suporte
        posicao_range = (
            (ultimo_close - suporte) / largura
            if largura > 0 else 1.0
        )

        variacao_3m = (
            (closes[-1] - closes[-4]) / closes[-4]
            if len(closes) >= 4 and closes[-4] else 0.0
        )
        volume_medio_10 = sum(volumes[-10:]) / 10
        volume_relativo = (
            volumes[-1] / volume_medio_10
            if volume_medio_10 > 0 else 0.0
        )

        retomada_micro = bool(
            ultimo_close > penultimo_close
            and ultimo_close >= ema5
            and ema5 >= ema9
        )

        entrada_micro_estendida = bool(
            posicao_range > 0.85
            and variacao_3m > 0.0018
        )

        return {
            "disponivel": True,
            "ema5_1m": round(ema5, 8),
            "ema9_1m": round(ema9, 8),
            "tendencia_micro_alta": bool(ema5 >= ema9),
            "retomada_micro": retomada_micro,
            "ultimo_candle_verde_1m": bool(ultimo_close > ultimo_open),
            "variacao_3m": round(variacao_3m, 6),
            "posicao_range_10m": round(posicao_range, 6),
            "volume_relativo_1m": round(volume_relativo, 4),
            "entrada_micro_estendida": entrada_micro_estendida,
        }
    except Exception as e:
        return {
            "disponivel": False,
            "erro": str(e)
        }


def registrar_contexto_sinal_no_executor(symbol, preview, tempo_sinal):
    """
    Guarda no executor o contexto técnico/IA do sinal para que,
    quando o trade fechar, o aprendizado saiba exatamente como entrou.
    Falha aqui NÃO bloqueia alerta nem execução.
    """
    if not APRENDIZADO_ATIVO:
        return False

    token = _token_executor()
    if not token:
        return False

    try:
        dados = preview.get("dados", {}) if isinstance(preview, dict) else {}
        ia = preview.get("analise_ia", {}) if isinstance(preview, dict) else {}

        payload = {
            "ativo": symbol,
            "tempo_sinal": int(tempo_sinal),
            "preco_sinal": preview.get("entrada"),
            "estrategia_versao": ESTRATEGIA_VERSAO,
            "score_final": preview.get("score"),
            "score_tecnico": dados.get("score_tecnico"),
            "grau_setup": dados.get("grau_setup"),
            "decisao_ia": ia.get("decisao"),
            "qualidade_ia": ia.get("qualidade"),
            "tipo_mercado_ia": ia.get("tipo_mercado"),
            "risco_ia": ia.get("risco"),
            "motivo_ia": ia.get("motivo"),
            "features": {
                "posicao_range_local": dados.get("posicao_range_local"),
                "impulso_desde_fundo": dados.get("impulso_desde_fundo"),
                "rompimento_necessario_para_tp": dados.get("rompimento_necessario_para_tp"),
                "distancia_preco_ema9": dados.get("distancia_preco_ema9"),
                "rsi": dados.get("rsi"),
                "variacao_5": dados.get("variacao_5"),
                "variacao_10": dados.get("variacao_10"),
                "entrada_na_zona_baixa": dados.get("entrada_na_zona_baixa"),
                "entrada_estendida": dados.get("entrada_estendida"),
                "alvo_plausivel": dados.get("alvo_plausivel"),
                "confirmacao_compra": dados.get("confirmacao_compra"),
                "reacao_atual": dados.get("reacao_atual"),
                "volume_ok": dados.get("volume_ok"),
                "volume_candle_forte": dados.get("volume_candle_forte"),
                "pressao_vendedora_forte": dados.get("pressao_vendedora_forte"),
                "macro_baixista": dados.get("macro_baixista"),
                "perto_resistencia_4h": dados.get("perto_resistencia_4h"),
                "contexto_1m": dados.get("contexto_1m"),
            },
            "parametros_entrada": dados.get("parametros_entrada"),
            "aprendizado": dados.get("aprendizado"),
        }

        r = requests.post(
            f"{EXECUTOR_BASE_URL}/registrar-contexto-sinal",
            params={"token": token},
            json=payload,
            timeout=APRENDIZADO_TIMEOUT
        )
        return r.status_code < 400
    except Exception as e:
        print("APRENDIZADO_REGISTRO_SINAL_FALHOU", symbol, str(e))
        return False


def _extrair_json_ia(texto):
    texto = (texto or "").strip().replace("```json", "").replace("```", "").strip()
    inicio = texto.find("{")
    fim = texto.rfind("}") + 1
    if inicio == -1 or fim <= inicio:
        raise ValueError("Resposta da IA sem JSON válido")
    return json.loads(texto[inicio:fim])


def analisar_lote_aprendizado_com_ia(pendente):
    """
    A IA pode propor no máximo 1 alteração por ativo e somente
    nos parâmetros permitidos. TP, SL, capital, ativos, OCO e
    controles de segurança ficam fora do aprendizado automático.
    """
    prompt = f"""
Você é o módulo de aprendizagem quantitativa de um robô Spot LONG BTC/USDT e ETH/USDT.

OBJETIVO:
Analisar um lote de 10 trades reais fechados e decidir se existe evidência suficiente
para manter, reverter ou fazer UM microajuste de entrada por ativo.

REGRAS OBRIGATÓRIAS:
1. Não altere TP, SL, capital, ativos, OCO, APIs, limites de exposição ou segurança.
2. Máximo de 1 parâmetro alterado por ativo neste lote.
3. Parâmetros permitidos:
   - posicao_range_local_max
   - impulso_desde_fundo_max
   - rompimento_max_para_tp
   - distancia_preco_ema9_max
   - rejeicao_minima
   - volume_minimo_relativo
4. Se o ativo tiver amostra insuficiente ou dados de entrada incompletos: MANTER.
5. Não ajuste para "corrigir" um único loss.
6. Priorize expectativa líquida, Profit Factor, MAE/MFE e padrões recorrentes.
7. Se o lote posterior a uma mudança ficou claramente pior, você pode escolher ROLLBACK.
8. Um ajuste deve ser pequeno. O executor ainda validará limites e variação máxima.
9. Não confunda score com probabilidade de WIN.
10. O objetivo é melhorar TIMING de entrada, não aumentar frequência artificialmente.

BASELINES ATUAIS:
{json.dumps({s: CONFIG_ATIVOS[s]["entrada"] for s in ATIVOS_MONITORADOS}, ensure_ascii=False)}

LOTE PENDENTE:
{json.dumps(pendente, ensure_ascii=False)}

RESPONDA SOMENTE JSON PURO:
{{
  "batch_id": "id recebido",
  "resumo": "conclusão curta",
  "decisoes": [
    {{
      "ativo": "BTCUSDT",
      "acao": "MANTER|AJUSTAR|ROLLBACK",
      "parametro": "nome ou null",
      "valor_novo": número ou null,
      "motivo": "evidência objetiva e curta"
    }},
    {{
      "ativo": "ETHUSDT",
      "acao": "MANTER|AJUSTAR|ROLLBACK",
      "parametro": "nome ou null",
      "valor_novo": número ou null,
      "motivo": "evidência objetiva e curta"
    }}
  ]
}}
"""

    resposta = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        timeout=12
    )
    proposta = _extrair_json_ia(resposta.choices[0].message.content)

    decisoes_validas = []
    for decisao in proposta.get("decisoes", []):
        if not isinstance(decisao, dict):
            continue
        ativo = str(decisao.get("ativo", "")).upper()
        acao = str(decisao.get("acao", "MANTER")).upper()

        if ativo not in ATIVOS_MONITORADOS:
            continue
        if acao not in {"MANTER", "AJUSTAR", "ROLLBACK"}:
            acao = "MANTER"

        parametro = decisao.get("parametro")
        valor_novo = decisao.get("valor_novo")

        if acao == "AJUSTAR":
            if parametro not in PARAMETROS_ADAPTAVEIS:
                acao = "MANTER"
                parametro = None
                valor_novo = None
            else:
                try:
                    valor_novo = float(valor_novo)
                    minimo, maximo = LIMITES_ADAPTATIVOS[parametro]
                    if not (minimo <= valor_novo <= maximo):
                        acao = "MANTER"
                        parametro = None
                        valor_novo = None
                except Exception:
                    acao = "MANTER"
                    parametro = None
                    valor_novo = None

        decisoes_validas.append({
            "ativo": ativo,
            "acao": acao,
            "parametro": parametro,
            "valor_novo": valor_novo,
            "motivo": str(decisao.get("motivo", ""))[:500]
        })

    return {
        "batch_id": pendente.get("batch_id"),
        "resumo": str(proposta.get("resumo", ""))[:1000],
        "decisoes": decisoes_validas
    }


def processar_aprendizado_pendente():
    if not APRENDIZADO_ATIVO:
        return

    token = _token_executor()
    if not token:
        return

    r = requests.get(
        f"{EXECUTOR_BASE_URL}/aprendizado-pendente",
        params={"token": token},
        timeout=APRENDIZADO_TIMEOUT
    )
    if r.status_code >= 400:
        return

    pendente = r.json()
    if not isinstance(pendente, dict) or not pendente.get("pendente"):
        return

    proposta = analisar_lote_aprendizado_com_ia(pendente)

    aplicar = requests.post(
        f"{EXECUTOR_BASE_URL}/aprendizado-aplicar",
        params={"token": token},
        json=proposta,
        timeout=3
    )
    if aplicar.status_code >= 400:
        print("APRENDIZADO_APLICACAO_FALHOU", aplicar.status_code, aplicar.text[:500])
        return

    resultado = aplicar.json()

    with APRENDIZADO_CACHE_LOCK:
        APRENDIZADO_CACHE.clear()
        APRENDIZADO_CACHE_TS.clear()

    enviar_telegram(
        "🧠 APRENDIZADO VERTICAL CONCLUÍDO\n\n"
        f"Lote: {proposta.get('batch_id')}\n"
        f"Resumo: {proposta.get('resumo')}\n"
        f"Aplicação: {json.dumps(resultado.get('alteracoes', []), ensure_ascii=False)}"
    )


def loop_aprendizado_automatico():
    """
    Thread separada: qualquer erro de IA/aprendizado não interfere
    no monitor de mercado nem no executor.
    """
    while True:
        try:
            processar_aprendizado_pendente()
        except Exception as e:
            print("ERRO_APRENDIZADO_AUTOMATICO:", str(e))
        time.sleep(60)



# =========================
# UTILITÁRIOS
# =========================

def obter_grupo(symbol):
    symbol = symbol.upper()
    return CONFIG_ATIVOS.get(symbol, {}).get("grupo")


def assinar_params(params: dict, secret: str):
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


def arredondar(valor, casas):
    quant = Decimal("1") / (Decimal("10") ** casas)
    return str(Decimal(str(valor)).quantize(quant, rounding=ROUND_DOWN))


def registrar_evento(tipo, dados):
    try:
        evento = {
            "tipo": tipo,
            "timestamp": int(time.time()),
            **dados
        }

        with open("trades_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(evento, ensure_ascii=False) + "\n")

    except Exception as e:
        print("ERRO_LOG:", str(e))


# =========================
# BINANCE DATA
# =========================

def get_klines(symbol, interval="5m", limit=50):
    url = f"{BINANCE_DATA_URL}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json()


def get_preco_atual(symbol):
    url = f"{BINANCE_DATA_URL}/api/v3/ticker/price?symbol={symbol}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    dados = response.json()
    return float(dados["price"])


def calcular_ma(closes, periodo):
    return sum(closes[-periodo:]) / periodo


def calcular_rsi(closes, periodo=14):
    ganhos = []
    perdas = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            ganhos.append(diff)
            perdas.append(0)
        else:
            ganhos.append(0)
            perdas.append(abs(diff))

    media_ganhos = sum(ganhos[-periodo:]) / periodo
    media_perdas = sum(perdas[-periodo:]) / periodo

    if media_perdas == 0:
        return 100

    rs = media_ganhos / media_perdas
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calcular_edge_contexto(data):
    try:
        closes = [float(c[4]) for c in data]
        highs = [float(c[2]) for c in data]
        lows = [float(c[3]) for c in data]

        preco = closes[-1]

        # =========================
        # 1. PRESSÃO DE ROMPIMENTO
        # =========================
        suporte = min(lows[-10:])
        resistencia = max(highs[-10:])

        toques_resistencia = sum(1 for h in highs[-5:] if abs(h - resistencia) / resistencia < 0.002)
        toques_suporte = sum(1 for l in lows[-5:] if abs(l - suporte) / suporte < 0.002)

        pressao_rompimento = False

        if toques_resistencia >= 3:
            pressao_rompimento = "resistencia"
        elif toques_suporte >= 3:
            pressao_rompimento = "suporte"

        # =========================
        # 2. REJEIÇÃO (FORÇA REAL)
        # =========================
        ultima = data[-1]

        abertura = float(ultima[1])
        fechamento = float(ultima[4])
        maxima = float(ultima[2])
        minima = float(ultima[3])

        corpo = abs(fechamento - abertura)
        range_total = maxima - minima

        rejeicao = "neutra"

        if range_total > 0:
            sombra_superior = maxima - max(abertura, fechamento)
            sombra_inferior = min(abertura, fechamento) - minima

            if sombra_inferior > corpo * 1.2:
                rejeicao = "compra"
            elif sombra_superior > corpo * 1.2:
                rejeicao = "venda"

        return {
            "pressao_rompimento": pressao_rompimento,
            "rejeicao": rejeicao
        }

    except Exception as e:
        print("ERRO_EDGE:", str(e))
        return {
            "pressao_rompimento": None,
            "rejeicao": "neutra"
        }
def calcular_contexto_4h(symbol):
    try:
        data_4h_bruta = get_klines(symbol, interval="4h", limit=120)

        # A ultima kline da Binance pode estar em formacao.
        # O contexto 4h usa somente candles concluidos para evitar
        # que a classificacao macro mude no meio do candle.
        data_4h = data_4h_bruta[:-1]

        if len(data_4h) < 99:
            raise ValueError("Historico 4h insuficiente para MA99 com candle fechado")

        closes = [float(c[4]) for c in data_4h]
        volumes = [float(c[5]) for c in data_4h]

        preco_fechado_4h = closes[-1]
        ma7_4h = calcular_ma(closes, 7)
        ma25_4h = calcular_ma(closes, 25)
        ma99_4h = calcular_ma(closes, 99)

        volume_atual_4h = volumes[-1]
        volume_medio_4h = sum(volumes[-10:]) / 10

        macro_baixista = (
            preco_fechado_4h < ma25_4h
            and ma25_4h < ma99_4h
        )

        perto_resistencia_4h = (
            preco_fechado_4h < ma25_4h
            and ((ma25_4h - preco_fechado_4h) / preco_fechado_4h) < 0.012
        )

        volume_4h_fraco = volume_atual_4h < volume_medio_4h

        return {
            "ma7_4h": ma7_4h,
            "ma25_4h": ma25_4h,
            "ma99_4h": ma99_4h,
            "macro_baixista": macro_baixista,
            "perto_resistencia_4h": perto_resistencia_4h,
            "volume_4h_fraco": volume_4h_fraco,
            "contexto_4h_candle_fechado": True
        }

    except Exception as e:
        print("ERRO_CONTEXTO_4H:", str(e))
        return {
            "ma7_4h": None,
            "ma25_4h": None,
            "ma99_4h": None,
            "macro_baixista": False,
            "perto_resistencia_4h": False,
            "volume_4h_fraco": False,
            "contexto_4h_candle_fechado": False
        }

def calcular_ema(valores, periodo):
    if not valores:
        return 0
    ema = float(valores[0])
    k = 2.0 / (float(periodo) + 1.0)
    for valor in valores[1:]:
        ema = (float(valor) * k) + (ema * (1.0 - k))
    return ema


def calcular_score(dados, ia=None):
    # Score de ranking, NÃO representa probabilidade de WIN.
    score = int(dados.get("score_tecnico", 0))
    if ia:
        if ia.get("aprovar") is True:
            score += 5
        if ia.get("risco") == "baixo":
            score += 3
    return max(0, min(score, 100))

# =========================
# ANÁLISE TÉCNICA
# =========================

def gerar_analise(symbol):
    """
    VERTICAL HÍBRIDO V3 — base estrutural adaptada do lateral Híbrido V2.

    O que foi trazido do lateral:
    - localização da entrada dentro do range local;
    - anti-atraso pelo impulso desde o fundo recente;
    - alvo plausível sem depender de grande rompimento;
    - confirmação por reação / rejeição / qualidade de candle;
    - pressão vendedora como veto contextual, não empilhamento infinito de filtros.

    O que permanece específico do vertical:
    - tendência de alta em 5m é obrigatória;
    - contexto 4h continua sendo observado;
    - setup usa candles FECHADOS e preço atual apenas para localização/gatilho;
    - TP/SL não são alterados aqui; continuam alinhados ao executor vertical.
    """
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        raise ValueError("Ativo não permitido.")

    config = CONFIG_ATIVOS[symbol]
    p, contexto_aprendizado = parametros_entrada_efetivos(symbol)

    data_bruta = get_klines(symbol, limit=50)
    data = data_bruta[:-1]  # estrutura somente com candles fechados

    if len(data) < 30:
        raise ValueError("Historico 5m insuficiente para analise vertical hibrida")

    preco = float(get_preco_atual(symbol))
    contexto_4h = calcular_contexto_4h(symbol)
    contexto_1m = calcular_contexto_1m(symbol)

    closes = [float(c[4]) for c in data]
    highs = [float(c[2]) for c in data]
    lows = [float(c[3]) for c in data]
    volumes = [float(c[5]) for c in data]

    ultimo_close = closes[-1]
    penultimo_close = closes[-2]
    ultimo_open = float(data[-1][1])
    ultimo_high = highs[-1]
    ultimo_low = lows[-1]
    penultimo_high = highs[-2]

    ema9 = calcular_ema(closes[-20:], 9)
    ema21 = calcular_ema(closes[-30:], 21)
    ma7 = calcular_ma(closes, 7)
    ma25 = calcular_ma(closes, 25)
    rsi = calcular_rsi(closes)

    tendencia_alta = ema9 > ema21
    tendencia_baixa = ema9 < ema21
    tendencia = "alta" if tendencia_alta else "baixa"

    distancia_preco_ema9 = abs(preco - ema9) / ema9 if ema9 else 1
    distancia_ema = abs(ema9 - ema21) / ema21 if ema21 else 1
    distancia_ma7 = (preco - ma7) / ma7 if ma7 else 0
    distancia_ma25 = (preco - ma25) / ma25 if ma25 else 0

    variacao_5 = (closes[-1] - closes[-5]) / closes[-5]
    variacao_10 = (closes[-1] - closes[-10]) / closes[-10]

    corpo_candle = abs(ultimo_close - ultimo_open)
    range_candle = ultimo_high - ultimo_low

    if range_candle > 0:
        pavio_inferior = min(ultimo_open, ultimo_close) - ultimo_low
        pavio_superior = ultimo_high - max(ultimo_open, ultimo_close)
        corpo_percentual = corpo_candle / range_candle
        pavio_inferior_percentual = pavio_inferior / range_candle
        pavio_superior_percentual = pavio_superior / range_candle
    else:
        corpo_percentual = 0
        pavio_inferior_percentual = 0
        pavio_superior_percentual = 0

    candle_verde = ultimo_close > ultimo_open
    candle_estendido = (
        corpo_percentual > 0.75
        and pavio_superior_percentual > 0.20
    )

    rejeicao_pavio_comprador = (
        candle_verde
        and pavio_inferior_percentual > 0.35
        and corpo_percentual > 0.35
    )

    rompeu_maxima_anterior = ultimo_close > penultimo_high
    sequencia_fechamentos_alta = closes[-3] < closes[-2] < closes[-1]

    volume_atual = volumes[-1]
    media_volume_10 = sum(volumes[-10:]) / 10
    media_volume_30 = sum(volumes[-30:]) / 30
    volume_candle_forte = volume_atual > media_volume_10
    volume_ok = media_volume_10 >= media_volume_30 * p["volume_minimo_relativo"]

    qualidade_candle = (
        candle_verde
        and not candle_estendido
        and (
            rejeicao_pavio_comprador
            or rompeu_maxima_anterior
            or sequencia_fechamentos_alta
        )
        and volume_candle_forte
    )

    rejeicao_compra = (
        penultimo_close < ultimo_close
        and ultimo_close > ema9
        and ((ultimo_close - penultimo_close) / penultimo_close) > p["rejeicao_minima"]
    )

    # Reação atual simples: não esperamos outro candle inteiro fechar.
    # O preço atual só confirma que a correção parou e começou a reagir.
    reacao_atual = (
        preco > ultimo_close
        and preco >= ema9
    )

    # V3.1 — correção fiel ao princípio do lateral:
    # uma simples reação intrabar não aprova sozinha.
    # Se a confirmação vier apenas do preço atual, exigimos também
    # volume do último candle fechado acima da média de 10 candles.
    reacao_atual_com_volume = bool(
        reacao_atual
        and volume_candle_forte
    )

    confirmacao_compra = bool(
        rejeicao_compra
        or qualidade_candle
        or reacao_atual_com_volume
    )

    # Range macro apenas para identificar mercado morto / volatilidade mínima.
    suporte_macro = min(lows[-30:])
    resistencia_macro = max(highs[-30:])
    amplitude_macro = (
        (resistencia_macro - suporte_macro) / suporte_macro
        if suporte_macro > 0 else 0
    )
    volatilidade_minima = amplitude_macro >= p["amplitude_minima"]

    # RANGE LOCAL: mesma ideia que vem funcionando no lateral.
    janela = int(p["janela_range_local"])
    highs_local = highs[-(janela + 1):-1]
    lows_local = lows[-(janela + 1):-1]

    if not highs_local or not lows_local:
        raise ValueError("Range local insuficiente")

    suporte_local = min(lows_local)
    resistencia_local = max(highs_local)
    largura_range_local = resistencia_local - suporte_local

    if largura_range_local > 0:
        posicao_range_local = (preco - suporte_local) / largura_range_local
    else:
        posicao_range_local = 1

    entrada_na_zona_baixa = (
        posicao_range_local >= -0.05
        and posicao_range_local <= p["posicao_range_local_max"]
    )

    # Fundo recente exclui o último candle fechado, igual ao princípio do lateral.
    lows_fundo_recente = lows[-7:-1]
    fundo_recente = min(lows_fundo_recente) if lows_fundo_recente else suporte_local

    impulso_desde_fundo = (
        (preco - fundo_recente) / fundo_recente
        if fundo_recente > 0 else 1
    )
    entrada_estendida = impulso_desde_fundo > p["impulso_desde_fundo_max"]

    distancia_preco_ema9_ok = distancia_preco_ema9 <= p["distancia_preco_ema9_max"]

    alvo = preco * (1 + ALVO_EXECUTOR_PERCENTUAL)
    stop = preco * (1 - STOP_EXECUTOR_PERCENTUAL)
    stop_limit = preco * (1 - STOP_LIMIT_EXECUTOR_PERCENTUAL)

    if alvo > resistencia_local and resistencia_local > 0:
        rompimento_necessario_para_tp = (
            (alvo - resistencia_local) / resistencia_local
        )
    else:
        rompimento_necessario_para_tp = 0

    alvo_plausivel = (
        rompimento_necessario_para_tp <= p["rompimento_max_para_tp"]
    )

    espaco_resistencia_local = (
        (resistencia_local - preco) / preco
        if preco > 0 else -1
    )

    pressao_vendedora_forte = (
        ultimo_close < ultimo_open
        and corpo_percentual >= p["corpo_pressao_min"]
        and volume_atual >= (media_volume_10 * p["volume_pressao_relativo"])
    )

    veto_pressao_vendedora = (
        pressao_vendedora_forte
        and not reacao_atual
        and not rejeicao_compra
        and not qualidade_candle
    )

    # Contexto 4h não é mais um empilhamento de bloqueios isolados.
    # Vira penalidade e só se torna veto quando coincide com pressão vendedora forte.
    veto_macro = bool(
        contexto_4h.get("macro_baixista") is True
        and veto_pressao_vendedora
    )

    score_tecnico = 0
    ajustes_score = []

    if tendencia_alta:
        score_tecnico += 25
    if preco >= ema21:
        score_tecnico += 10
    if distancia_preco_ema9_ok:
        score_tecnico += 15
    if entrada_na_zona_baixa:
        score_tecnico += 20
    if not entrada_estendida:
        score_tecnico += 15
    if alvo_plausivel:
        score_tecnico += 20
    if confirmacao_compra:
        score_tecnico += 15
    if volume_ok:
        score_tecnico += 10
    if qualidade_candle:
        score_tecnico += 5
    if not contexto_4h.get("macro_baixista"):
        score_tecnico += 5

    if pressao_vendedora_forte:
        score_tecnico -= 15
        ajustes_score.append("penalidade_pressao_vendedora:-15")
    if contexto_4h.get("macro_baixista"):
        score_tecnico -= 8
        ajustes_score.append("penalidade_macro_4h:-8")
    if contexto_4h.get("perto_resistencia_4h"):
        score_tecnico -= 5
        ajustes_score.append("penalidade_resistencia_4h:-5")

    score_tecnico = max(0, min(int(score_tecnico), 100))
    score_minimo = int(p["score_minimo"])

    setup_tecnico_ok = bool(
        tendencia_alta
        and preco >= ema21
        and volatilidade_minima
        and distancia_preco_ema9_ok
        and entrada_na_zona_baixa
        and not entrada_estendida
        and alvo_plausivel
        and confirmacao_compra
        and not veto_pressao_vendedora
        and not veto_macro
        and score_tecnico >= score_minimo
    )

    motivos_bloqueio = []
    if not tendencia_alta:
        motivos_bloqueio.append("tendencia_5m_nao_alta")
    if preco < ema21:
        motivos_bloqueio.append("preco_abaixo_ema21")
    if not volatilidade_minima:
        motivos_bloqueio.append("volatilidade_insuficiente")
    if not distancia_preco_ema9_ok:
        motivos_bloqueio.append("preco_longe_ema9")
    if not entrada_na_zona_baixa:
        motivos_bloqueio.append("entrada_alta_no_range_local")
    if entrada_estendida:
        motivos_bloqueio.append("entrada_tardia_apos_subida")
    if not alvo_plausivel:
        motivos_bloqueio.append("tp_exige_rompimento_excessivo")
    if not confirmacao_compra:
        motivos_bloqueio.append("sem_reacao_compradora")
    if veto_pressao_vendedora:
        motivos_bloqueio.append("pressao_vendedora_forte")
    if veto_macro:
        motivos_bloqueio.append("macro_4h_baixista_com_pressao")
    if score_tecnico < score_minimo:
        motivos_bloqueio.append(f"score_abaixo_minimo:{score_tecnico}<{score_minimo}")

    if setup_tecnico_ok:
        grau_setup = "APROVADO"
    elif (
        tendencia_alta
        and entrada_na_zona_baixa
        and not entrada_estendida
        and alvo_plausivel
    ):
        grau_setup = "QUASE_APROVADO"
    elif score_tecnico >= 65:
        grau_setup = "PROXIMO"
    else:
        grau_setup = "FRACO"

    return {
        "ativo": symbol,
        "grupo": obter_grupo(symbol),
        "estrategia_versao": ESTRATEGIA_VERSAO,
        "estrategia_entrada": "vertical_hibrida_trend_localizacao_range",
        "setup_tecnico_ok": setup_tecnico_ok,
        "grau_setup": grau_setup,
        "score_tecnico": score_tecnico,
        "score_minimo": score_minimo,
        "motivos_bloqueio": motivos_bloqueio,

        "preco": round(preco, config["price_decimals"]),
        "preco_ultimo_fechamento_5m": ultimo_close,
        "setup_candles_fechados": True,
        "gatilho_preco_atual": True,

        "tendencia": tendencia,
        "tendencia_alta": tendencia_alta,
        "tendencia_baixa": tendencia_baixa,
        "ema9": ema9,
        "ema21": ema21,
        "distancia_ema": round(distancia_ema, 6),
        "distancia_preco_ema9": round(distancia_preco_ema9, 6),
        "distancia_preco_ema9_ok": distancia_preco_ema9_ok,
        "ma7": ma7,
        "ma25": ma25,
        "distancia_ma7": round(distancia_ma7, 6),
        "distancia_ma25": round(distancia_ma25, 6),
        "rsi": round(rsi, 2),
        "variacao_5": round(variacao_5, 6),
        "variacao_10": round(variacao_10, 6),

        "suporte_local": round(suporte_local, config["price_decimals"]),
        "resistencia_local": round(resistencia_local, config["price_decimals"]),
        "posicao_range_local": round(posicao_range_local, 6),
        "posicao_range_local_pct": round(posicao_range_local * 100, 3),
        "entrada_na_zona_baixa": entrada_na_zona_baixa,
        "fundo_recente": round(fundo_recente, config["price_decimals"]),
        "impulso_desde_fundo": round(impulso_desde_fundo, 6),
        "impulso_desde_fundo_pct": round(impulso_desde_fundo * 100, 3),
        "entrada_estendida": entrada_estendida,
        "espaco_resistencia_local": round(espaco_resistencia_local, 6),
        "rompimento_necessario_para_tp": round(rompimento_necessario_para_tp, 6),
        "rompimento_necessario_para_tp_pct": round(rompimento_necessario_para_tp * 100, 3),
        "alvo_plausivel": alvo_plausivel,

        "reacao_atual": reacao_atual,
        "reacao_atual_com_volume": reacao_atual_com_volume,
        "rejeicao_compra": rejeicao_compra,
        "rejeicao_pavio_comprador": rejeicao_pavio_comprador,
        "qualidade_candle": qualidade_candle,
        "candle_estendido": candle_estendido,
        "confirmacao_compra": confirmacao_compra,
        "volume_ok": volume_ok,
        "volume_candle_forte": volume_candle_forte,
        "pressao_vendedora_forte": pressao_vendedora_forte,
        "veto_pressao_vendedora": veto_pressao_vendedora,
        "veto_macro": veto_macro,
        "volatilidade_minima": volatilidade_minima,
        "amplitude_macro": round(amplitude_macro, 6),

        "alvo": round(alvo, config["price_decimals"]),
        "stop": round(stop, config["price_decimals"]),
        "stop_limit": round(stop_limit, config["price_decimals"]),

        "ma7_4h": contexto_4h["ma7_4h"],
        "ma25_4h": contexto_4h["ma25_4h"],
        "ma99_4h": contexto_4h["ma99_4h"],
        "macro_baixista": contexto_4h["macro_baixista"],
        "perto_resistencia_4h": contexto_4h["perto_resistencia_4h"],
        "volume_4h_fraco": contexto_4h["volume_4h_fraco"],
        "contexto_4h_candle_fechado": contexto_4h.get("contexto_4h_candle_fechado"),
        "contexto_1m": contexto_1m,
        "aprendizado": {
            "ativo": bool(APRENDIZADO_ATIVO),
            "versao": contexto_aprendizado.get("learning_version") if isinstance(contexto_aprendizado, dict) else None,
            "active_overrides": contexto_aprendizado.get("active_overrides", {}) if isinstance(contexto_aprendizado, dict) else {},
            "total_trades_aprendidos": contexto_aprendizado.get("total_trades_aprendidos") if isinstance(contexto_aprendizado, dict) else None,
            "ultimo_lote": contexto_aprendizado.get("ultimo_lote") if isinstance(contexto_aprendizado, dict) else None,
        },
        "ajustes_score": ajustes_score,
        "parametros_entrada": dict(p)
    }

# =========================
# IA
# =========================

def gerar_ia(symbol, dados=None):
    if dados is None:
        dados = gerar_analise(symbol)

    prompt = f"""
Você é a camada de TIMING de um robô profissional Spot LONG de BTC/USDT e ETH/USDT.

IMPORTANTE:
- A direção e a segurança estrutural vêm das regras quantitativas.
- Sua função é escolher o melhor MOMENTO entre ENTER, WAIT e REJECT.
- WAIT é uma decisão válida e deve ser usada quando a ideia é boa, mas a entrada ainda está prematura,
  estendida, perto demais da máxima/resistência ou sem retomada micro suficiente.
- REJECT quando o cenário perdeu qualidade ou contradiz a tese.
- ENTER somente quando o setup técnico está aprovado e o timing atual oferece relação razoável
  entre espaço até o alvo e risco de stop.

COMO USAR O CONTEXTO 1m:
- É apenas microtiming; não substitui o 5m.
- Se contexto_1m.disponivel=false, NÃO reprove só por isso.
- Prefira WAIT se entrada_micro_estendida=true.
- Retomada_micro=true, tendência_micro_alta e volume relativo saudável favorecem ENTER,
  desde que o 5m já esteja tecnicamente aprovado.

COMO USAR O APRENDIZADO:
- "aprendizado.active_overrides" representa ajustes já aprovados em lotes anteriores.
- "aprendizado.ultimo_lote" é evidência histórica recente, não garantia.
- Não invente padrões que os dados não sustentam.
- Não mude parâmetros aqui. Apenas decida o timing do trade atual.

REGRAS:
- Score NÃO é probabilidade de WIN.
- Não persiga preço.
- Evite entrada próxima de teto local se o TP depender de rompimento adicional relevante.
- Dê peso a posição no range, impulso já gasto, distância da EMA9, confirmação, pressão vendedora,
  contexto 4h e microtiming 1m.
- Se o setup técnico não estiver aprovado, nunca retorne ENTER.

RESPONDA SOMENTE JSON PURO:
{{
  "decisao": "ENTER|WAIT|REJECT",
  "aprovar": true ou false,
  "qualidade": número de 0 a 100,
  "tipo_mercado": "tendencia_pullback | tendencia_forte | falsa_reacao | entrada_atrasada | pressao_vendedora | indefinido",
  "risco": "baixo | medio | alto",
  "motivo": "explicação curta, técnica e específica sobre o timing"
}}

DADOS:
{json.dumps(dados, ensure_ascii=False)}
"""

    try:
        resposta = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            timeout=8
        )

        ia = _extrair_json_ia(resposta.choices[0].message.content)
        decisao = str(ia.get("decisao", "")).upper()

        if decisao not in {"ENTER", "WAIT", "REJECT"}:
            decisao = "ENTER" if ia.get("aprovar") is True else "REJECT"

        if not dados.get("setup_tecnico_ok") and decisao == "ENTER":
            decisao = "WAIT"

        ia["decisao"] = decisao
        ia["aprovar"] = bool(decisao == "ENTER")
        try:
            ia["qualidade"] = max(0, min(int(ia.get("qualidade", 0)), 100))
        except Exception:
            ia["qualidade"] = 0

    except Exception as e:
        print("ERRO_IA:", str(e))
        ia = {
            "decisao": "REJECT",
            "aprovar": False,
            "qualidade": 0,
            "tipo_mercado": "erro_ia",
            "risco": "alto",
            "motivo": f"IA falhou ou demorou demais: {str(e)}"
        }

    score = calcular_score(dados, ia)
    return {
        "dados": dados,
        "analise_ia": ia,
        "score": score
    }

# =========================
# ROTAS DE ANÁLISE
# =========================

@app.get("/preco/{symbol}")
def get_preco(symbol: str):
    symbol = symbol.upper()

    if symbol not in CONFIG_ATIVOS:
        return {"erro": "Ativo não permitido"}

    url = f"{BINANCE_DATA_URL}/api/v3/ticker/price?symbol={symbol}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        return {
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
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


@app.get("/ia/{symbol}")
def ia(symbol: str):
    try:
        return gerar_ia(symbol)

    except Exception as e:
        return {
            "ativo": symbol.upper(),
            "erro": str(e)
        }


@app.get("/ordem-preview/{symbol}")
def ordem_preview(symbol: str):
    try:
        symbol = symbol.upper()

        if symbol not in CONFIG_ATIVOS:
            return {
                "ativo": symbol,
                "pode_operar": False,
                "motivo": "Ativo não permitido"
            }

        dados = gerar_analise(symbol)
        score_tecnico = int(dados.get("score_tecnico", 0))

        # A IA de timing observa também setups QUASE_APROVADOS para aprender
        # a diferença entre WAIT e REJECT. Segurança preservada: somente
        # setup_tecnico_ok pode virar ordem real.
        if not dados.get("setup_tecnico_ok"):
            if dados.get("grau_setup") == "QUASE_APROVADO":
                resultado_ia = gerar_ia(symbol, dados=dados)
                ia_quase = resultado_ia.get("analise_ia", {})
                return {
                    "ativo": symbol,
                    "grupo": obter_grupo(symbol),
                    "pode_operar": False,
                    "motivo": f"TIMING_{ia_quase.get('decisao', 'WAIT')}: "
                              + (" | ".join(dados.get("motivos_bloqueio", [])) or "Setup ainda não aprovado"),
                    "score": resultado_ia.get("score", score_tecnico),
                    "dados": dados,
                    "analise_ia": ia_quase
                }

            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": " | ".join(dados.get("motivos_bloqueio", [])) or "Setup técnico não aprovado",
                "score": score_tecnico,
                "dados": dados,
                "analise_ia": {
                    "decisao": "REJECT",
                    "aprovar": False,
                    "qualidade": 0,
                    "tipo_mercado": "prefiltro_tecnico",
                    "risco": "alto",
                    "motivo": "Setup técnico distante; IA de timing não chamada"
                }
            }

        resultado_ia = gerar_ia(symbol, dados=dados)
        ia = resultado_ia.get("analise_ia", {})
        score = resultado_ia.get("score", score_tecnico)
        qualidade_minima = int(CONFIG_ATIVOS[symbol]["entrada"]["qualidade_ia_minima"])

        if not ia.get("aprovar"):
            decisao_ia = str(ia.get("decisao", "REJECT")).upper()
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": (
                    "IA decidiu WAIT: setup bom, mas timing ainda não ideal"
                    if decisao_ia == "WAIT"
                    else "IA rejeitou o timing atual"
                ),
                "score": score,
                "dados": dados,
                "analise_ia": ia
            }

        if int(ia.get("qualidade", 0)) < qualidade_minima:
            return {
                "ativo": symbol,
                "grupo": obter_grupo(symbol),
                "pode_operar": False,
                "motivo": f"Qualidade IA abaixo do mínimo: {ia.get('qualidade', 0)}<{qualidade_minima}",
                "score": score,
                "dados": dados,
                "analise_ia": ia
            }

        return {
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
            "pode_operar": True,
            "direcao": "compra",
            "entrada": dados["preco"],
            "stop": dados["stop"],
            "stop_limit": dados["stop_limit"],
            "alvo": dados["alvo"],
            "score": score,
            "valor_usdt": VALOR_POR_TRADE_USDT,
            "confirmacao_necessaria": True,
            "dados": dados,
            "analise_ia": ia
        }

    except Exception as e:
        return {"erro": str(e)}

@app.get("/diagnostico/{symbol}")
def diagnostico(symbol: str):
    symbol = symbol.upper()

    try:
        dados = gerar_analise(symbol)
        ia = {
            "aprovar": False,
            "qualidade": 0,
            "tipo_mercado": "prefiltro_tecnico",
            "risco": "alto",
            "motivo": "IA não chamada porque o setup técnico não passou"
        }
        score = int(dados.get("score_tecnico", 0))

        if dados.get("setup_tecnico_ok"):
            resultado_ia = gerar_ia(symbol, dados=dados)
            ia = resultado_ia.get("analise_ia", ia)
            score = resultado_ia.get("score", score)

        qualidade_minima = int(CONFIG_ATIVOS[symbol]["entrada"]["qualidade_ia_minima"])
        pode_operar = bool(
            dados.get("setup_tecnico_ok")
            and ia.get("aprovar") is True
            and int(ia.get("qualidade", 0)) >= qualidade_minima
        )

        bloqueios = list(dados.get("motivos_bloqueio", []))
        if dados.get("setup_tecnico_ok") and ia.get("aprovar") is not True:
            bloqueios.append("ia_nao_aprovou")
        if dados.get("setup_tecnico_ok") and int(ia.get("qualidade", 0)) < qualidade_minima:
            bloqueios.append(f"qualidade_ia_baixa:{ia.get('qualidade', 0)}<{qualidade_minima}")

        return {
            "ativo": symbol,
            "estrategia_versao": ESTRATEGIA_VERSAO,
            "pode_operar": pode_operar,
            "score": score,
            "bloqueios": bloqueios,
            "dados": dados,
            "analise_ia": ia
        }

    except Exception as e:
        return {
            "ativo": symbol,
            "erro": str(e)
        }

# =========================
# EXECUÇÃO DIRETA — MANTIDA, MAS NÃO USAR COMO FLUXO PRINCIPAL
# =========================

@app.post("/executar/{symbol}")
def executar(
    symbol: str,
    confirmar: str = Query(default="NAO", description="Use SIM para executar ordem real")
):
    try:
        symbol = symbol.upper()

        if confirmar != "SIM":
            return {
                "status": "bloqueado",
                "motivo": "Confirmação ausente. Use ?confirmar=SIM para executar ordem real."
            }

        if symbol not in CONFIG_ATIVOS:
            return {
                "status": "bloqueado",
                "motivo": "Ativo não permitido"
            }

        api_key = os.getenv("BINANCE_API_KEY")
        secret = os.getenv("BINANCE_API_SECRET")

        if not api_key or not secret:
            return {"erro": "API Binance não configurada"}

        preview_1 = ordem_preview(symbol)

        if not preview_1.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": preview_1.get("motivo"),
                "preview": preview_1
            }

        time.sleep(1)

        preview_2 = ordem_preview(symbol)

        if not preview_2.get("pode_operar"):
            return {
                "status": "bloqueado",
                "motivo": "Cenário mudou antes da execução.",
                "preview_inicial": preview_1,
                "preview_atual": preview_2
            }

        if preview_2.get("score", 0) < 82:
            return {
                "status": "bloqueado",
                "motivo": "Score caiu antes da execução.",
                "preview": preview_2
            }

        config = CONFIG_ATIVOS[symbol]
        valor_usd = config["valor_usd"]

        headers = {
            "X-MBX-APIKEY": api_key
        }

        params_compra = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": str(valor_usd),
            "newOrderRespType": "FULL",
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }

        signed_compra = assinar_params(params_compra, secret)
        url_compra = f"{BINANCE_API_URL}/api/v3/order?{signed_compra}"

        resposta_compra = requests.post(url_compra, headers=headers, timeout=10)
        compra_json = resposta_compra.json()

        if resposta_compra.status_code >= 400:
            return {
                "status": "erro_compra",
                "resposta_binance": compra_json
            }

        executed_qty = float(compra_json.get("executedQty", 0))

        if executed_qty <= 0:
            return {
                "status": "erro_compra",
                "motivo": "Quantidade executada veio zerada"
            }

        cummulative_quote = float(compra_json.get("cummulativeQuoteQty", 0))

        if cummulative_quote > 0 and executed_qty > 0:
            preco_medio = cummulative_quote / executed_qty
        else:
            preco_medio = float(compra_json["fills"][0]["price"])

        alvo = preco_medio * (1 + ALVO_EXECUTOR_PERCENTUAL)
        stop = preco_medio * (1 - STOP_EXECUTOR_PERCENTUAL)
        stop_limit = preco_medio * (1 - STOP_LIMIT_EXECUTOR_PERCENTUAL)

        # Correção preservada também na rota direta de contingência:
        # não aplicar corte fixo de 0,5%. Descontar apenas eventual
        # comissão cobrada no próprio ativo-base.
        asset_base = symbol[:-4] if symbol.endswith("USDT") else symbol
        comissao_base = sum(
            float(fill.get("commission", 0) or 0)
            for fill in (compra_json.get("fills") or [])
            if str(fill.get("commissionAsset") or "").upper() == asset_base
        )
        quantidade_liquida = executed_qty - comissao_base

        if quantidade_liquida <= 0:
            return {
                "status": "erro_compra",
                "motivo": "Quantidade líquida inválida após comissão"
            }

        qty_oco = arredondar(quantidade_liquida, config["qty_decimals"])

        params_oco = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": qty_oco,
            "aboveType": "LIMIT_MAKER",
            "abovePrice": arredondar(alvo, config["price_decimals"]),
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": arredondar(stop, config["price_decimals"]),
            "belowPrice": arredondar(stop_limit, config["price_decimals"]),
            "belowTimeInForce": "GTC",
            "recvWindow": 5000,
            "timestamp": int(time.time() * 1000)
        }

        signed_oco = assinar_params(params_oco, secret)
        url_oco = f"{BINANCE_API_URL}/api/v3/orderList/oco?{signed_oco}"

        resposta_oco = requests.post(url_oco, headers=headers, timeout=10)
        oco_json = resposta_oco.json()

        if resposta_oco.status_code >= 400:
            return {
                "status": "compra_ok_sem_oco",
                "alerta": "Compra executada, mas OCO falhou",
                "compra": compra_json,
                "erro_oco": oco_json
            }

        return {
            "status": "executado_com_oco",
            "ativo": symbol,
            "grupo": obter_grupo(symbol),
            "entrada": preco_medio,
            "alvo": alvo,
            "stop": stop,
            "quantidade": qty_oco,
            "valor_usdt": valor_usd,
            "compra": compra_json,
            "oco": oco_json
        }

    except Exception as e:
        return {
            "status": "erro",
            "erro": str(e)
        }


@app.get("/aprovar/{symbol}")
def aprovar(
    symbol: str,
    token: str,
    preco: float = None,
    tempo: int = None
):
    approval_token = os.getenv("APPROVAL_TOKEN")

    if token != approval_token:
        return {"status": "bloqueado", "motivo": "Token inválido"}

    return executar(symbol, confirmar="SIM")


# =========================
# TESTES E ALERTAS
# =========================

@app.get("/teste-botao")
def teste_botao():
    symbol = "BTCUSDT"
    dados = gerar_analise(symbol)
    preco_atual = dados["preco"]

    mensagem = f"""🚨 TESTE COM BOTÃO

Ativo: {symbol}
Grupo: {obter_grupo(symbol)}
Preço sinal: {preco_atual}
Valor planejado: {VALOR_POR_TRADE_USDT} USDT"""

    enviar_telegram(
        mensagem,
        symbol=symbol,
        preco=preco_atual
    )

    return {
        "status": "enviado",
        "ativo": symbol,
        "grupo": obter_grupo(symbol),
        "preco_sinal": preco_atual
    }


@app.get("/alerta-teste/{symbol}")
def alerta_teste(symbol: str):
    symbol = symbol.upper()

    preview = ordem_preview(symbol)

    if not preview.get("pode_operar"):
        return {
            "status": "sem_alerta",
            "ativo": symbol,
            "motivo": preview.get("motivo"),
            "preview": preview
        }

    mensagem = f"""🚨 OPORTUNIDADE DETECTADA

Ativo: {preview['ativo']}
Grupo: {preview['grupo']}
Direção: {preview['direcao']}
Score: {preview['score']}
Entrada: {preview['entrada']}
Stop: {preview['stop']}
Alvo: {preview['alvo']}
Valor planejado: {preview['valor_usdt']} USDT

⚠️ Sinal com validade curta. Aprove somente se fizer sentido."""

    enviar_telegram(
        mensagem,
        symbol=symbol,
        preco=preview["entrada"],
        tempo=int(time.time())
    )

    return {
        "status": "alerta_enviado",
        "ativo": symbol,
        "preview": preview
    }


# =========================
# MONITORAMENTO AUTOMÁTICO
# =========================

def monitorar_mercado():
    while True:
        try:
            for symbol in ATIVOS_MONITORADOS:
                agora = time.time()

                if symbol in ultimos_sinais:
                    if agora - ultimos_sinais[symbol] < 600:
                        continue

                preview = ordem_preview(symbol)

                if preview.get("pode_operar"):

                    dados_sinal = preview.get("dados", {})

                    registrar_evento("sinal_detectado", {
                        "symbol": symbol,
                        "grupo": preview.get("grupo"),
                        "score": preview.get("score"),
                        "entrada": preview.get("entrada"),
                        "valor_usdt": VALOR_POR_TRADE_USDT,
                        "score_tecnico": dados_sinal.get("score_tecnico"),
                        "grau_setup": dados_sinal.get("grau_setup"),
                        "posicao_range_local_pct": dados_sinal.get("posicao_range_local_pct"),
                        "impulso_desde_fundo_pct": dados_sinal.get("impulso_desde_fundo_pct"),
                        "rompimento_necessario_para_tp_pct": dados_sinal.get("rompimento_necessario_para_tp_pct"),
                        "entrada_na_zona_baixa": dados_sinal.get("entrada_na_zona_baixa"),
                        "entrada_estendida": dados_sinal.get("entrada_estendida"),
                        "alvo_plausivel": dados_sinal.get("alvo_plausivel"),
                        "confirmacao_compra": dados_sinal.get("confirmacao_compra"),
                        "reacao_atual": dados_sinal.get("reacao_atual"),
                        "pressao_vendedora_forte": dados_sinal.get("pressao_vendedora_forte"),
                        "decisao_ia": preview.get("analise_ia", {}).get("decisao"),
                        "qualidade_ia": preview.get("analise_ia", {}).get("qualidade"),
                        "aprendizado_versao": dados_sinal.get("aprendizado", {}).get("versao")
                    })

                    mensagem = f"""🚨 OPORTUNIDADE DETECTADA

Ativo: {preview['ativo']}
Grupo: {preview['grupo']}
Direção: {preview['direcao']}
Score: {preview['score']}
Entrada: {preview['entrada']}
Stop: {preview['stop']}
Alvo: {preview['alvo']}
Valor planejado: {preview['valor_usdt']} USDT

⚠️ Sinal com validade curta. Aprove somente se fizer sentido."""

                    tempo_sinal = int(time.time())

                    registrar_contexto_sinal_no_executor(
                        symbol,
                        preview,
                        tempo_sinal
                    )

                    enviar_telegram(
                        mensagem,
                        symbol=symbol,
                        preco=preview["entrada"],
                        tempo=tempo_sinal
                    )

                    ultimos_sinais[symbol] = agora

                time.sleep(3)

        except Exception as e:
            print("ERRO_MONITORAMENTO:", str(e))

        time.sleep(60)


@app.on_event("startup")
def iniciar_monitoramento():
    thread_mercado = threading.Thread(
        target=monitorar_mercado,
        name="vertical-market-monitor",
        daemon=True
    )
    thread_mercado.start()

    thread_aprendizado = threading.Thread(
        target=loop_aprendizado_automatico,
        name="vertical-learning-monitor",
        daemon=True
    )
    thread_aprendizado.start()
