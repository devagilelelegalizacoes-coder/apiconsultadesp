import asyncio
import base64
import html
import os
import re
import unicodedata
from datetime import date
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://portal.crddrj.com.br"
LOGIN_PATH = "/despachante"
ORGAO_DETRAN_RJ = "1"
RECARGA_MINIMA = 10.0

# One logged-in session shared by every request (Laravel session cookie + CSRF token)
_sessao: Dict[str, Any] = {"client": None, "csrf": None}
_lock = asyncio.Lock()


class CRDDError(Exception):
    pass


def _texto(fragmento: str) -> str:
    sem_tags = re.sub(r"<[^>]+>", " ", fragmento or "")
    return " ".join(html.unescape(sem_tags).split())


def _digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _reais(valor: float) -> str:
    # 1234.5 -> "1.234,50"
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _valor_br(valor: Any) -> str:
    """Accepts 19000, 19000.5, "19000.50" or an already formatted "19.000,50"."""
    texto = str(valor).strip()
    if "," in texto:
        return texto
    return _reais(float(texto))


def _texto_comven(valor: str, limite: int) -> str:
    sem_acento = unicodedata.normalize("NFD", str(valor)).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9 ./&]", " ", sem_acento).split())[:limite]


def _alertas(pagina: str) -> Dict[str, List[str]]:
    """Flash messages and validation errors rendered by the portal after a form POST."""
    sucesso, erro = [], []
    for classe, corpo in re.findall(r'<div[^>]*class="[^"]*alert-(success|danger|warning)[^"]*"[^>]*>(.*?)</div>', pagina, re.S):
        texto = _texto(corpo).replace("×", "").strip()
        if texto:
            (sucesso if classe == "success" else erro).append(texto)
    return {"sucesso": sucesso, "erro": erro}


def _linhas_tabela(pagina: str) -> List[str]:
    corpo = re.search(r"<tbody[^>]*>(.*?)</tbody>", pagina, re.S)
    return re.findall(r"<tr[^>]*>(.*?)</tr>", corpo.group(1) if corpo else "", re.S)


