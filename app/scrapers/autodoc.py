import asyncio
import os
import re
import unicodedata
from typing import Any, Dict, Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

from app.scrapers.base_scraper import BaseScraper

# One registration at a time: two parallel requests for the same CPF would both
# miss the duplicate check and create the customer twice
_lock = asyncio.Lock()


class AutodocCliente(BaseModel):
    nome: str
    email: str = Field(description="Obrigatório no AutoDoc e único por cliente")
    cpf_cnpj: str
    telefone: Optional[str] = None
    nascimento: Optional[str] = Field(None, description="DD/MM/AAAA")
    sexo: Optional[str] = Field(None, description="Masculino, Feminino ou Outro")
    rg: Optional[str] = None
    orgao_emissor: Optional[str] = None
    cep: Optional[str] = None
    cidade: Optional[str] = None
    rua: Optional[str] = None
    numero: Optional[str] = None
    bairro: Optional[str] = None
    uf: Optional[str] = None


class AutodocVeiculo(BaseModel):
    placa: str
    marca: str
    modelo: str
    ano_fabricacao: int
    ano_modelo: int
    tipo: str = Field(description='Espécie/tipo como no CRLV, ex.: "PASSAGEIRO MOTOCICLETA", "MISTO CAMIONETA"')
    renavam: Optional[str] = None
    cor: Optional[str] = None
    combustivel: Optional[str] = None
    categoria: Optional[str] = None
    chassi: Optional[str] = None
    numero_motor: Optional[str] = None
    potencia: Optional[str] = None
    uf: Optional[str] = None
    procedencia: Optional[str] = None
    proprietario_atual_nome: Optional[str] = None
    proprietario_atual_cpf_cnpj: Optional[str] = None
    proprietario_anterior_nome: Optional[str] = None
    proprietario_anterior_cpf_cnpj: Optional[str] = None


class AutodocCadastro(BaseModel):
    cliente: AutodocCliente
    veiculo: Optional[AutodocVeiculo] = None


def _digitos(valor: Optional[str]) -> str:
    return re.sub(r"\D", "", valor or "")


def _formatar_doc(doc: str) -> str:
    d = _digitos(doc)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return d


def _palavras(texto: str) -> set:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().upper()
    return set(re.findall(r"[A-Z]+", sem_acento))


def _escolher_tipo(tipo: str, opcoes: list) -> Optional[str]:
    """Maps the CRLV species/type (e.g. "MISTO CAMIONETA") to an AutoDoc option ("Camioneta (Misto, Especial)")."""
    alvo = _palavras(tipo)
    melhor, pontos = None, 0
    for opcao in opcoes:
        cabeca, _, resto = opcao.partition("(")
        if not (_palavras(cabeca) & alvo):
            continue
        p = 10 + len(_palavras(resto) & alvo)
        if p > pontos:
            melhor, pontos = opcao, p
    return melhor


