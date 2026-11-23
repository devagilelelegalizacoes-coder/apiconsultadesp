from dotenv import load_dotenv
load_dotenv()

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

from fastapi import FastAPI, HTTPException, Depends, Header, Response
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from app.core.coordinator import QueryCoordinator
from app.infrastructure.supabase_db import db as cache
from app.security.encryption import encrypt_data, decrypt_data
from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.detran_pontuacao import DetranPontuacaoScraper
from app.scrapers.wdapi import WDAPIClient
from app.scrapers.apiplacas_v3 import APIPlacasV3Client, APIPlacasError
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.sefaz import SefazDiscoveryScraper
from app.scrapers.bradesco import BradescoScraper
from app.scrapers.autodoc import AutodocScraper, AutodocCadastro
from app.scrapers.uniao21 import Uniao21Client, Uniao21Error
from app.scrapers.crdd import CRDDClient, CRDDError
from app.infrastructure import documentos
from typing import Any
from app.core.budget_coordinator import BudgetCoordinator
from app.core.job_manager import JobManager
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from app.security.jwt_handler import create_access_token, decode_access_token, verify_password
from app.security.user_manager import user_manager


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

@app.get("/detran/pontuacao-cnh/{cnh}", tags=["Consultas Detran-RJ"])
async def query_detran_pontuacao_cnh(
    cnh: str,
    cpf: str,
    uf: str = "RJ",
    pgu: bool = False,
    token: str = Depends(get_current_user)
):
    """
    Consulta a pontuação da CNH no DETRAN-RJ: pontos e quantidade de autos dos
    últimos 5 anos e 12 meses, situação das infrações e processos de
    suspensão/cassação ativos. Informe pgu=true quando o número for do PGU.
    """
    cpf = "".join(filter(str.isdigit, cpf))
    cnh = "".join(filter(str.isdigit, cnh))
    if len(cpf) != 11:
        raise HTTPException(status_code=400, detail="CPF inválido")
    if not cnh or len(cnh) > 11:
        raise HTTPException(status_code=400, detail="Número da CNH/PGU inválido")
    try:
        scraper = DetranPontuacaoScraper()
        return await scraper.get_pontuacao_cnh(cpf, cnh, uf=uf, pgu=pgu)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/placa/{placa}", tags=["Consultas API Placas"])
async def query_api_placas(
    placa: str,
    atualizar: bool = False,
    token: str = Depends(get_current_user)
):
    """
    Consulta o veículo pela placa na API Placas (wdapi2.com.br): marca/modelo,
    chassi, FIPE, restrições (ex.: alienação fiduciária) e dados técnicos.
    Cada consulta gasta crédito, por isso a resposta fica em cache por 24h;
    use atualizar=true para forçar uma nova consulta.
    """
    placa_limpa = "".join(ch for ch in placa.upper() if ch.isalnum())
    if len(placa_limpa) != 7:
        raise HTTPException(status_code=400, detail="Placa inválida")
    result = await WDAPIClient().get_placa(placa_limpa, atualizar=atualizar)
    if result["status"] != "success":
        raise HTTPException(status_code=502, detail=result["message"])
    return result

@app.get("/placas-v3/saldo", tags=["Consultas API Placas v3"])
async def query_api_placas_v3_saldo(token: str = Depends(get_current_user)):
    """Saldo da conta na API Placas v3, preços por consulta e consultas disponíveis (sem cobrança)."""
    try:
        data = await APIPlacasV3Client().saldo()
    except APIPlacasError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)
    return {"source": "API Placas v3", "status": "success", "data": data}

@app.get("/placas-v3/logs", tags=["Consultas API Placas v3"])
async def query_api_placas_v3_logs(
    limite: int = 100,
    status: str | None = None,
    token: str = Depends(get_current_user)
):
    """Histórico de requisições das últimas 48h na API Placas v3 (sem cobrança). status: sucesso ou erro."""
    try:
        data = await APIPlacasV3Client().logs(limite=limite, status=status)
    except APIPlacasError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)
    return {"source": "API Placas v3", "status": "success", "data": data}

@app.get("/placas-v3/{consulta}/{placa}", tags=["Consultas API Placas v3"])
async def query_api_placas_v3(
    consulta: str,
    placa: str,
    atualizar: bool = False,
    token: str = Depends(get_current_user)
):
    """
    Consulta na API Placas v3 (token WDAPI_TOKEN_NOVO). consulta:
    basico, chassi, renavam, motor, fipe, basico-fipe, motor-chassi ou completa
    (completa = basico + motor + chassi + renavam, cobrada uma vez só).
    Cada consulta é cobrada, por isso fica em cache por 24h; atualizar=true força nova consulta.
    """
    try:
        return await APIPlacasV3Client().consultar(consulta, placa, atualizar=atualizar)
    except APIPlacasError as e:
        raise HTTPException(status_code=e.http_status, detail=e.message)

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