def _celulas(linha: str) -> List[str]:
    return [_texto(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", linha, re.S)]


def _paginas(pagina: str, atual: int) -> int:
    numeros = [int(n) for n in re.findall(r"[?&](?:amp;)?page=(\d+)", pagina)]
    return max(numeros + [atual])


class CRDDClient:
    """Portal do Despachante do CRDD-RJ: carteira (saldo/recarga), ASD Digital, CRLV-e, comunicação e intenção de venda."""

    def __init__(self):
        self.login = os.getenv("CRDD_LOGIN")
        self.senha = os.getenv("CRDD_SENHA")

    # --- sessão ---

    async def _client(self) -> httpx.AsyncClient:
        if not self.login or not self.senha:
            raise CRDDError("CRDD_LOGIN/CRDD_SENHA não configurados")
        async with _lock:
            if _sessao["client"] is None:
                _sessao["client"] = httpx.AsyncClient(
                    base_url=BASE_URL, follow_redirects=True, timeout=120,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"})
                await self._login(_sessao["client"])
        return _sessao["client"]

    async def _login(self, client: httpx.AsyncClient):
        pagina = (await client.get(LOGIN_PATH)).text
        token = re.search(r'name="_token" value="([^"]+)"', pagina)
        resposta = await client.post(f"{LOGIN_PATH}/login", data={
            "_token": token.group(1) if token else "", "email": self.login, "senha": self.senha})
        if resposta.url.path.startswith(LOGIN_PATH):
            erros = _alertas(resposta.text)["erro"]
            raise CRDDError(f"Login no CRDD falhou: {erros[0] if erros else 'credenciais recusadas'}")
        self._guardar_csrf(resposta.text)
        print("[+] [CRDD] Login ok")

    @staticmethod
    def _guardar_csrf(pagina: str):
        if m := re.search(r'name="_token" value="([^"]+)"', pagina):
            _sessao["csrf"] = m.group(1)

    async def _request(self, metodo: str, path: str, **kwargs) -> httpx.Response:
        client = await self._client()
        headers = kwargs.pop("headers", {})
        for tentativa in range(2):
            resposta = await client.request(metodo, path, headers={"X-CSRF-TOKEN": _sessao["csrf"] or "", **headers}, **kwargs)
            # An expired session lands on the login page (or 419 for a stale CSRF): log in again once
            if (resposta.url.path.startswith(LOGIN_PATH) or resposta.status_code == 419) and tentativa == 0:
                async with _lock:
                    await self._login(client)
                for campo in ("data", "json"):
                    if isinstance(kwargs.get(campo), dict) and "_token" in kwargs[campo]:
                        kwargs[campo]["_token"] = _sessao["csrf"]
                continue
            if "text/html" in resposta.headers.get("content-type", ""):
                self._guardar_csrf(resposta.text)
            return resposta
        raise CRDDError("Sessão do CRDD não pôde ser renovada")

    async def _html(self, path: str, **params) -> str:
        resposta = await self._request("GET", path, params={k: v for k, v in params.items() if v not in (None, "")})
        resposta.raise_for_status()
        return resposta.text

    async def _json(self, metodo: str, path: str, **kwargs) -> Any:
        resposta = await self._request(metodo, path, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}, **kwargs)
        try:
            dados = resposta.json()
        except ValueError:
            raise CRDDError(f"Resposta inesperada do CRDD em {path} (HTTP {resposta.status_code})")
        if resposta.status_code >= 400 or (isinstance(dados, dict) and dados.get("error") is True):
            raise CRDDError(self._mensagem_erro(dados) or f"CRDD recusou a operação (HTTP {resposta.status_code})")
        return dados

    @staticmethod
    def _mensagem_erro(dados: Any) -> str:
        if not isinstance(dados, dict):
            return ""
        erros = [e for lista in (dados.get("errors") or {}).values() for e in (lista if isinstance(lista, list) else [lista])]
        mensagem = dados.get("message")
        if isinstance(mensagem, list):
            mensagem = " ".join(mensagem)
        return " ".join(erros) or (mensagem or "")

    async def _form_token(self, path: str) -> str:
        pagina = await self._html(path)
        token = re.search(r'name="_token" value="([^"]+)"', pagina)
        if not token:
            raise CRDDError(f"Formulário do CRDD em {path} não carregou")
        return token.group(1)

    # --- carteira ---

    async def extrato(self, data_ini: Optional[str] = None, data_fim: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lançamentos da carteira. tipo: R=recarga, M=débito de serviço, C=estorno (crédito), E=estorno."""
        dados = await self._json("POST", "/ajax/relSaldo", data={
            "data_ini": data_ini or "2020-01-01", "data_fim": data_fim or date.today().isoformat(), "usuario": ""})
        return dados if isinstance(dados, list) else []

    async def saldo(self) -> Dict[str, Any]:
        # The portal shows no balance figure; it is the running total of the whole wallet history
        lancamentos = await self.extrato()
        saldo = 0.0
        for item in lancamentos:
            valor = float(item.get("valor") or 0)
            if item.get("tipo") in ("R", "C"):
                saldo += valor
            elif item.get("tipo") == "M":
                saldo -= valor
        saldo = round(saldo, 2)
        return {"saldo": saldo, "saldo_formatado": f"R$ {_reais(saldo)}",
                "ultimos_lancamentos": lancamentos[:10], "total_lancamentos": len(lancamentos)}

    async def movimento(self, placa: str = "", renavam: str = "", chassi: str = "",
                        data_ini: str = "", data_fim: str = "") -> Any:
        """Serviços debitados da carteira (com estornos)."""
        if not any((placa, renavam, chassi, data_ini)):
            data_ini, data_fim = f"{date.today().year}-01-01", date.today().isoformat()
        return await self._json("POST", "/ajax/relMovimento", data={
            "placa": placa.upper(), "renavam": renavam, "chassi": chassi.upper(),
            "data_ini": data_ini, "data_fim": data_fim or date.today().isoformat(), "usuario": ""})

    async def recarregar(self, valor: float) -> Dict[str, Any]:
        if valor < RECARGA_MINIMA:
            raise CRDDError(f"O valor da recarga não pode ser inferior a R$ {_reais(RECARGA_MINIMA)}")
        token = await self._form_token("/carteira/recarga")
        resposta = await self._request("POST", "/carteira/recarga/emitirPix", data={
            "_token": token, "recarga_forma_pagamento": "PIX", "recarga_valor": _reais(valor)})
        pagina = resposta.text
        copia_cola = re.search(r"(00020101\d{2}[^\s\"'<]+)", pagina)
        txid = re.search(r'name=["\']?recargaPrepagoTxid["\']?[^>]*value="([^"]+)"', pagina)
        if not copia_cola or not txid:
            erros = _alertas(pagina)["erro"]
            raise CRDDError(erros[0] if erros else "O CRDD não gerou o PIX de recarga")
        qr = re.search(r'src="data:image/png;base64,([^"]+)"', pagina)
        return {"valor": round(valor, 2), "txid": txid.group(1), "pix_copia_cola": html.unescape(copia_cola.group(1)),
                "qr_code_base64": qr.group(1) if qr else None}

    async def status_recarga(self, txid: str) -> Dict[str, Any]:
        resposta = await self._request("POST", "/ajax/consultaPagamentoRecargaPrepago", data={"txid": txid})
        situacao = resposta.text.strip().strip('"')
        return {"txid": txid, "pago": situacao == "quitado", "situacao": situacao}

    # --- consultas auxiliares (gratuitas) ---

    async def buscar_placa(self, placa: str) -> Dict[str, Any]:
        placa = re.sub(r"[^A-Z0-9]", "", placa.upper())
        dados = await self._json("GET", f"/api/busca-placa/{placa}")
        if not isinstance(dados, dict) or str(dados.get("retorno")) != "1":
            raise CRDDError("Placa não encontrada no CRDD")
        return dados

    async def buscar_cep(self, cep: str) -> Dict[str, Any]:
        dados = await self._json("POST", "/ajax/searchCepCep", data={"term": _digitos(cep)})
        if not isinstance(dados, dict) or str(dados.get("retorno")) != "1":
            raise CRDDError(dados.get("retorno_msg") if isinstance(dados, dict) else "CEP não encontrado")
        return dados

    async def municipios(self, termo: str, uf: str = "") -> List[Dict[str, Any]]:
        dados = await self._json("POST", "/ajax/searchMunicipio", data={"term": termo, "municipioUF": uf.upper()})
        return [m for grupo in dados for m in (grupo if isinstance(grupo, list) else [grupo])] if isinstance(dados, list) else []

    async def _municipio_id(self, cidade: str, uf: str) -> int:
        if str(cidade).isdigit():
            return int(cidade)
        # The portal keeps city names upper-case and without accents ("NITEROI")
        nome = unicodedata.normalize("NFKD", _texto(cidade)).encode("ascii", "ignore").decode().upper()
        candidatos = await self.municipios(nome[:40], uf)
        exato = [m for m in candidatos if m.get("municipio", "").upper() == nome]
        if not (exato or candidatos):
            raise CRDDError(f"Município '{cidade}/{uf}' não encontrado no CRDD")
        return int((exato or candidatos)[0]["id"])

    # --- ASD Digital (gerarSelo) ---

    async def _abrir_orgao(self) -> str:
        token = await self._form_token("/gerarSelo")
        resposta = await self._request("POST", "/gerarSelo/selecionarOrgao", data={"_token": token, "orgao_id": ORGAO_DETRAN_RJ})
        return resposta.text

    async def asd_servicos(self) -> List[Dict[str, str]]:
        pagina = await self._abrir_orgao()
        bloco = re.search(r'<select[^>]*id="selo_servico".*?</select>', pagina, re.S)
        return [{"codigo": v, "descricao": _texto(t)}
                for v, t in re.findall(r'<option[^>]*value="([^"]+)"[^>]*>(.*?)</option>', bloco.group(0) if bloco else "", re.S)]

    async def asd_listar(self, placa: str = "", chassi: str = "", proprietario: str = "", page: int = 1) -> Dict[str, Any]:
        await self._abrir_orgao()
        pagina = await self._html("/gerarSelo", placa=placa.upper(), chassi=chassi.upper(), proprietario=proprietario, page=page)
        itens = []
        for linha in _linhas_tabela(pagina):
            c = _celulas(linha)
            if len(c) < 6:
                continue
            selo = re.search(r'data-seloid="(\d+)"', linha, re.I)
            rascunho = re.search(r'rascunho[^"\']*?[/=](\d+)', linha, re.I)
            itens.append({"status": c[0], "proprietario": c[1], "placa": c[2], "chassi": c[3], "servico": c[4],
                          "data_cadastro": c[5], "selo_id": int(selo.group(1)) if selo else None,
                          "rascunho_id": int(rascunho.group(1)) if rascunho else None})
        return {"page": page, "paginas": _paginas(pagina, page), "itens": itens}

    async def asd_criar(self, dados: Dict[str, Any], gerar: bool = False) -> Dict[str, Any]:
        """
        Salva um ASD Digital como rascunho (padrão) ou gera o ASD (gerar=True debita a carteira).
        dados usa os nomes de campo do portal: selo_cpf_cnpj, selo_nome_razao, selo_servicos (lista),
        placa_documental, renavam_documental, chassi_documental, beneficiario_* etc.
        """
        pagina = await self._abrir_orgao()
        token = re.search(r'name="_token" value="([^"]+)"', pagina)
        servicos = dados.pop("selo_servicos", None) or []
        if not servicos:
            raise CRDDError("Informe ao menos um serviço (selo_servicos)")
        documentos = dados.pop("documentos", None) or []
        form: List[tuple] = [("_token", token.group(1) if token else _sessao["csrf"]),
                             ("base64_selo", ""), ("rascunho_id", str(dados.pop("rascunho_id", "") or "")),
                             ("tempo_preenchimento", "60000")]
        form += [("selo_servicos[]", s) for s in servicos]
        form += [("documentos[]", d) for d in documentos]
        for campo, valor in dados.items():
            if valor is not None:
                form.append((campo, str(valor)))
        if gerar:
            form.append(("LGPD", "on"))
        else:
            form.append(("btnSalvarRascunho", ""))
        # httpx async takes repeated fields (selo_servicos[], documentos[]) as a dict of lists
        campos: Dict[str, Any] = {}
        for nome, valor in form:
            campos.setdefault(nome, []).append(valor)
        # The draft button posts to its own formaction; POST /gerarSelo always generates (and charges) the ASD
        destino = "/gerarSelo" if gerar else "/gerarSelo/rascunho"
        resposta = await self._request("POST", destino, data={k: v if len(v) > 1 else v[0] for k, v in campos.items()})
        alertas = _alertas(resposta.text)
        if alertas["erro"] and not alertas["sucesso"]:
            raise CRDDError(" ".join(alertas["erro"]))
        placa = str(dados.get("placa_documental") or "").upper()
        recentes = (await self.asd_listar(placa=placa))["itens"][:3] if placa else []
        if not gerar and any("gerad" in m.lower() for m in alertas["sucesso"]):
            raise CRDDError("O CRDD gerou o ASD em vez de salvar o rascunho: confira a lista e a carteira")
        return {"acao": "gerado" if gerar else "rascunho", "mensagens": alertas["sucesso"], "registros": recentes}

    async def asd_pdf(self, selo_id: int) -> bytes:
        resposta = await self._request("GET", "/ajax/visualizacaoSelo", params={"seloId": selo_id, "fileName": f"ASD_{selo_id}"})
        if "pdf" not in resposta.headers.get("content-type", ""):
            raise CRDDError("PDF do ASD não disponível")
        return resposta.content

    # --- CRLV-e ---

    async def crlve_listar(self, placa: str = "", cpf: str = "", situacao: str = "", page: int = 1) -> Dict[str, Any]:
        pagina = await self._html("/crlve", placa=placa.upper(), cpf=_digitos(cpf), situacao=situacao, page=page)
        itens = []
        for linha in _linhas_tabela(pagina):
            c = _celulas(linha)
            if len(c) < 6:
                continue
            doc = re.search(r"enviarCRLVe\('(\d+)'\)", linha)
            itens.append({"placa": c[0], "renavam": c[1], "cpf_proprietario": c[2], "nome_proprietario": c[3],
                          "situacao": c[4], "data_cadastro": c[5], "id": int(doc.group(1)) if doc else None})
        return {"page": page, "paginas": _paginas(pagina, page), "itens": itens}

    async def crlve_solicitar(self, placa: str, renavam: str, cpf_proprietario: str, nome_proprietario: str,
                              cpf_contratante: str, nome_contratante: str) -> Dict[str, Any]:
        """Solicita a impressão do CRLV-e (debita a carteira). O solicitante é o despachante logado."""
        renavam = _digitos(renavam).zfill(11)
        pagina = await self._html("/crlve")
        form_html = re.search(r'<form[^>]*id="form_padrao".*?</form>', pagina, re.S)
        if not form_html:
            raise CRDDError("Formulário de CRLV-e não carregou")
        # Keep the prefilled requester fields (despachante, UF, contact) exactly as the page sends them
        form = {n: html.unescape(v) for n, v in re.findall(r'<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"', form_html.group(0))}
        form.update({
            "placa": re.sub(r"[^A-Z0-9]", "", placa.upper()), "renavam": renavam,
            "cpf_proprietario": _digitos(cpf_proprietario), "nome_proprietario": nome_proprietario.upper(),
            # The contracting party is not always the owner, so it is never copied from the owner
            "cpf_contratante": _digitos(cpf_contratante), "nome_contratante": nome_contratante.upper()})
        resposta = await self._request("POST", "/crlve", data=form)
        alertas = _alertas(resposta.text)
        if alertas["erro"] and not alertas["sucesso"]:
            raise CRDDError(" ".join(alertas["erro"]))
        recentes = (await self.crlve_listar(placa=form["placa"]))["itens"][:3]
        return {"mensagens": alertas["sucesso"], "registros": recentes}

    async def crlve_documento(self, crlve_id: int) -> Dict[str, Any]:
        """Link (válido por 10 min) do PDF do CRLV-e emitido."""
        dados = await self._json("POST", "/crlve/documento", data={"id": crlve_id, "_token": _sessao["csrf"]})
        if not dados.get("url"):
            raise CRDDError("CRLV-e ainda em processamento" if dados.get("status") in ("aguardando_documento", "na_fila")
                            else "Documento do CRLV-e não disponível")
        return dados

    # --- Comunicação de venda ---

    async def comunicacao_listar(self, **filtros) -> Any:
        """Filtros: placa, renavam, crv, cpf_cnpj_vendedor, cpf_cnpj_comprador, data_venda, data_comunicacao_ini/fim (AAAA-MM-DD)."""
        return await self._json("POST", "/ajax/comunicacaoVendaAcompanhamentoFiltro",
                                json={k: v for k, v in filtros.items() if v})

    async def comunicacao_comunicar(self, dados: Dict[str, Any]) -> Dict[str, Any]:
        """
        Cadastra e envia a comunicação de venda ao DETRAN (debita a carteira).
        dados usa os nomes do portal (vendedor_*, comprador_*, venda_*, veiculo_*); venda_cidade e
        comprador_end_cidade aceitam o nome do município, que é convertido no código do CRDD.
        """
        pagina = await self._html("/comunicacaoVenda")
        solicitacao = re.search(r'name="solicitacao_id"[^>]*value="([^"]+)"', pagina)
        token = re.search(r'name="_token" value="([^"]+)"', pagina)
        form = {"_token": token.group(1) if token else _sessao["csrf"], "comunicacao_venda_id": "",
                "solicitacao_id": solicitacao.group(1) if solicitacao else "", "tipo": "ATPV-e",
                "venda_comprador_solicitante": "N", "veiculo_crv_via": "1", "lgpd": "1"}
        form.update({k: str(v) for k, v in dados.items() if v is not None})
        for campo, uf in (("venda_cidade", "venda_estado"), ("comprador_end_cidade", "comprador_end_estado")):
            if form.get(campo):
                form[campo] = str(await self._municipio_id(form[campo], form.get(uf, "RJ")))
        for campo in ("vendedor_cpf_cnpj", "comprador_cpf_cnpj", "comprador_end_cep", "veiculo_renavam",
                      "veiculo_crv_numero", "veiculo_crv_codseg", "comprador_celular"):
            if form.get(campo):
                form[campo] = _digitos(form[campo])
        # Same clean-up the page applies before sending to the DETRAN (upper-case, no accents, field limits)
        for campo, limite in (("vendedor_nome", 40), ("comprador_nome", 40), ("comprador_end_logradouro", 30),
                              ("comprador_end_numero", 5), ("comprador_end_bairro", 20), ("comprador_end_complemento", 20)):
            if form.get(campo):
                form[campo] = _texto_comven(form[campo], limite)
        if form.get("venda_valor"):
            form["venda_valor"] = _valor_br(form["venda_valor"])
        for campo in ("vendedor_tipo", "comprador_tipo"):
            if not form.get(campo):
                doc = form.get(campo.replace("_tipo", "_cpf_cnpj"), "")
                form[campo] = "J" if len(doc) == 14 else "F"
        form["veiculo_placa"] = re.sub(r"[^A-Z0-9]", "", form.get("veiculo_placa", "").upper())
        resposta = await self._request("POST", "/ajax/comunicacaoVendaCadastrarComunicar",
                                       headers={"Accept": "application/json"},
                                       files={k: (None, v) for k, v in form.items()})
        try:
            resultado = resposta.json()
        except ValueError:
            raise CRDDError(f"Resposta inesperada do CRDD (HTTP {resposta.status_code})")
        if resposta.status_code >= 400 or resultado.get("error"):
            raise CRDDError(self._mensagem_erro(resultado) or "O CRDD recusou a comunicação de venda")
        return resultado

    async def comunicacao_comprovante(self, comunicacao_id: int) -> bytes:
        dados = await self._json("POST", "/ajax/comunicacaoVendaComprovante", json={"comunicacao_venda_id": comunicacao_id})
        return base64.b64decode(dados["pdf"])

    async def comunicacao_historico(self, comunicacao_id: int) -> Dict[str, Any]:
        return await self._json("POST", "/ajax/comunicacaoVendaHistorico", json={"comunicacao_venda_id": comunicacao_id})

    async def comunicacao_cancelar(self, comunicacao_id: int, motivo: int, observacoes: str,
                                   codigo_seguranca_crv: Optional[str] = None, renavam: Optional[str] = None) -> Dict[str, Any]:
        """motivo: 1 registro indevido, 2 dados incorretos, 3 desistência, 8 leilão."""
        return await self._json("POST", "/ajax/comunicacaoVendaCancelamento", json={
            "comunicacao_venda_id": comunicacao_id, "motivo_cancelamento": motivo, "observacoes": observacoes,
            "codigo_seguranca_crv": codigo_seguranca_crv or None, "renavam_cancelamento": renavam or None})

    # --- Intenção de venda (ATPV-e) ---

    async def intencao_listar(self, placa: str = "", cpf: str = "", situacao: str = "", page: int = 1) -> Any:
        return await self._json("GET", "/intencao-venda", params={k: v for k, v in {
            "placa": placa.upper(), "cpf": _digitos(cpf), "situacao": situacao, "page": page}.items() if v})

    async def intencao_cadastrar(self, veiculo: Dict[str, Any], vendedor: Dict[str, Any], comprador: Dict[str, Any],
                                 uf: str = "RJ") -> Dict[str, Any]:
        """Cadastra a intenção de venda (ATPV-e) e debita a carteira. Campos como no formulário do portal."""
        pagina = await self._html("/intencao-venda")
        request_id = re.search(r'name="request_id"[^>]*value="([^"]+)"', pagina)
        token = re.search(r'name="_token" value="([^"]+)"', pagina)
        if not request_id:
            raise CRDDError("Formulário de intenção de venda não carregou")
        form: Dict[str, str] = {"_token": token.group(1) if token else _sessao["csrf"], "request_id": request_id.group(1),
                                "notificar_assinatura_partes": "0", "uf": uf}

        def achatar(prefixo: str, valores: Dict[str, Any]):
            for chave, valor in valores.items():
                nome = f"{prefixo}[{chave}]"
                if isinstance(valor, dict):
                    achatar(nome, valor)
                elif valor is not None:
                    form[nome] = str(valor)

        if veiculo.get("valor_venda") is not None:
            veiculo = {**veiculo, "valor_venda": _valor_br(veiculo["valor_venda"])}
        achatar("veiculo", veiculo)
        achatar("vendedor", vendedor)
        achatar("comprador", comprador)
        resposta = await self._request("POST", "/intencao-venda", headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                                       files={k: (None, v) for k, v in form.items()})
        try:
            resultado = resposta.json()
        except ValueError:
            raise CRDDError(f"Resposta inesperada do CRDD (HTTP {resposta.status_code})")
        if resposta.status_code >= 400:
            raise CRDDError(self._mensagem_erro(resultado) or "O CRDD recusou a intenção de venda")
        return resultado

    async def baixar(self, url: str) -> bytes:
        """Downloads a document link (S3 signed URL or a portal page that needs the session)."""
        resposta = await self._request("GET", url) if url.startswith(BASE_URL) or url.startswith("/") \
            else await (await self._client()).get(url)
        if resposta.status_code != 200 or not resposta.content.startswith(b"%PDF"):
            raise CRDDError("Documento não disponível para download")
        return resposta.content

    async def crlve_pdf(self, crlve_id: int) -> bytes:
        return await self.baixar((await self.crlve_documento(crlve_id))["url"])

    async def intencao_item(self, intencao_id: int, max_paginas: int = 20) -> Dict[str, Any]:
        for page in range(1, max_paginas + 1):
            dados = await self.intencao_listar(page=page)
            for item in dados.get("data", []):
                if item.get("id") == intencao_id:
                    return item
            if page >= (dados.get("meta") or {}).get("last_page", page):
                break
        raise CRDDError("Intenção de venda não encontrada")

    async def intencao_pdf(self, intencao_id: int) -> Dict[str, Any]:
        item = await self.intencao_item(intencao_id)
        url = item.get("atpvd_url") or item.get("atpve_url")
        if not url or item.get("cancelado"):
            raise CRDDError("ATPV-e ainda não disponível para esta intenção de venda")
        return {"pdf": await self.baixar(url), "placa": item.get("placa")}

    async def intencao_historico(self, intencao_id: int, page: int = 1) -> Any:
        return await self._json("GET", f"/intencao-venda/{intencao_id}/historico", params={"page": page})

    async def intencao_atualizar(self, intencao_id: int) -> Dict[str, Any]:
        await self._json("GET", f"/intencao-venda/{intencao_id}")
        return {"id": intencao_id, "message": "Atualização solicitada ao CRDD. Consulte a listagem em alguns segundos."}

    async def intencao_acao(self, intencao_id: int, acao: str, duda: Optional[str] = None) -> Any:
        """acao: cancelar (com duda quando o portal exigir) ou recuperar_atpv (obter ATPV no DETRAN)."""
        if acao not in ("cancelar", "recuperar_atpv"):
            raise CRDDError("acao deve ser cancelar ou recuperar_atpv")
        payload = {"acao": acao, **({"duda": _digitos(duda)} if duda else {})}
        return await self._json("POST", f"/intencao-venda/{intencao_id}/acoes", json=payload)