class AutodocScraper(BaseScraper):
    """Cadastra cliente e veículo no AutoDoc preenchendo as próprias telas do sistema."""

    def __init__(self):
        super().__init__(use_proxy=False)
        self.base_url = os.getenv("AUTODOC_URL", "https://autodoc-olive.vercel.app").rstrip("/")
        self.login = os.getenv("AUTODOC_LOGIN")
        self.senha = os.getenv("AUTODOC_SENHA")

    async def cadastrar(self, dados: AutodocCadastro) -> Dict[str, Any]:
        base = {"source": "AutoDoc"}
        if not self.login or not self.senha:
            return {**base, "status": "error", "message": "AUTODOC_LOGIN/AUTODOC_SENHA não configurados"}
        async with _lock:
            try:
                page = await self.init_browser(use_stealth=False)
                await self._entrar(page)
                cliente = await self._garantir_cliente(page, dados.cliente)
                veiculo = await self._garantir_veiculo(page, cliente["id"], dados.veiculo) if dados.veiculo else None
                return {**base, "status": "success", "cliente": cliente, "veiculo": veiculo}
            except Exception as e:
                print(f"[!] [AutoDoc] Cadastro falhou: {e}")
                return {**base, "status": "error", "message": str(e)}
            finally:
                await self.close()

    async def _entrar(self, page):
        await page.goto(f"{self.base_url}/login")
        await page.fill("#email", self.login)
        await page.fill("#password", self.senha)
        await page.get_by_role("button", name="Entrar").click()
        try:
            await page.wait_for_url(lambda url: "/login" not in url, timeout=30000)
        except Exception:
            raise Exception(f"Login no AutoDoc falhou: {await self._mensagens(page) or 'credenciais recusadas'}")
        print("[+] [AutoDoc] Login ok")

    async def _mensagens(self, page) -> str:
        # Validation errors and toasts shown by the forms
        textos = await page.locator("[data-sonner-toast], [role=alert], .text-destructive, .text-red-500").all_inner_texts()
        return " | ".join(t.strip() for t in textos if t.strip())

    async def _buscar_cliente(self, page, doc: str) -> Optional[str]:
        # The search matches the CPF/CNPJ only with its punctuation
        await page.goto(f"{self.base_url}/customers?search={quote(_formatar_doc(doc))}&page=1")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        for link in await page.locator('a[href^="/customers/"]').all():
            if (await link.inner_text()).strip():
                return (await link.get_attribute("href")).rstrip("/").split("/")[-1]
        return None

    async def _garantir_cliente(self, page, c: AutodocCliente) -> Dict[str, Any]:
        if existente := await self._buscar_cliente(page, c.cpf_cnpj):
            print(f"[*] [AutoDoc] Cliente {c.cpf_cnpj} já cadastrado: {existente}")
            return {"id": existente, "criado": False}

        await page.goto(f"{self.base_url}/customers")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name="Novo Cliente").click()
        dialogo = page.locator("[role=dialog]")
        await dialogo.locator("#fullName").wait_for()

        documento = _digitos(c.cpf_cnpj)
        if len(documento) == 14:
            await dialogo.locator("select:not([name])").first.select_option("PJ")
            await page.wait_for_timeout(500)
        campo_doc = "#cnpj" if len(documento) == 14 and await dialogo.locator("#cnpj").count() else "#cpf"
        campos = {
            "#fullName": c.nome, "#email": c.email, "#phone": _digitos(c.telefone), campo_doc: documento,
            "#rg": c.rg, "#rg_issuer": c.orgao_emissor, "#cep": _digitos(c.cep), "#city": c.cidade,
            "#street": c.rua, "#number": c.numero, "#neighborhood": c.bairro, "#state": c.uf,
        }
        for seletor, valor in campos.items():
            if valor:
                # Typing (not fill) so the CPF/phone masks receive key events
                await dialogo.locator(seletor).fill("")
                await dialogo.locator(seletor).type(str(valor), delay=10)
        if c.nascimento and (m := re.match(r"(\d{2})/(\d{2})/(\d{4})", c.nascimento)):
            await dialogo.locator("#birthDate").fill(f"{m[3]}-{m[2]}-{m[1]}")
        if c.sexo:
            await dialogo.locator("select[name=sexo]").select_option(label=c.sexo.capitalize())

        await dialogo.get_by_role("button", name="Cadastrar Cliente").click()
        # The dialog stays open after a successful save, so the search is what confirms it
        await page.wait_for_timeout(5000)
        mensagens = await self._mensagens(page)
        novo = await self._buscar_cliente(page, c.cpf_cnpj)
        if not novo:
            raise Exception(f"AutoDoc recusou o cliente: {mensagens or 'não apareceu na busca após salvar'}")
        print(f"[+] [AutoDoc] Cliente criado: {novo}")
        return {"id": novo, "criado": True}

    async def _veiculo_na_garagem(self, page, placa: str) -> Optional[str]:
        await page.goto(f"{self.base_url}/admin/garage?q={placa}")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(1500)
        for linha in await page.locator("tbody tr").all_inner_texts():
            if placa in linha.upper():
                return re.sub(r"\s+", " ", linha).strip()
        return None

    async def _garantir_veiculo(self, page, cliente_id: str, v: AutodocVeiculo) -> Dict[str, Any]:
        placa = re.sub(r"[^A-Z0-9]", "", v.placa.upper())
        if linha := await self._veiculo_na_garagem(page, placa):
            print(f"[*] [AutoDoc] Placa {placa} já cadastrada")
            return {"placa": placa, "criado": False, "garagem": linha}

        await page.goto(f"{self.base_url}/customers/{cliente_id}")
        await page.wait_for_load_state("networkidle")
        await page.get_by_role("button", name=re.compile(r"^Cadastrar Ve")).first.click()
        dialogo = page.locator("[role=dialog]")
        await dialogo.locator("#plate").wait_for()

        seletor_tipo = dialogo.locator("select").first
        # Labels carry stray spaces, so the option is selected by its value
        opcoes = await seletor_tipo.locator("option").evaluate_all(
            "os => os.map(o => ({texto: o.textContent.trim(), valor: o.value}))")
        tipo = _escolher_tipo(v.tipo, [o["texto"] for o in opcoes])
        if not tipo:
            raise Exception(f"Tipo de veículo '{v.tipo}' não corresponde a nenhuma opção do AutoDoc: {[o['texto'] for o in opcoes]}")
        await seletor_tipo.select_option(value=next(o["valor"] for o in opcoes if o["texto"] == tipo))

        campos = {
            "#plate": placa, "#renavam": _digitos(v.renavam), "#brand": v.marca, "#model": v.modelo,
            "#manufacturing_year": v.ano_fabricacao, "#model_year": v.ano_modelo, "#color": v.cor,
            "#fuel": v.combustivel, "#category": v.categoria, "#chassis": (v.chassi or "").upper(),
            "#numero_motor": v.numero_motor, "#potencia": v.potencia, "#uf": v.uf, "#procedencia": v.procedencia,
        }
        for seletor, valor in campos.items():
            if valor:
                await dialogo.locator(seletor).fill(str(valor))

        proprietarios = {
            "#current_owner_name": v.proprietario_atual_nome, "#current_owner_cpf": _digitos(v.proprietario_atual_cpf_cnpj),
            "#previous_owner_name": v.proprietario_anterior_nome, "#previous_owner_cpf": _digitos(v.proprietario_anterior_cpf_cnpj),
        }
        if any(proprietarios.values()):
            await dialogo.get_by_text(re.compile(r"^Hist.rico e Propriet")).click()
            await dialogo.locator("#current_owner_name").wait_for()
            for seletor, valor in proprietarios.items():
                if valor:
                    await dialogo.locator(seletor).fill(valor)

        await dialogo.get_by_role("button", name=re.compile(r"^Salvar Ve")).click()
        await page.wait_for_timeout(5000)
        mensagens = await self._mensagens(page)
        linha = await self._veiculo_na_garagem(page, placa)
        if not linha:
            raise Exception(f"AutoDoc recusou o veículo: {mensagens or 'não apareceu na Garagem após salvar'}")
        print(f"[+] [AutoDoc] Veículo {placa} cadastrado (tipo {tipo})")
        return {"placa": placa, "criado": True, "tipo": tipo, "garagem": linha}