@app.get("/sefaz/descobrir-cpf/{renavam}", tags=["Consultas IPVASefaz-RJ"])
async def query_sefaz_descobrir_cpf(
    renavam: str,
    token: str = Depends(get_current_user)
):
    """
    Descobre o CPF/CNPJ e o nome do contribuinte atual do IPVA a partir do Renavam
    (Portal de Pagamentos da SEFAZ-RJ). Em caso de comunicação de venda, retorna o comprador.
    """
    renavam = "".join(filter(str.isdigit, renavam))
    if not renavam or len(renavam) > 11:
        raise HTTPException(status_code=400, detail="Renavam inválido")
    try:
        scraper = SefazDiscoveryScraper()
        result = await scraper.discovery_owner_document(renavam)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="CPF/CNPJ não encontrado para este Renavam")
    return {"source": "SEFAZ-RJ", "renavam": renavam.zfill(11), "status": "success", "data": result}

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

@app.post("/autodoc/cadastro", tags=["AutoDoc"])
async def autodoc_cadastro(
    dados: AutodocCadastro,
    token: str = Depends(get_current_user)
):
    """
    Cadastra o cliente e, opcionalmente, o veículo dele no AutoDoc, preenchendo as telas do sistema.
    Não duplica: se o CPF/CNPJ ou a placa já existirem, devolve o cadastro existente com criado=false.
    O tipo do veículo aceita a espécie/tipo do CRLV (ex.: "PASSAGEIRO MOTOCICLETA").
    """
    result = await AutodocScraper().cadastrar(dados)
    if result["status"] != "success":
        raise HTTPException(status_code=502, detail=result["message"])
    return result

# --- UNIÃO 21 (app.uniao21.com) ---

class Uniao21Recarga(BaseModel):
    cpf_cnpj: str = Field(description="CPF/CNPJ do pagador do PIX")
    valor: float = Field(description="Valor em reais (mínimo R$ 20,00; a partir de R$ 100,00 há bônus)")

class Uniao21Emissao(BaseModel):
    placas: list[str] = Field(default_factory=list, description="Uma ou mais placas (até o limite do serviço)")
    renavam: str | None = Field(None, description="Obrigatório em atpv_e")
    chassi: str | None = Field(None, description="Obrigatório em atpv_e_primeira_licenca")

class Uniao21Consulta(BaseModel):
    parametro: str = Field(description="Placa (ou o parâmetro que o serviço pede)")

async def _uniao21(coro):
    try:
        return await coro
    except Uniao21Error as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"União 21 indisponível: {e}")

@app.get("/uniao21/saldo", tags=["União 21"])
async def uniao21_saldo(token: str = Depends(get_current_user)):
    """Saldo atual e gasto do dia na União 21."""
    return await _uniao21(Uniao21Client().saldo())

@app.get("/uniao21/servicos", tags=["União 21"])
async def uniao21_servicos(token: str = Depends(get_current_user)):
    """Serviços liberados para a conta (emissões e consultas), com preço e campos exigidos."""
    return await _uniao21(Uniao21Client().servicos())

@app.post("/uniao21/recarga", tags=["União 21"])
async def uniao21_recarga(dados: Uniao21Recarga, token: str = Depends(get_current_user)):
    """Gera um PIX de recarga de saldo e devolve o código copia e cola e o pagamento_id."""
    return await _uniao21(Uniao21Client().recarregar(dados.cpf_cnpj, dados.valor))

@app.get("/uniao21/recarga/{pagamento_id}", tags=["União 21"])
async def uniao21_recarga_status(pagamento_id: int, token: str = Depends(get_current_user)):
    """Situação do PIX de recarga: pago=true quando o saldo já foi creditado."""
    return await _uniao21(Uniao21Client().status_recarga(pagamento_id))

@app.get("/uniao21/recarga/{pagamento_id}/qr", tags=["União 21"])
async def uniao21_recarga_qr(pagamento_id: int, token: str = Depends(get_current_user)):
    """QR Code (PNG) do PIX de recarga."""
    return Response(content=await _uniao21(Uniao21Client().qr_code_recarga(pagamento_id)), media_type="image/png")

