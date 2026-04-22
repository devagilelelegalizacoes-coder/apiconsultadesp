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
from app.core.job_manager import JobManager
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from app.security.jwt_handler import create_access_token, decode_access_token, verify_password
from app.security.user_manager import user_manager

load_dotenv()

app = FastAPI(
    title="CONSULTA FACIL VEICULAR DESPACHANTE 2.0 API",
    description="API para consultas veiculares (DETRAN-RJ, SEFAZ-RJ, Bradesco) individuais ou consolidadas.",
    version="1.0.0"
)

app.add_middleware(

    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security Configuration
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"})
    user = await user_manager.get_user(payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# ========================================
# LOGIN E GERENCIAMENTO DE USUÁRIOS
# ========================================

@app.post("/token", tags=["Login"])
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await user_manager.get_user(form_data.username)
    if not user or not verify_password(form_data.password, user.get("hashed_password")):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    
    access_token = create_access_token(data={"sub": user["username"], "role": user.get("role", "user")})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/auth/register", tags=["Login"])
async def register_user(username: str, password: str, current_user: dict = Depends(get_current_user)):
    """Only existing active users can register new users (administrative)."""
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to create users")
    
    existing = await user_manager.get_user(username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already registered")
        
    result = await user_manager.create_user(username, password)
    if result:
        return {"status": "success", "message": f"User {username} created."}
    raise HTTPException(status_code=500, detail="Error creating user")

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

@app.get("/detran/cadastro/{placa}", tags=["Consultas Detran-RJ"])
async def query_detran_cadastro(
    placa: str,
    token: str = Depends(get_current_user)
):
    """Consulta apenas os dados cadastrais no DETRAN-RJ."""
    try:
        scraper = DetranRJScraper()
        result = await scraper.get_cadastro_data(placa)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detran/multas/{renavam}", tags=["Consultas Detran-RJ"])
async def query_detran_multas(
    renavam: str,
    cpf: str,
    token: str = Depends(get_current_user)
):
    """Consulta apenas nada consta de multas no DETRAN-RJ."""
    try:
        scraper = DetranRJScraper()
        result = await scraper.get_multas_data(renavam, cpf)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/detran/nada-consta-apreendido/{placa}", tags=["Consultas Detran-RJ"])
async def query_detran_nada_consta_apreendido(
    placa: str,
    chassi: str,
    renavam: str,
    doc_type: str,
    doc_num: str,
    token: str = Depends(get_current_user)
):
    """Consulta nada consta de veículo apreendido no DETRAN-RJ."""
    try:
        scraper = DetranRJScraper()
        result = await scraper.get_nada_consta_apreendido_data(placa, chassi, renavam, doc_type, doc_num)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sefaz/{renavam}", tags=["Consultas IPVASefaz-RJ"])
async def query_sefaz(
    renavam: str,
    token: str = Depends(get_current_user)
):
    """Consulta apenas dados de IPVA na SEFAZ-RJ."""
    try:
        scraper = SefazRJScraper()
        result = await scraper.get_vehicle_data(renavam)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bradesco/grt/{renavam}", tags=["Consultas Bradesco"])
async def query_bradesco_grt(
    renavam: str,
    cpf: str,
    token: str = Depends(get_current_user)
):
    """Consulta débitos de GRT no Bradesco."""
    try:
        scraper = BradescoScraper()
        result = await scraper.get_grt_debts(renavam, cpf)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/bradesco/multas/{renavam}", tags=["Consultas Bradesco"])
async def query_bradesco_multas(
    renavam: str,
    cpf: str,
    token: str = Depends(get_current_user)
):
    """Consulta multas (GRM) no Bradesco."""
    try:
        scraper = BradescoScraper()
        result = await scraper.get_fines_data(renavam, cpf)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dataf5/{placa}", tags=["Consultas completas com DataF5"])
async def query_dataf5(
    placa: str,
    token: str = Depends(get_current_user)
):
    """Consulta completa de placa no portal DataF5."""
    try:
        scraper = DataF5Scraper()
        result = await scraper.get_vehicle_data(placa)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dataf5/gravame/{chassi}", tags=["Consultas completas com DataF5"])
async def query_dataf5_gravame(
    chassi: str,
    token: str = Depends(get_current_user)
):
    """Consulta de gravame no portal DataF5 pelo Chassi."""
    try:
        scraper = DataF5Gravame()
        result = await scraper.get_gravame_data(chassi)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- ENDPOINT CONSOLIDADO ---

@app.get("/veiculo/{renavam}", tags=["Consulta debitos com Renavam placa e cpf"])
async def query_vehicle(
    renavam: str, 
    cpf: str,
    placa: str | None = None,
    token: str = Depends(get_current_user)
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

@app.get("/consulta/orcamento/{placa}", tags=["Consula orçamento com placa"])
async def query_budget(
    placa: str,
    token: str = Depends(get_current_user)
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

@app.post("/consulta/orcamento/async/{placa}")
async def query_budget_async(
    placa: str,
    token: str = Depends(get_current_user)
):
    """
    Adiciona a consulta de orçamento na fila do banco de dados para ser processada pelo Worker.
    Retorna um job_id para consulta posterior.
    """
    try:
        manager = JobManager()
        job_id = await manager.create_job(placa=placa, query_type="orcamento")
        return {"status": "accepted", "job_id": job_id, "message": "Consulta adicionada à fila."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao adicionar na fila: {str(e)}")

@app.get("/consulta/status/{job_id}")
async def get_job_status(
    job_id: str,
    token: str = Depends(get_current_user)
):
    """
    Verifica o status e obtém o resultado de uma consulta na fila.
    """
    try:
        manager = JobManager()
        status_data = await manager.get_job_status(job_id)
        return status_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar status: {str(e)}")

@app.get("/")
async def root():
    return {
        "title": "Veículo API",
        "description": "High-performance API for vehicle data scraping and consultation.",
        "version": "1.2.0",
        "health_check": "/health",
        "documentation": "/docs"
    }

@app.get("/health")
async def health_check():
    loop = asyncio.get_event_loop()
    return {
        "status": "healthy", 
        "database": "connected" if cache.client else "disconnected",
        "loop_type": str(type(loop).__name__)
    }
