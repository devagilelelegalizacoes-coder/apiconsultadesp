import asyncio
import base64
import html
import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://app.uniao21.com"
# The portal's own JS sends this header; without it the module actions answer with HTML instead of JSON
_XHR = {"X-Requested-With": "fetch"}
TIPOS_HISTORICO = ("", "saldo", "documento", "consulta")
PERIODOS_HISTORICO = ("7d", "30d", "90d", "365d", "todos")

# One logged-in session shared by every request (login is a form POST guarded by CSRF)
_sessao: Dict[str, Any] = {"client": None, "csrf": None}
_lock = asyncio.Lock()


class Uniao21Error(Exception):
    pass


def _texto(fragmento: str) -> str:
    sem_tags = re.sub(r"<[^>]+>", " ", fragmento or "")
    return " ".join(html.unescape(sem_tags).split())


def _attrs(tag: str) -> Dict[str, str]:
    return {k: html.unescape(v) for k, v in re.findall(r'([\w-]+)\s*=\s*"([^"]*)"', tag)}


def _chave(titulo: str) -> str:
    # "Veículo" -> "veiculo"
    sem_acento = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", sem_acento).strip("_")


def _reais(texto: str) -> Optional[float]:
    m = re.search(r"-?[\d.]+,\d{2}", texto or "")
    return float(m.group().replace(".", "").replace(",", ".")) if m else None


def _linhas_detalhe(pagina: str) -> Dict[str, Dict[str, str]]:
    """Detail pages are cards of "solic-detalhe-row" label/value pairs under an h2 section title."""
    secoes: Dict[str, Dict[str, str]] = {}
    for card in re.findall(r'<div class="solic-detalhe-card[^"]*">(.*?)(?=<div class="solic-detalhe-card|</main>)', pagina, re.S):
        titulo = re.search(r'<h2[^>]*>(.*?)</h2>', card, re.S)
        linhas = re.findall(r'solic-detalhe-label">(.*?)</span>\s*<span class="solic-detalhe-valor[^"]*">(.*?)</span>', card, re.S)
        if titulo and linhas:
            secoes[_texto(titulo.group(1))] = {_texto(k): _texto(v) for k, v in linhas}
    return secoes


def _paginas(pagina: str, atual: int) -> int:
    numeros = [int(n) for n in re.findall(r"[?&](?:amp;)?page=(\d+)", pagina)]
    return max(numeros + [atual])