@app.get("/uniao21/financeiro", tags=["União 21"])
async def uniao21_financeiro(status: str = "", page: int = 1, token: str = Depends(get_current_user)):
    """Histórico de recargas PIX. status: pago, pendente ou cancelado."""
    return await _uniao21(Uniao21Client().historico_financeiro(status=status, page=page))

@app.post("/uniao21/emitir/{servico}", tags=["União 21"])
async def uniao21_emitir(servico: str, dados: Uniao21Emissao, token: str = Depends(get_current_user)):
    """
    Emite um documento e debita o saldo (veja os serviços em /uniao21/servicos):
    crlv_e, reemissao, crv_e, atpv_e (placa + renavam), atpv_e_consulta_placa,
    atpv_e_primeira_licenca (chassi). O PDF sai em /uniao21/documentos/{doc_id}/pdf.
    """
    return await _uniao21(Uniao21Client().emitir(servico, dados.placas, renavam=dados.renavam, chassi=dados.chassi))

@app.get("/uniao21/solicitacoes", tags=["União 21"])
async def uniao21_solicitacoes(status: str = "", placa: str = "", page: int = 1, token: str = Depends(get_current_user)):
    """Minhas solicitações (emissões). status: processando, emitido, erro ou pendente."""
    return await _uniao21(Uniao21Client().solicitacoes(status=status, placa=placa, page=page))

@app.get("/uniao21/documentos/{doc_id}", tags=["União 21"])
async def uniao21_documento(doc_id: int, token: str = Depends(get_current_user)):
    """Situação de uma emissão e se o PDF já está pronto."""
    return await _uniao21(Uniao21Client().documento(doc_id))

@app.get("/uniao21/documentos/{doc_id}/pdf", tags=["União 21"])
async def uniao21_documento_pdf(doc_id: int, token: str = Depends(get_current_user)):
    """Baixa o PDF de uma emissão."""
    pdf = await _uniao21(Uniao21Client().pdf(doc_id))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="uniao21-{doc_id}.pdf"'})

@app.post("/uniao21/consultar/{servico}", tags=["União 21"])
async def uniao21_consultar(servico: str, dados: Uniao21Consulta, token: str = Depends(get_current_user)):
    """Executa uma consulta paga (ex.: Proprietário Atual), quando liberada para a conta."""
    return await _uniao21(Uniao21Client().consultar(servico, dados.parametro))

@app.get("/uniao21/consultas/{consulta_id}", tags=["União 21"])
async def uniao21_consulta(consulta_id: int, token: str = Depends(get_current_user)):
    """Resultado de uma consulta já feita: veículo e proprietário."""
    return await _uniao21(Uniao21Client().consulta(consulta_id))

@app.get("/uniao21/historico", tags=["União 21"])
async def uniao21_historico(tipo: str = "", periodo: str = "30d", page: int = 1, token: str = Depends(get_current_user)):
    """
    Movimentações do saldo com explicação de cada uma.
    tipo: saldo, documento, consulta (vazio = todas). periodo: 7d, 30d, 90d, 365d, todos.
    """
    return await _uniao21(Uniao21Client().historico(tipo=tipo, periodo=periodo, page=page))

# --- CRDD-RJ (portal.crddrj.com.br/despachante) ---

class CRDDRecarga(BaseModel):
    valor: float = Field(description="Valor em reais (mínimo R$ 10,00)")

class CRDDAsd(BaseModel):
    servicos: list[str] = Field(description="Códigos de /crdd/asd/servicos, ex.: ['INTENCAO_VENDA'] ou ['TRANSF_PROPRIEDADE']")
    contratante_cpf_cnpj: str = Field(description="Quem contrata o serviço; nem sempre é o proprietário, informe sempre")
    contratante_nome: str
    placa: str | None = None
    renavam: str | None = None
    chassi: str | None = None
    marca_modelo: str | None = None
    ano_fabricacao: str | None = None
    ano_modelo: str | None = None
    especie: str | None = None
    categoria: str | None = None
    tipo: str | None = None
    cor: str | None = None
    combustivel: str | None = None
    crv_nota_fiscal: str | None = None
    crv_data_emissao: str | None = Field(None, description="AAAA-MM-DD")
    uf_carro: str | None = None
    municipio_carro: str | None = Field(None, description="Nome do município do veículo")
    proprietario_nome: str = Field(description="Proprietário Atual = novo dono após a transferência (na ATPV, o comprador)")
    proprietario_cpf_cnpj: str
    proprietario_identidade: str | None = None
    proprietario_orgao: str | None = None
    proprietario_data_expedicao: str | None = Field(None, description="AAAA-MM-DD")
    proprietario_uf_identidade: str | None = None
    cep: str | None = Field(None, description="CEP do Proprietário Atual; endereço, bairro, UF e município são completados por ele quando não informados")
    endereco: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    uf: str | None = None
    municipio: str | None = None
    anterior_nome: str | None = Field(None, description="Proprietário Anterior = dono no CRLV antes da transferência (na ATPV, o vendedor)")
    anterior_cpf_cnpj: str | None = None
    observacao: str | None = None
    documentos: list[str] = Field(default_factory=list, description="Textos exatos dos checkboxes da aba Documentos")
    outros_documentos: str | None = None
    processo_administrativo_tipo: str | None = None
    rascunho_id: int | None = None
    gerar: bool = Field(False, description="false = salva como Rascunho (sem custo); true = Gera o ASD e debita R$ 3,00")

