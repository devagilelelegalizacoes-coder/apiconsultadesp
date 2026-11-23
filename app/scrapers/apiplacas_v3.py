import os
import re
from typing import Dict, Any, Optional

import httpx
from dotenv import load_dotenv

from app.infrastructure.supabase_db import db as cache

load_dotenv()

BASE_URL = "https://wdapi2.com.br/v3"
# Every vehicle query is charged, so a plate is served from cache for a day
CACHE_SECONDS = 24 * 3600

# Route -> blocks it returns (from apiplacas.com.br/documentacao)
CONSULTAS = {
    "basico": "basico",
    "chassi": "chassi",
    "renavam": "renavam",
    "motor": "motor",
    "fipe": "fipe",
    "basico-fipe": "basico + fipe",
    "motor-chassi": "motor + chassi",
    "completa": "basico + motor + chassi + renavam",
}

PLACA_RE = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")


class APIPlacasError(Exception):
    def __init__(self, message: str, http_status: int = 502, code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code


class APIPlacasV3Client:
    """API PLACAS v3 (apiplacas.com.br): consultas por placa, saldo e logs, autenticadas por Bearer token."""

    def __init__(self):
        self.token = os.getenv("WDAPI_TOKEN_NOVO")

    @staticmethod
    def normalizar_placa(placa: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", (placa or "").upper())

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.token:
            raise APIPlacasError("WDAPI_TOKEN_NOVO não configurado", 500)
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resposta = await client.get(f"{BASE_URL}{path}", headers=headers, params=params)
        except Exception as e:
            print(f"[!] [APIPlacas v3] Falha em {path}: {type(e).__name__}")
            raise APIPlacasError("API Placas v3 indisponível", 503)

        try:
            data = resposta.json()
        except ValueError:
            data = None

        if resposta.status_code != 200 or not isinstance(data, dict) or data.get("error") is True:
            body = data if isinstance(data, dict) else {}
            message = body.get("message") or body.get("mensagem") or f"HTTP {resposta.status_code}"
            code = body.get("code") or body.get("codigo")
            # 4xx are caller problems (plate, balance, token); anything else is upstream
            status = resposta.status_code if 400 <= resposta.status_code < 500 else 502
            raise APIPlacasError(message, status, code)
        return data

    async def consultar(self, consulta: str, placa: str, atualizar: bool = False) -> Dict[str, Any]:
        if consulta not in CONSULTAS:
            raise APIPlacasError(f"Consulta inválida. Use: {', '.join(CONSULTAS)}", 400)
        placa = self.normalizar_placa(placa)
        if not PLACA_RE.match(placa):
            raise APIPlacasError("Placa inválida. Use o formato ABC1234 ou ABC1D23.", 400, "placa_invalida")

        base = {"source": "API Placas v3", "consulta": consulta, "blocos": CONSULTAS[consulta], "placa": placa}
        cache_key = f"apiplacas_v3:{consulta}:{placa}"
        if not atualizar and (cached := await cache.get(cache_key)):
            return {**base, "status": "success", "cache": True, "data": cached}

        data = await self._get(f"/{consulta}/{placa}")
        await cache.set(cache_key, data, expire=CACHE_SECONDS)
        print(f"[+] [APIPlacas v3] {consulta} {placa}")
        return {**base, "status": "success", "cache": False, "data": data}

    async def saldo(self) -> Dict[str, Any]:
        return await self._get("/saldo")

    async def logs(self, limite: int = 100, status: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limite": limite}
        if status:
            params["status"] = status
        return await self._get("/logs", params)
