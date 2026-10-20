import re
import unicodedata
from typing import Dict, Any, List

from app.scrapers.detran_rj import DetranRJScraper
from app.infrastructure.captcha_solver import solver

BASE_URL = "http://multas.detran.rj.gov.br/gaideweb2/consultaPontuacao"
# Each magnifier on the summary posts #formDetalheDaConsulta to a sub-path; "5anos" lists every auto
DETALHE_URL = BASE_URL + "/busca/5anos"

# The portal's nginx returns 502 often; a page with this text must be retried
_GATEWAY_ERRORS = ("BAD GATEWAY", "GATEWAY TIME-OUT", "SERVICE UNAVAILABLE")

_TABLES_JS = """() => [...document.querySelectorAll('table')].map(t => ({
    headers: [...t.querySelectorAll('thead th')].map(th => th.innerText.trim()),
    rows: [...t.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
}))"""

# Every auto is a bootstrap panel of "<label>Campo:</label><span>valor</span>..." pairs
_AUTOS_JS = """() => [...document.querySelectorAll('#accordion > .panel')].map(p => Object.fromEntries(
    [...p.querySelectorAll('label')].map(l => [
        l.innerText.trim().replace(/:$/, ''),
        [...l.parentElement.querySelectorAll('span')].map(s => s.innerText.replace(/\\s+/g, ' ').trim())
    ])
))"""