class CRDDCrlve(BaseModel):
    placa: str
    renavam: str
    cpf_proprietario: str
    nome_proprietario: str
    cpf_contratante: str = Field(description="Quem contrata o serviço; nem sempre é o proprietário")
    nome_contratante: str

class CRDDComunicacao(BaseModel):
    tipo: str = Field("ATPV-e", description="ATPV-e (digital) ou CRV/DUT (físico)")
    vendedor_cpf_cnpj: str
    vendedor_nome: str
    venda_estado: str = "RJ"
    venda_cidade: str = Field(description="Município da venda (nome ou código do CRDD)")
    venda_data: str = Field(description="AAAA-MM-DD")
    venda_valor: str = Field(description="Ex.: 19000 ou 19.000,00")
    comprador_cpf_cnpj: str
    comprador_nome: str
    comprador_celular: str | None = None
    comprador_end_cep: str
    comprador_end_logradouro: str
    comprador_end_numero: str
    comprador_end_bairro: str
    comprador_end_complemento: str | None = None
    comprador_end_estado: str = "RJ"
    comprador_end_cidade: str = Field(description="Nome do município ou código do CRDD")
    veiculo_placa: str
    veiculo_renavam: str
    veiculo_crv_numero: str
    veiculo_crv_codseg: str
    veiculo_crv_via: int = 1
    veiculo_crv_emissao: str = Field(description="AAAA-MM-DD")
    veiculo_crv_emissao_uf: str = "RJ"
    veiculo_ano_fab: str
    veiculo_ano_mod: str

class CRDDCancelarComunicacao(BaseModel):
    motivo: int = Field(description="1 registro indevido, 2 dados incorretos, 3 desistência, 8 leilão")
    observacoes: str
    codigo_seguranca_crv: str | None = None
    renavam: str | None = None

class CRDDIntencao(BaseModel):
    uf: str = "RJ"
    veiculo: dict = Field(description="placa, renavam, chassi, marca, modelo, cor, ano_fabricacao, ano_modelo, categoria, numero_crv, valor_venda, data_venda (AAAA-MM-DD)")
    vendedor: dict = Field(description="documento, nome, email, telefone, data_nascimento, endereco{cep, uf, cidade, bairro, logradouro, numero, complemento}")
    comprador: dict = Field(description="documento, nome, email, telefone, endereco{cep, uf, cidade, bairro, logradouro, numero, complemento} (endereço obrigatório)")

class CRDDIntencaoAcao(BaseModel):
    acao: str = Field(description="cancelar ou recuperar_atpv")
    duda: str | None = Field(None, description="Exigida no cancelamento quando o portal pedir")

async def _crdd(coro):
    try:
        return await coro
    except CRDDError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"CRDD indisponível: {e}")

def _pdf(conteudo: bytes, nome: str) -> Response:
    return Response(content=conteudo, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nome}.pdf"'})

async def _documento(tipo: str, referencia: Any, baixar, nome: str, placa: str | None = None,
                     descricao: str | None = None, atualizar: bool = False) -> Response:
    """
    Serve the PDF from the database; on the first request it is downloaded from the CRDD,
    compressed and stored. The database is a cache: if it is unavailable the PDF still goes out.
    """
    if not atualizar:
        try:
            if guardado := await documentos.buscar(tipo, referencia):
                return _pdf(guardado, nome)
        except Exception as e:
            print(f"[!] [Documentos] Leitura falhou ({tipo} {referencia}): {e}")
    original = await _crdd(baixar())
    pdf = documentos.comprimir_pdf(original)
    try:
        await documentos.salvar(tipo, referencia, pdf, placa=placa, descricao=descricao, tamanho_original=len(original))
    except Exception as e:
        print(f"[!] [Documentos] Gravação falhou ({tipo} {referencia}): {e}")
    return _pdf(pdf, nome)

