import os
import re
from typing import Dict, Any

import httpx
from dotenv import load_dotenv

from app.infrastructure.supabase_db import db as cache

load_dotenv()

BASE_URL = "https://wdapi2.com.br/consulta"
# Every query spends a credit, so a plate is served from cache for a day
CACHE_SECONDS = 24 * 3600


class WDAPIClient:
    """Consulta de veículo por placa na API Placas (wdapi2.com.br): cadastro, FIPE e restrições."""

    def __init__(self):
        self.token = os.getenv("WDAPI_TOKEN")

    async def get_placa(self, placa: str, atualizar: bool = False) -> Dict[str, Any]:
        placa = re.sub(r"[^A-Z0-9]", "", (placa or "").upper())
        base = {"source": "API Placas", "placa": placa}
        if not self.token:
            return {**base, "status": "error", "message": "WDAPI_TOKEN não configurado"}

        cache_key = f"wdapi:{placa}"
        if not atualizar and (cached := await cache.get(cache_key)):
            return {**base, "status": "success", "cache": True, "data": cached}

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resposta = await client.get(f"{BASE_URL}/{placa}/{self.token}")
            data = resposta.json()
        except Exception as e:
            # The URL carries the token, so the exception text is not passed on
            print(f"[!] [WDAPI] Falha na consulta da placa {placa}: {type(e).__name__}")
            return {**base, "status": "error", "message": "API Placas indisponível"}

        mensagem = data.get("mensagemRetorno") if isinstance(data, dict) else None
        if resposta.status_code != 200 or mensagem != "Sem erros.":
            return {**base, "status": "error", "message": mensagem or f"HTTP {resposta.status_code}"}

        await cache.set(cache_key, data, expire=CACHE_SECONDS)
        print(f"[+] [WDAPI] {placa}: {data.get('marcaModelo')}")
        return {**base, "status": "success", "cache": False, "data": data}