def _slug(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def _numero(valor: str):
    # "*" marks mandatory infractions, whose points are not summed
    return int(valor) if valor and valor.isdigit() else valor


def _tabela(tabela: Dict[str, Any]) -> List[Dict[str, Any]]:
    """First column is the row label, the others are counts named after their headers."""
    chaves = ["descricao"] + [_slug(h) for h in tabela["headers"][1:]]
    return [{k: (_numero(v) if i else v) for i, (k, v) in enumerate(zip(chaves, row))}
            for row in tabela["rows"] if row]


def _auto(campos: Dict[str, List[str]]) -> Dict[str, Any]:
    def valor(rotulo: str, i: int = 0):
        spans = campos.get(rotulo) or []
        texto = spans[i] if len(spans) > i else ""
        return None if texto in ("", "-") else texto

    # "183 - MÉDIA" next to " Pontos: 4"
    enquadramento, _, gravidade = (valor("Enquadramento") or "").partition(" - ")
    pontos = re.search(r"\d+", valor("Enquadramento", 1) or "")
    codigo, _, descricao = (valor("Infração") or "").partition(" - ")
    return {
        "auto": valor("Nº Auto"),
        "data": valor("Data"),
        "hora": valor("Data", 1),
        "orgao": valor("Orgão"),
        "placa": valor("Placa"),
        "proprietario": valor("Proprietário"),
        "responsavel_pontos": valor("Resp. Pontos"),
        "situacao": valor("Situação"),
        "codigo_infracao": codigo or None,
        "infracao": descricao or None,
        "local": valor("Local"),
        "enquadramento": enquadramento or None,
        "gravidade": gravidade or None,
        "pontos": int(pontos.group()) if pontos else None,
        "vencimento": valor("Vencimento"),
        "valor": valor("Valor"),
        "valor_com_desconto": valor("Valor com desconto"),
        "processo": valor("Processo"),
    }


class DetranPontuacaoScraper(DetranRJScraper):
    """Consulta de pontuação da CNH no portal GAIDEWEB do DETRAN-RJ."""

    async def get_pontuacao_cnh(self, cpf: str, cnh: str, uf: str = "RJ", pgu: bool = False) -> Dict[str, Any]:
        cpf = "".join(filter(str.isdigit, str(cpf or "")))
        cnh = "".join(filter(str.isdigit, str(cnh or "")))
        uf = (uf or "RJ").strip().upper()
        base = {"source": "DETRAN-RJ Pontuação", "cpf": cpf, "cnh": cnh, "uf": uf}

        max_retries = 3
        last_error = ""
        for attempt in range(1, max_retries + 1):
            print(f"[*] [DETRAN-Pontuacao] Consultando CNH {cnh} (Tentativa {attempt})")
            page = await self.init_browser(use_stealth=False)
            try:
                await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                # Checked before solving the captcha, so a 502 here costs nothing
                if not await page.locator("#cpf").count():
                    raise Exception("Formulário de consulta não carregou")

                await page.fill("#cpf", cpf)
                await page.fill("#cnh", cnh)
                await page.select_option("#uf", uf)
                if pgu:
                    await page.check("#pgu")

                sitekey = await page.get_attribute(".g-recaptcha", "data-sitekey")
                print(f"[*] [DETRAN-Pontuacao] Resolvendo Captcha (Provedor: {solver.provider}, Sitekey: {sitekey})")
                token = await solver.solve_recaptcha_v2(sitekey, BASE_URL)
                if not token:
                    last_error = "Falha ao resolver o captcha"
                    continue
                await self._inject_recaptcha_token(page, token)

                # The portal can take over 30s to answer the POST, so the click must not wait for it
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=90000):
                    await page.click("input[type=submit]", no_wait_after=True)

                body = await page.evaluate("document.body.innerText")
                if any(e in body.upper() for e in _GATEWAY_ERRORS):
                    last_error = "Portal do DETRAN indisponível (502)"
                    print(f"[!] [DETRAN-Pontuacao] {last_error}. Tentando novamente...")
                    continue

                # Errors come back on the form page with the reason in #errorMsg (e.g. "CNH NÃO CADASTRADA")
                erro = page.locator("#errorMsg")
                if await erro.count() and (msg := (await erro.inner_text()).strip()):
                    if "CAPTCHA" in msg.upper() or "ERRO INTERNO" in msg.upper():
                        last_error = msg
                        print(f"[!] [DETRAN-Pontuacao] {msg}. Tentando novamente...")
                        continue
                    return {**base, "status": "error", "message": msg}

                if "CONSULTA PONTUA" not in body.upper():
                    last_error = "Resultado da consulta não reconhecido"
                    continue

                data = await self._parse_resumo(page, body)
                print(f"[+] [DETRAN-Pontuacao] Pontos nos últimos 12 meses: {data.get('pontos_12_meses')}")

                # The list of autos is optional: when only it fails the summary is still returned
                try:
                    data["autos"] = await self._buscar_autos(page)
                    print(f"[+] [DETRAN-Pontuacao] {len(data['autos'])} autos nos últimos 5 anos")
                except Exception as e:
                    data["autos"] = None
                    data["erro_autos"] = str(e)
                    print(f"[!] [DETRAN-Pontuacao] Falha ao listar os autos: {e}")
                return {**base, "status": "success", "data": data}

            except Exception as e:
                last_error = str(e)
                print(f"[!] [DETRAN-Pontuacao] Erro na tentativa {attempt}: {e}")
            finally:
                await self.close()

        return {**base, "status": "error", "message": f"Falha após {max_retries} tentativas. Último erro: {last_error}"}

    async def _buscar_autos(self, page) -> List[Dict[str, Any]]:
        # The portal opens the details in a new tab; posting with the page's session skips the captcha
        form = await page.evaluate("() => Object.fromEntries(new FormData(document.querySelector('#formDetalheDaConsulta')))")
        ultimo_erro = ""
        for _ in range(3):
            resposta = await page.request.post(DETALHE_URL, form=form, timeout=90000)
            html = (await resposta.body()).decode("iso-8859-1")
            if resposta.ok and "CONSULTA TODAS AS INFRA" in html.upper():
                await page.set_content(html)
                return [_auto(campos) for campos in await page.evaluate(_AUTOS_JS)]
            ultimo_erro = f"HTTP {resposta.status}" if not resposta.ok else "página de autos não reconhecida"
        raise Exception(ultimo_erro)

    async def _parse_resumo(self, page, body: str) -> Dict[str, Any]:
        tabelas = await page.evaluate(_TABLES_JS)
        pontuacao = _tabela(tabelas[0]) if len(tabelas) > 0 else []
        todas = _tabela(tabelas[1]) if len(tabelas) > 1 else []

        # Row "Infrações Pontuáveis (últimos 12 meses)": the points that count toward suspension
        pontos_12_meses = next((linha.get("pontos") for linha in pontuacao
                                if "pontuaveis" in _slug(linha["descricao"]) and "12_meses" in _slug(linha["descricao"])), None)

        def processo_ativo(artigo: str):
            # "Condutor NÃO possui, ... referente ao art.261 do CTB, processo "ativo" ..."
            frase = next((l for l in body.splitlines() if artigo in l), "")
            if not frase:
                return None
            return not re.search(r"N[ÃA]O\s+possui", frase, re.IGNORECASE)

        return {
            "pontos_12_meses": pontos_12_meses,
            "suspensao_ativa": processo_ativo("art.261"),
            "cassacao_ativa": processo_ativo("art.263"),
            "pontuacao": pontuacao,
            "todas_infracoes": todas,
        }