@app.get("/crdd/documentos", tags=["CRDD-RJ"])
async def crdd_documentos(tipo: str = "", placa: str = "", limite: int = 50, token: str = Depends(get_current_user)):
    """Documentos já guardados no banco (tipo: asd, crlve, comunicacao, atpve), com tamanho original e comprimido."""
    try:
        return await documentos.listar(tipo, placa, limite)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Banco de documentos indisponível (rode sql/crdd_documentos.sql): {e}")

@app.get("/crdd/saldo", tags=["CRDD-RJ"])
async def crdd_saldo(token: str = Depends(get_current_user)):
    """Saldo da carteira pré-paga (soma de recargas e estornos menos os serviços) e os últimos lançamentos."""
    return await _crdd(CRDDClient().saldo())

@app.get("/crdd/extrato", tags=["CRDD-RJ"])
async def crdd_extrato(data_ini: str = "", data_fim: str = "", token: str = Depends(get_current_user)):
    """Lançamentos da carteira (AAAA-MM-DD). tipo: R recarga, M serviço debitado, C estorno."""
    return await _crdd(CRDDClient().extrato(data_ini or None, data_fim or None))

@app.get("/crdd/movimento", tags=["CRDD-RJ"])
async def crdd_movimento(placa: str = "", renavam: str = "", chassi: str = "", data_ini: str = "", data_fim: str = "",
                         token: str = Depends(get_current_user)):
    """Serviços cobrados na carteira, com estornos. Sem filtro, traz o ano corrente."""
    return await _crdd(CRDDClient().movimento(placa, renavam, chassi, data_ini, data_fim))

@app.post("/crdd/recarga", tags=["CRDD-RJ"])
async def crdd_recarga(dados: CRDDRecarga, token: str = Depends(get_current_user)):
    """Gera um PIX para colocar crédito na carteira: copia e cola, QR Code (base64) e txid."""
    return await _crdd(CRDDClient().recarregar(dados.valor))

@app.get("/crdd/recarga/{txid}", tags=["CRDD-RJ"])
async def crdd_recarga_status(txid: str, token: str = Depends(get_current_user)):
    """Situação do PIX de recarga: pago=true quando o crédito já entrou na carteira."""
    return await _crdd(CRDDClient().status_recarga(txid))

@app.get("/crdd/placa/{placa}", tags=["CRDD-RJ"])
async def crdd_placa(placa: str, token: str = Depends(get_current_user)):
    """Dados do veículo na base do CRDD (chassi, motor, ano, cor, espécie...). Sem custo."""
    return await _crdd(CRDDClient().buscar_placa(placa))

@app.get("/crdd/cep/{cep}", tags=["CRDD-RJ"])
async def crdd_cep(cep: str, token: str = Depends(get_current_user)):
    """Endereço e código do município do CRDD a partir do CEP."""
    return await _crdd(CRDDClient().buscar_cep(cep))

@app.get("/crdd/municipios", tags=["CRDD-RJ"])
async def crdd_municipios(termo: str, uf: str = "", token: str = Depends(get_current_user)):
    """Municípios do CRDD (id, nome, UF, código IBGE)."""
    return await _crdd(CRDDClient().municipios(termo, uf))

@app.get("/crdd/asd/servicos", tags=["CRDD-RJ"])
async def crdd_asd_servicos(token: str = Depends(get_current_user)):
    """Serviços que podem ir no ASD Digital do DETRAN-RJ."""
    return await _crdd(CRDDClient().asd_servicos())

@app.get("/crdd/asd", tags=["CRDD-RJ"])
async def crdd_asd_listar(placa: str = "", chassi: str = "", proprietario: str = "", page: int = 1,
                          token: str = Depends(get_current_user)):
    """Lista de ASD Digital (status, proprietário, serviço e selo_id para baixar o PDF)."""
    return await _crdd(CRDDClient().asd_listar(placa, chassi, proprietario, page))