class Uniao21Client:
    """Acesso à União 21 (app.uniao21.com): saldo, recarga PIX, emissões, consultas e históricos."""

    def __init__(self):
        self.usuario = os.getenv("UNIAO_21")
        self.senha = os.getenv("UNIAO_21_PASS")

    # --- sessão ---

    async def _client(self) -> httpx.AsyncClient:
        if not self.usuario or not self.senha:
            raise Uniao21Error("UNIAO_21/UNIAO_21_PASS não configurados")
        async with _lock:
            if _sessao["client"] is None:
                _sessao["client"] = httpx.AsyncClient(
                    base_url=BASE_URL, follow_redirects=True, timeout=120,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                await self._login(_sessao["client"])
        return _sessao["client"]

    async def _login(self, client: httpx.AsyncClient):
        pagina = (await client.get("/login")).text
        csrf = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', pagina)
        # Flask-WTF on HTTPS also checks the Referer, otherwise it answers "Sessão expirada"
        resposta = await client.post("/login", headers={"Referer": BASE_URL + "/login", "Origin": BASE_URL}, data={
            "csrf_token": csrf.group(1) if csrf else "", "username": self.usuario, "password": self.senha, "submit": "Entrar"})
        if resposta.url.path == "/login":
            erro = re.search(r'class="[^"]*(?:alert|flash)[^"]*"[^>]*>\s*([^<]+)', resposta.text)
            raise Uniao21Error(f"Login na União 21 falhou: {erro.group(1).strip() if erro else 'credenciais recusadas'}")
        self._guardar_csrf(resposta.text)
        print("[+] [Uniao21] Login ok")

    def _guardar_csrf(self, pagina: str):
        if m := re.search(r'<meta name="csrf-token" content="([^"]+)"', pagina):
            _sessao["csrf"] = m.group(1)

    async def _request(self, metodo: str, path: str, **kwargs) -> httpx.Response:
        client = await self._client()
        for tentativa in range(2):
            headers = {"Referer": BASE_URL + "/dashboard", **kwargs.pop("headers", {})}
            resposta = await client.request(metodo, path, headers=headers, **kwargs)
            # An expired session is redirected to the login page: log in again once and repeat
            if resposta.url.path == "/login" and tentativa == 0:
                async with _lock:
                    await self._login(client)
                if "data" in kwargs and "csrf_token" in kwargs["data"]:
                    kwargs["data"]["csrf_token"] = _sessao["csrf"]
                kwargs["headers"] = headers
                continue
            if "text/html" in resposta.headers.get("content-type", ""):
                self._guardar_csrf(resposta.text)
            return resposta
        raise Uniao21Error("Sessão da União 21 não pôde ser renovada")

    async def _html(self, path: str, **params) -> str:
        resposta = await self._request("GET", path, params={k: v for k, v in params.items() if v not in (None, "")})
        if resposta.status_code == 404:
            raise Uniao21Error("Não encontrado na União 21")
        resposta.raise_for_status()
        return resposta.text

    async def _json(self, metodo: str, path: str, **kwargs) -> Dict[str, Any]:
        resposta = await self._request(metodo, path, headers=_XHR, **kwargs)
        try:
            return resposta.json()
        except ValueError:
            raise Uniao21Error(f"Resposta inesperada da União 21 (HTTP {resposta.status_code})")

    # --- saldo e serviços ---

    async def saldo(self) -> Dict[str, Any]:
        dados = await self._json("GET", "/historico/dados", params={"periodo": "7d"})
        pagina = await self._html("/conta")
        gasto = re.search(r"Gasto hoje:\s*(?:<[^>]+>\s*)*(R\$\s*[\d.,]+)", pagina)
        return {"saldo": dados.get("saldo_atual"), "saldo_fmt": dados.get("saldo_atual_fmt"),
                "gasto_hoje_fmt": gasto.group(1) if gasto else None}

    async def servicos(self) -> List[Dict[str, Any]]:
        """Modules offered to this account: document emissions on the dashboard, paid queries on /consultas/."""
        servicos = []
        for path in ("/dashboard", "/consultas/"):
            pagina = await self._html(path)
            for m in re.finditer(r"<form[^>]*>.*?</form>", pagina, re.S):
                form = m.group(0)
                tag = _attrs(re.match(r"<form[^>]*>", form).group(0))
                classes = tag.get("class", "")
                if "js-module-doc-form" not in classes and "js-module-consulta-form" not in classes:
                    continue
                campos = [a["name"] for a in map(_attrs, re.findall(r"<(?:input|textarea)[^>]*>", form))
                          if a.get("name") not in (None, "csrf_token", "multiplas", "m", "tipo")]
                ocultos = {a["name"]: a.get("value", "") for a in map(_attrs, re.findall(r"<input[^>]*>", form))
                           if a.get("type") == "hidden" and a.get("name") in ("m", "tipo")}
                servicos.append({
                    "servico": tag["action"].rstrip("/").split("/")[-1],
                    "tipo": "consulta" if "js-module-consulta-form" in classes else "documento",
                    "titulo": tag.get("data-pdf-titulo") or tag.get("data-titulo") or _texto(pagina[max(0, m.start() - 300):m.start()])[-60:],
                    "preco": float(tag.get("data-preco") or 0),
                    "campos": list(dict.fromkeys(campos)),
                    "multiplas": tag.get("data-multiplas") == "1",
                    "limite": int(tag.get("data-limite") or 1),
                    "action": tag["action"],
                    **({"parametros_fixos": ocultos} if ocultos else {}),
                })
        return servicos

    async def _servico(self, servico: str, tipo: str) -> Dict[str, Any]:
        disponiveis = await self.servicos()
        achado = next((s for s in disponiveis if s["servico"] == servico and s["tipo"] == tipo), None)
        if not achado:
            nomes = [s["servico"] for s in disponiveis if s["tipo"] == tipo]
            raise Uniao21Error(f"Serviço '{servico}' não está disponível nesta conta. Disponíveis: {nomes or 'nenhum'}")
        return achado

    # --- emissões (documentos) ---

    async def emitir(self, servico: str, placas: List[str], renavam: Optional[str] = None, chassi: Optional[str] = None) -> Dict[str, Any]:
        """Each plate is a separate request, as the portal does for "múltiplas placas"."""
        info = await self._servico(servico, "documento")
        placas = [re.sub(r"[^A-Z0-9]", "", p.upper()) for p in placas if p and p.strip()]
        if "chassi" in info["campos"]:
            chassi = re.sub(r"\s", "", (chassi or "").upper())
            if not re.fullmatch(r"[A-Z0-9]{17}", chassi):
                raise Uniao21Error("Informe o chassi com 17 caracteres")
            pedidos = [{"chassi": chassi}]
        else:
            if not placas:
                raise Uniao21Error("Informe ao menos uma placa")
            if len(placas) > info["limite"]:
                raise Uniao21Error(f"No máximo {info['limite']} placa(s) por pedido em {servico}")
            if "renavam" in info["campos"] and not renavam:
                raise Uniao21Error(f"{servico} exige o Renavam")
            pedidos = [{"placa": p, **({"renavam": re.sub(r'\D', '', renavam)} if "renavam" in info["campos"] else {})} for p in placas]

        resultados = []
        for pedido in pedidos:
            corpo = await self._json("POST", info["action"], data={"csrf_token": _sessao["csrf"] or "", **pedido})
            resultados.append({
                **pedido, "ok": bool(corpo.get("ok")), "doc_id": corpo.get("doc_id"),
                "pdf_pronto": corpo.get("pdf_pronto"), "processando": corpo.get("processando"),
                "erro": None if corpo.get("ok") else (corpo.get("erro") or corpo.get("mensagem") or "Não foi possível emitir"),
                "saldo_fmt": corpo.get("saldo_formatado"),
            })
            print(f"[*] [Uniao21] {servico} {pedido}: {'ok' if corpo.get('ok') else resultados[-1]['erro']}")
        return {"servico": servico, "titulo": info["titulo"], "preco": info["preco"], "resultados": resultados}

    async def documento(self, doc_id: int) -> Dict[str, Any]:
        pagina = await self._html(f"/documentos/{doc_id}")
        status = await self._json("GET", "/documentos/historico/pendencias", params={"ids": doc_id})
        item = next(iter(status.get("items") or []), {})
        titulo = re.search(r'solic-card-eyebrow">(.*?)<', pagina)
        return {
            "id": doc_id, "titulo": _texto(titulo.group(1)) if titulo else None,
            "status": item.get("status"), "pdf_pronto": bool(item.get("pdf")), "erro": item.get("erro") or None,
            "detalhes": _linhas_detalhe(pagina).get("Informações", {}),
        }

    async def pdf(self, doc_id: int) -> bytes:
        resposta = await self._request("GET", f"/documentos/{doc_id}/download", headers={**_XHR, "Accept": "application/pdf"})
        if "application/pdf" not in resposta.headers.get("content-type", ""):
            raise Uniao21Error("PDF ainda não disponível para este documento")
        return resposta.content

    async def solicitacoes(self, status: str = "", placa: str = "", page: int = 1) -> Dict[str, Any]:
        pagina = await self._html("/documentos/historico", status=status, q=placa, page=page if page > 1 else None)
        itens = []
        for card in re.findall(r'<article class="solic-card[^"]*" data-doc-id="\d+">.*?</article>', pagina, re.S):
            doc_id = int(re.search(r'data-doc-id="(\d+)"', card).group(1))
            corpo = re.search(r'solic-card-body">(.*?)</div>\s*<footer', card, re.S)
            valor = re.search(r'solic-card-valor">(.*?)<', card)
            data = re.search(r'<time datetime="([^"]+)">(.*?)</time>', card)
            placa_card = re.search(r'solic-card-placa">(.*?)<', card)
            # Errors add a line under the meta row, e.g. "Estornado Solicitado emissão, porém ..."
            extra = _texto(re.sub(r'<div class="solic-card-(?:placa|meta)">.*?</div>', "", corpo.group(1), flags=re.S)) if corpo else ""
            itens.append({
                "id": doc_id, "titulo": _texto(re.search(r'solic-card-eyebrow">(.*?)<', card).group(1)),
                "status": _texto(re.search(r'solic-badge[^"]*">(.*?)<', card, re.S).group(1)),
                "placa": _texto(placa_card.group(1)) if placa_card else None,
                "data": data.group(1) if data else None, "data_fmt": data.group(2) if data else None,
                "valor_fmt": _texto(valor.group(1)) if valor else None,
                "pdf_disponivel": f"/documentos/{doc_id}/download" in card,
                "observacao": extra or None,
            })
        return {"page": page, "pages": _paginas(pagina, page), "itens": itens}

    # --- consultas ---

    async def consultar(self, servico: str, parametro: str) -> Dict[str, Any]:
        info = await self._servico(servico, "consulta")
        pagina = await self._html("/consultas/")
        chave = re.search(r'<meta name="consulta-cipher-key" content="([^"]+)"', pagina)
        csrf = _sessao["csrf"] or ""
        payload = {"csrf_token": csrf, "parametro": parametro.strip().upper(), "multiplas": "0", **info.get("parametros_fixos", {})}
        if chave:
            # Same as consulta-cipher.js: AES-GCM with a random 12-byte IV, sent as base64(iv + ciphertext)
            iv = os.urandom(12)
            cifrado = AESGCM(base64.b64decode(chave.group(1))).encrypt(iv, json.dumps(payload).encode(), None)
            dados = {"p": base64.b64encode(iv + cifrado).decode(), "csrf_token": csrf}
        else:
            dados = payload
        corpo = await self._json("POST", info["action"], data=dados, headers={**_XHR, "X-CSRFToken": csrf})
        if not corpo.get("ok"):
            raise Uniao21Error(corpo.get("erro") or corpo.get("mensagem") or "Consulta não concluída")
        return {"servico": servico, "titulo": info["titulo"], "preco": info["preco"], "resultado": corpo}

    async def consulta(self, consulta_id: int) -> Dict[str, Any]:
        pagina = await self._html(f"/consultas/{consulta_id}")
        secoes = _linhas_detalhe(pagina)
        resumo = secoes.pop("Resumo", {})
        titulo = re.search(r'solic-card-eyebrow">(.*?)<', pagina)
        data = re.search(r"Consultado em ([\d/]+ às [\d:]+)", pagina)
        return {"id": consulta_id, "titulo": _texto(titulo.group(1)) if titulo else None,
                "consultado_em": data.group(1) if data else None, "resumo": resumo, **{_chave(k): v for k, v in secoes.items()}}

    # --- históricos ---

    async def historico(self, tipo: str = "", periodo: str = "30d", page: int = 1) -> Dict[str, Any]:
        if tipo not in TIPOS_HISTORICO or periodo not in PERIODOS_HISTORICO:
            raise Uniao21Error(f"tipo deve ser um de {TIPOS_HISTORICO} e periodo um de {PERIODOS_HISTORICO}")
        dados = await self._json("GET", "/historico/dados", params={"tipo": tipo, "periodo": periodo, "page": page})
        return {k: dados.get(k) for k in ("page", "pages", "total", "saldo_atual", "saldo_atual_fmt", "stats", "itens")}

    async def historico_financeiro(self, status: str = "", page: int = 1) -> Dict[str, Any]:
        pagina = await self._html("/financeiro/historico", status=status, page=page if page > 1 else None)
        itens = []
        for celula in re.findall(r'<div class="ios-cell ios-cell--financeiro">(.*?)</div>\s*</div>\s*(?=<div class="ios-cell|</div>)', pagina, re.S):
            titulo = re.search(r'ios-cell-title">(.*?)<span', celula, re.S)
            subtitulos = [_texto(s) for s in re.findall(r'ios-cell-subtitle">(.*?)</span>', celula, re.S)]
            badge = re.search(r'admin-user-badge[^"]*">(.*?)</span>', celula, re.S)
            creditos = re.search(r'admin-plano-creditos">(.*?)</span>', celula, re.S)
            datas = subtitulos[0].split(" · ") if subtitulos else []
            tx = next((s[3:].strip() for s in subtitulos if s.startswith("TX:")), None)
            itens.append({
                "descricao": _texto(titulo.group(1)) if titulo else None,
                "status": _texto(badge.group(1)) if badge else None,
                "valor": _reais(_texto(titulo.group(1))) if titulo else None,
                "creditos_fmt": _texto(creditos.group(1)) if creditos else None,
                "criado_em": datas[0] if datas else None,
                "pago_em": datas[1].replace("Pago ", "") if len(datas) > 1 else None,
                "tx": tx,
            })
        return {"page": page, "pages": _paginas(pagina, page), "itens": itens}

    # --- recarga ---

    async def recarregar(self, cpf_cnpj: str, valor: float) -> Dict[str, Any]:
        pagina = await self._html("/financeiro/")
        minimo = re.search(r'data-min="([\d.]+)"', pagina)
        if minimo and valor < float(minimo.group(1)):
            raise Uniao21Error(f"Valor mínimo da recarga: R$ {float(minimo.group(1)):.2f}".replace(".", ","))
        resposta = await self._request("POST", "/financeiro/recarregar", data={
            "csrf_token": _sessao["csrf"] or "", "cpf_cnpj": re.sub(r"\D", "", cpf_cnpj), "valor": f"{valor:.2f}"})
        # A created charge redirects to /financeiro/pagamento/<id>, which shows the QR code and the "copia e cola"
        pagamento = re.fullmatch(r"/financeiro/pagamento/(\d+)", resposta.url.path)
        copia_cola = re.search(r'data-pix="([^"]+)"', resposta.text)
        if not pagamento or not copia_cola:
            erro = re.search(r'class="[^"]*(?:alert|flash)[^"]*"[^>]*>\s*([^<]+)', resposta.text)
            raise Uniao21Error(erro.group(1).strip() if erro else f"PIX não gerado (página {resposta.url.path})")
        pagamento_id = int(pagamento.group(1))
        print(f"[+] [Uniao21] PIX de recarga gerado: pagamento {pagamento_id}, R$ {valor:.2f}")
        return {"pagamento_id": pagamento_id, "valor": valor, "pix_copia_cola": html.unescape(copia_cola.group(1)),
                **await self.status_recarga(pagamento_id)}

    async def status_recarga(self, pagamento_id: int) -> Dict[str, Any]:
        dados = await self._json("GET", f"/financeiro/pagamento/{pagamento_id}/status")
        if not dados.get("ok"):
            raise Uniao21Error(dados.get("erro") or "Pagamento não encontrado")
        return {"pagamento_id": pagamento_id, "status": dados.get("status"), "pago": bool(dados.get("pago")),
                "creditos_fmt": dados.get("creditos_fmt"), "saldo_fmt": dados.get("saldo_fmt")}

    async def qr_code_recarga(self, pagamento_id: int) -> bytes:
        resposta = await self._request("GET", f"/financeiro/pagamento/{pagamento_id}/qr.png")
        if not resposta.headers.get("content-type", "").startswith("image/"):
            raise Uniao21Error("QR Code não encontrado para este pagamento")
        return resposta.content
