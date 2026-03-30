import sys
import asyncio
import os

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # Log current loop for debugging
    try:
        current_loop = asyncio.get_event_loop()
        print(f"[*] Current asyncio loop: {type(current_loop).__name__}")
    except RuntimeError:
        print("[*] No event loop running yet, policy set.")

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from app.core.coordinator import QueryCoordinator
from app.infrastructure.supabase_db import db as cache
from app.security.encryption import encrypt_data, decrypt_data
from dotenv import load_dotenv
from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.bradesco import BradescoScraper
from app.scrapers.dataf5 import DataF5Scraper, DataF5Gravame
from app.core.budget_coordinator import BudgetCoordinator

load_dotenv()

app = FastAPI(
    title="High-Performance Vehicle Query API",
    description="API para consultas veiculares (DETRAN-RJ, SEFAZ-RJ, Bradesco) individuais ou consolidadas.",
    version="1.2.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory auth for demonstration, use OAuth2 for production
API_KEY = os.getenv("API_KEY", "super-secret-key")

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key

@app.on_event("startup")
async def startup_event():
    try:
        await cache.connect()
    except Exception as e:
        print(f"Warning: Could not connect to Supabase: {e}. Data storage will be disabled.")

@app.on_event("shutdown")
async def shutdown_event():
    try:
        await cache.close()
    except:
        pass

# --- ENDPOINTS INDIVIDUAIS ---

@app.get("/detran/cadastro/{placa}")
async def query_detran_cadastro(
    placa: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta apenas os dados cadastrais no DETRAN-RJ."""
    try:
        scraper = DetranRJScraper()
        result = await scraper.get_cadastro_data(placa)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detran/multas/{renavam}")
async def query_detran_multas(
    renavam: str,
    cpf: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta apenas nada consta de multas no DETRAN-RJ."""
    try:
        scraper = DetranRJScraper()
        result = await scraper.get_multas_data(renavam, cpf)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detran/nada-consta-apreendido/{placa}")
async def query_detran_nada_consta_apreendido(
    placa: str,
    chassi: str,
    renavam: str,
    doc_type: str,
    doc_num: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta nada consta de veículo apreendido no DETRAN-RJ."""
    try:
        scraper = DetranRJScraper()
        result = await scraper.get_nada_consta_apreendido_data(placa, chassi, renavam, doc_type, doc_num)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sefaz/{renavam}")
async def query_sefaz(
    renavam: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta apenas dados de IPVA na SEFAZ-RJ."""
    try:
        scraper = SefazRJScraper()
        result = await scraper.get_vehicle_data(renavam)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bradesco/{renavam}")
async def query_bradesco(
    renavam: str,
    cpf: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta apenas dados de GRT no Bradesco."""
    try:
        scraper = BradescoScraper()
        result = await scraper.get_vehicle_data(renavam, cpf)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dataf5/{placa}")
async def query_dataf5(
    placa: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta completa de placa no portal DataF5."""
    try:
        scraper = DataF5Scraper()
        result = await scraper.get_vehicle_data(placa)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dataf5/gravame/{chassi}")
async def query_dataf5_gravame(
    chassi: str,
    x_api_key: str = Depends(verify_api_key)
):
    """Consulta de gravame no portal DataF5 pelo Chassi."""
    try:
        scraper = DataF5Gravame()
        result = await scraper.get_gravame_data(chassi)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT CONSOLIDADO ---

@app.get("/veiculo/{renavam}")
async def query_vehicle(
    renavam: str, 
    cpf: str,
    placa: str | None = None,
    x_api_key: str = Depends(verify_api_key)
):
    """
    Consulta consolidada (DETRAN-RJ, SEFAZ-RJ e Bradesco).
    Descobre a placa automaticamente se não for fornecida.
    """
    try:
        coordinator = QueryCoordinator()
        result = await coordinator.run_parallel_queries(renavam, cpf, placa=placa, user="api_user")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

@app.get("/consulta/orcamento/{placa}")
async def query_budget(
    placa: str,
    x_api_key: str = Depends(verify_api_key)
):
    """
    Executa o workflow completo de consulta para orçamento (7 etapas).
    Inclui DataF5, Detran Cadastro, Multas transitadas, descoberta de CPF, Bradesco e Dívida Ativa.
    """
    try:
        coordinator = BudgetCoordinator()
        result = await coordinator.run_budget_query(placa, user="api_user")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no workflow de orçamento: {str(e)}")

@app.get("/health")
async def health_check():
    loop = asyncio.get_event_loop()
    return {
        "status": "healthy", 
        "database": "connected" if cache.client else "disconnected",
        "loop_type": str(type(loop).__name__)
    }