@app.post("/crdd/asd", tags=["CRDD-RJ"])
async def crdd_asd_criar(dados: CRDDAsd, token: str = Depends(get_current_user)):
    """
    Cria um ASD Digital no DETRAN-RJ. Por padrão salva como RASCUNHO (sem custo);
    gerar=true gera o ASD e debita a carteira. O contratante (quem contrata o serviço) é informado
    à parte e nem sempre é o proprietário. Na ATPV: proprietário atual = comprador; proprietário anterior = vendedor.
    """
    cliente = CRDDClient()
    d = dados.model_dump()
    if d["cep"] and not d["endereco"]:
        endereco = await _crdd(cliente.buscar_cep(d["cep"]))
        d["endereco"] = " ".join(filter(None, [endereco.get("tipo"), endereco.get("logradouro")]))
        d["bairro"] = d["bairro"] or endereco.get("bairro")
        d["uf"] = d["uf"] or endereco.get("localidade_uf")
        d["municipio"] = d["municipio"] or endereco.get("localidade")
    campos = {
        "selo_cpf_cnpj": d["contratante_cpf_cnpj"], "selo_nome_razao": d["contratante_nome"],
        "selo_servicos": d["servicos"], "processo_administrativo_tipo": d["processo_administrativo_tipo"],
        "placa_documental": (d["placa"] or "").upper() or None, "renavam_documental": d["renavam"],
        "chassi_documental": (d["chassi"] or "").upper() or None, "crv_nota_fiscal": d["crv_nota_fiscal"],
        "data_carro": d["crv_data_emissao"], "marca_modelo": d["marca_modelo"], "ano_fabricacao": d["ano_fabricacao"], "ano_modelo": d["ano_modelo"],
        "especie": d["especie"], "categoria": d["categoria"], "tipo": d["tipo"], "cor": d["cor"],
        "combustivel": d["combustivel"], "uf_carro": d["uf_carro"], "municipio_carro": d["municipio_carro"],
        "beneficiario_proprietario": d["proprietario_nome"], "beneficiario_cpf_cnpj": d["proprietario_cpf_cnpj"],
        "beneficiario_identidade": d["proprietario_identidade"], "beneficiario_orgao": d["proprietario_orgao"],
        "beneficiario_data_expedicao": d["proprietario_data_expedicao"], "beneficiario_uf": d["proprietario_uf_identidade"],
        "beneficiario_cep": d["cep"], "beneficiario_endereco": d["endereco"], "beneficiario_complemento": d["complemento"],
        "beneficiario_bairro": d["bairro"], "beneficiario_uf_restricao": d["uf"],
        "beneficiario_municipio_restricao": (d["municipio"] or "").upper() or None,
        "beneficiario_proprietario_anterior": d["anterior_nome"], "beneficiario_cpf_cnpj_anterior": d["anterior_cpf_cnpj"],
        "beneficiario_observacao": d["observacao"], "documentos": d["documentos"],
        "outros_documentos": d["outros_documentos"], "rascunho_id": d["rascunho_id"],
    }
    resultado = await _crdd(cliente.asd_criar(campos, gerar=dados.gerar))
    if dados.gerar:
        # Keep the generated ASD in the database right away
        for registro in resultado.get("registros", []):
            if registro.get("selo_id") and registro.get("status", "").lower().startswith("conclu"):
                try:
                    pdf = await cliente.asd_pdf(registro["selo_id"])
                    resultado["documento"] = await documentos.salvar(
                        "asd", registro["selo_id"], pdf, placa=registro.get("placa"),
                        descricao=f"{registro.get('servico')} - {registro.get('proprietario')}")
                except Exception as e:
                    print(f"[!] [Documentos] ASD {registro['selo_id']} não foi guardado: {e}")
                break
    return resultado

@app.get("/crdd/asd/{selo_id}/pdf", tags=["CRDD-RJ"])
async def crdd_asd_pdf(selo_id: int, placa: str = "", atualizar: bool = False, token: str = Depends(get_current_user)):
    """PDF de um ASD Digital concluído (do banco; na primeira vez baixa do CRDD, comprime e guarda)."""
    return await _documento("asd", selo_id, lambda: CRDDClient().asd_pdf(selo_id), f"ASD_{placa or selo_id}",
                            placa=placa.upper() or None, atualizar=atualizar)

@app.get("/crdd/crlve", tags=["CRDD-RJ"])
async def crdd_crlve_listar(placa: str = "", cpf: str = "", situacao: str = "", page: int = 1,
                            token: str = Depends(get_current_user)):
    """
    Pedidos de CRLV-e. situacao: na_fila, confirmacao_pendente, asd_pendente_aprovacao,
    aguardando_documento, crlv_emitido. O id aparece quando o CRLV já foi emitido.
    """
    return await _crdd(CRDDClient().crlve_listar(placa, cpf, situacao, page))

@app.post("/crdd/crlve", tags=["CRDD-RJ"])
async def crdd_crlve_solicitar(dados: CRDDCrlve, token: str = Depends(get_current_user)):
    """Solicita a impressão do CRLV-e (debita R$ 14,90 da carteira)."""
    return await _crdd(CRDDClient().crlve_solicitar(**dados.model_dump()))

@app.get("/crdd/crlve/{crlve_id}/documento", tags=["CRDD-RJ"])
async def crdd_crlve_documento(crlve_id: int, token: str = Depends(get_current_user)):
    """Link do PDF do CRLV-e emitido (válido por 10 minutos)."""
    return await _crdd(CRDDClient().crlve_documento(crlve_id))

@app.get("/crdd/crlve/{crlve_id}/pdf", tags=["CRDD-RJ"])
async def crdd_crlve_pdf(crlve_id: int, placa: str = "", atualizar: bool = False, token: str = Depends(get_current_user)):
    """PDF do CRLV-e emitido (do banco; na primeira vez baixa do CRDD, comprime e guarda)."""
    return await _documento("crlve", crlve_id, lambda: CRDDClient().crlve_pdf(crlve_id), f"CRLVe_{placa or crlve_id}",
                            placa=placa.upper() or None, atualizar=atualizar)

@app.get("/crdd/comunicacao-venda", tags=["CRDD-RJ"])
async def crdd_comunicacao_listar(placa: str = "", renavam: str = "", crv: str = "", cpf_cnpj_vendedor: str = "",
                                  cpf_cnpj_comprador: str = "", data_venda: str = "",
                                  data_comunicacao_ini: str = "", data_comunicacao_fim: str = "",
                                  token: str = Depends(get_current_user)):
    """Acompanhamento das comunicações de venda (protocolo, situação, se pode cancelar)."""
    return await _crdd(CRDDClient().comunicacao_listar(
        placa=placa.upper(), renavam=renavam, crv=crv, cpf_cnpj_vendedor=cpf_cnpj_vendedor,
        cpf_cnpj_comprador=cpf_cnpj_comprador, data_venda=data_venda,
        data_comunicacao_ini=data_comunicacao_ini, data_comunicacao_fim=data_comunicacao_fim))

@app.post("/crdd/comunicacao-venda", tags=["CRDD-RJ"])
async def crdd_comunicacao_comunicar(dados: CRDDComunicacao, token: str = Depends(get_current_user)):
    """Cadastra e envia a comunicação de venda ao DETRAN-RJ (debita R$ 24,90 da carteira)."""
    return await _crdd(CRDDClient().comunicacao_comunicar(dados.model_dump()))

@app.get("/crdd/comunicacao-venda/{comunicacao_id}/comprovante", tags=["CRDD-RJ"])
async def crdd_comunicacao_comprovante(comunicacao_id: int, placa: str = "", atualizar: bool = False,
                                       token: str = Depends(get_current_user)):
    """PDF do comprovante da comunicação de venda (do banco; na primeira vez baixa do CRDD, comprime e guarda)."""
    return await _documento("comunicacao", comunicacao_id, lambda: CRDDClient().comunicacao_comprovante(comunicacao_id),
                            f"comunicacao-venda-{placa or comunicacao_id}", placa=placa.upper() or None, atualizar=atualizar)

@app.get("/crdd/comunicacao-venda/{comunicacao_id}/historico", tags=["CRDD-RJ"])
async def crdd_comunicacao_historico(comunicacao_id: int, token: str = Depends(get_current_user)):
    """Histórico da comunicação de venda (retornos do DETRAN)."""
    return await _crdd(CRDDClient().comunicacao_historico(comunicacao_id))

@app.post("/crdd/comunicacao-venda/{comunicacao_id}/cancelar", tags=["CRDD-RJ"])
async def crdd_comunicacao_cancelar(comunicacao_id: int, dados: CRDDCancelarComunicacao,
                                    token: str = Depends(get_current_user)):
    """Cancela uma comunicação de venda no DETRAN-RJ."""
    return await _crdd(CRDDClient().comunicacao_cancelar(comunicacao_id, dados.motivo, dados.observacoes,
                                                         dados.codigo_seguranca_crv, dados.renavam))

@app.get("/crdd/intencao-venda", tags=["CRDD-RJ"])
async def crdd_intencao_listar(placa: str = "", cpf: str = "", situacao: str = "", page: int = 1,
                               token: str = Depends(get_current_user)):
    """Intenções de venda (ATPV-e): situação, erro, link do ATPV (atpve_url/atpvd_url) e ações possíveis."""
    return await _crdd(CRDDClient().intencao_listar(placa, cpf, situacao, page))

@app.post("/crdd/intencao-venda", tags=["CRDD-RJ"])
async def crdd_intencao_cadastrar(dados: CRDDIntencao, token: str = Depends(get_current_user)):
    """Cadastra a intenção de venda e emite a ATPV-e (debita R$ 26,90 da carteira)."""
    return await _crdd(CRDDClient().intencao_cadastrar(dados.veiculo, dados.vendedor, dados.comprador, dados.uf))

@app.get("/crdd/intencao-venda/{intencao_id}/pdf", tags=["CRDD-RJ"])
async def crdd_intencao_pdf(intencao_id: int, atualizar: bool = False, token: str = Depends(get_current_user)):
    """PDF da ATPV-e (do banco; na primeira vez baixa do CRDD, comprime e guarda)."""
    info: dict = {}

    async def baixar():
        info.update(await CRDDClient().intencao_pdf(intencao_id))
        return info["pdf"]

    resposta = await _documento("atpve", intencao_id, baixar, f"ATPVe_{intencao_id}", atualizar=atualizar)
    if info.get("placa"):
        # Placa only becomes known after the download; record it for the documents listing
        try:
            documentos.db.client.table(documentos.TABELA).update({"placa": info["placa"]}) \
                .eq("tipo", "atpve").eq("referencia", str(intencao_id)).execute()
        except Exception:
            pass
    return resposta

@app.get("/crdd/intencao-venda/{intencao_id}/historico", tags=["CRDD-RJ"])
async def crdd_intencao_historico(intencao_id: int, page: int = 1, token: str = Depends(get_current_user)):
    """Eventos de cada etapa da intenção de venda."""
    return await _crdd(CRDDClient().intencao_historico(intencao_id, page))

@app.post("/crdd/intencao-venda/{intencao_id}/atualizar", tags=["CRDD-RJ"])
async def crdd_intencao_atualizar(intencao_id: int, token: str = Depends(get_current_user)):
    """Pede ao CRDD para sincronizar a situação com o DETRAN."""
    return await _crdd(CRDDClient().intencao_atualizar(intencao_id))

@app.post("/crdd/intencao-venda/{intencao_id}/acao", tags=["CRDD-RJ"])
async def crdd_intencao_acao(intencao_id: int, dados: CRDDIntencaoAcao, token: str = Depends(get_current_user)):
    """cancelar (com DUDA quando exigida) ou recuperar_atpv (obter o ATPV disponível no DETRAN; pode cobrar)."""
    return await _crdd(CRDDClient().intencao_acao(intencao_id, dados.acao, dados.duda))

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
    renavam: str,
    chassi: str | None = None,
    cpf: str | None = None,
    token: str = Depends(get_current_user)
):
    """
    Executa o workflow completo de consulta para orçamento:
    1. Detran Cadastro + descoberta do CPF/CNPJ na SEFAZ-RJ
    2. Nada Consta Apreendido (+ multas Detran)
    3. Apenas as fontes de débito apontadas no Nada Consta (GRT, IPVA, Dívida Ativa) + multas Bradesco

    Renavam é obrigatório. O CPF é descoberto na SEFAZ-RJ; informe-o apenas para
    forçar o documento usado nas consultas do Detran. O Chassi habilita o
    "nada consta apreendido"; sem ele, é buscado na API Placas (que também traz
    FIPE e restrições), e se não for encontrado todas as fontes de débito são consultadas.
    """
    try:
        coordinator = BudgetCoordinator()
        result = await coordinator.run_budget_query(placa, renavam=renavam, cpf=cpf, chassi=chassi, user="api_user")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no workflow de orçamento: {str(e)}")

@app.post("/consulta/orcamento/async/{placa}")
async def query_budget_async(
    placa: str,
    renavam: str,
    chassi: str | None = None,
    cpf: str | None = None,
    token: str = Depends(get_current_user)
):
    """
    Adiciona a consulta de orçamento na fila do banco de dados para ser processada pelo Worker.
    Retorna um job_id para consulta posterior.
    """
    try:
        manager = JobManager()
        job_id = await manager.create_job(placa=placa, renavam=renavam, cpf=cpf, chassi=chassi, query_type="orcamento")
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
