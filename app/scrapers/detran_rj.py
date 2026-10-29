from app.scrapers.base_scraper import BaseScraper
from app.infrastructure.captcha_solver import solver
from typing import Dict, Any
import asyncio
import re
import unicodedata


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()


# Since 10/2026 the services live on apps-portal (embedded as iframes on www.detran.rj.gov.br); www2 is gone
BASE_URL = "https://apps-portal.detran.rj.gov.br"

# Cadastro and Nada Consta post via AJAX and show the fragment in #retorno, or the failure in #erroConsulta
_AJAX_DONE = "#secaoResultadoConsulta:not(.d-none), #erroConsulta:not(.d-none)"

_GATEWAY_ERRORS = ("BAD GATEWAY", "GATEWAY TIME-OUT", "SERVICE UNAVAILABLE")

_CADASTRO_JS = """() => {
    const r = document.querySelector('#retorno');
    const txt = e => (e ? e.innerText : '').replace(/\\s+/g, ' ').trim();
    const campos = {};
    r.querySelectorAll('.cadastro-resultado__resumo > div').forEach(d => campos[txt(d.querySelector('span'))] = txt(d.querySelector('strong')));
    r.querySelectorAll('dt').forEach(dt => campos[txt(dt)] = txt(dt.nextElementSibling));
    const obs = r.querySelector('.cadastro-resultado__observacoes');
    const titulo = obs && obs.querySelector('h3');
    return {
        campos,
        placa: txt(r.querySelector('.cadastro-resultado__placa strong')),
        gravame: txt(r.querySelector('.cadastro-resultado__gravame')),
        observacoes: obs ? obs.innerText.replace(titulo ? titulo.innerText : '', '').trim() : '',
        alertas: [...r.querySelectorAll('.alert')].map(txt),
    };
}"""

# Every fine is a card: header with its status, body with "<strong>Campo:</strong> valor" pairs
_MULTAS_JS = """() => [...document.querySelectorAll('main article.card')].map(card => {
    const fine = {tipo_status: card.querySelector('.card-header').innerText.replace(/\\s+/g, ' ').trim()};
    card.querySelectorAll('.card-body strong').forEach(s => {
        const label = s.innerText.trim();
        fine[label] = s.parentElement.innerText.replace(label, '').replace(/\\s+/g, ' ').trim();
    });
    return fine;
})"""


class DetranRJScraper(BaseScraper):
    async def get_vehicle_data(self, renavam: str, cpf: str, placa: str | None = None) -> Dict[str, Any]:
        """Orchestrates both Cadastro and Multas queries."""
        results = {}
        try:
            if placa:
                results["cadastro"] = await self.get_cadastro_data(placa)
            results["multas"] = await self.get_multas_data(renavam, cpf)
            
            return {
                "source": "DETRAN-RJ",
                "renavam": renavam,
                "placa": placa,
                "status": "success",
                "data": results
            }
        except Exception as e:
            return {
                "source": "DETRAN-RJ",
                "renavam": renavam,
                "placa": placa,
                "status": "error",
                "message": str(e)
            }

    async def _inject_recaptcha_token(self, page, token: str) -> Dict[str, Any]:
        """Robustly injects reCAPTCHA token into the page and triggers callbacks."""
        try:
            await page.wait_for_selector('[name="g-recaptcha-response"]', state="attached", timeout=5000)
        except Exception as e:
            print(f"[!] [DetranScraper] Warning: g-recaptcha-response selector not attached after 5s: {e}")

        inject_result = await page.evaluate(f"""
            () => {{
                let el = document.getElementById('g-recaptcha-response');
                if (!el) {{
                    el = document.getElementsByName('g-recaptcha-response')[0];
                }}
                if (!el) {{
                    el = document.querySelector('textarea[class*="g-recaptcha-response"]');
                }}
                
                if (el) {{
                    el.value = '{token}';
                    el.style.display = 'block';
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                }}
                
                document.querySelectorAll('textarea[name="g-recaptcha-response"]').forEach(t => t.value = '{token}');

                let callbackFired = false;
                document.querySelectorAll('.g-recaptcha, [data-callback]').forEach(w => {{
                    const cb = w.getAttribute('data-callback');
                    if (cb && window[cb]) {{
                        window[cb]('{token}');
                        callbackFired = true;
                    }}
                }});
                
                try {{
                    const cfg = window.___grecaptcha_cfg;
                    if (cfg && cfg.clients) {{
                        Object.values(cfg.clients).forEach(client => {{
                            if (client && client.aa && client.aa.l && client.aa.l.callback) {{
                                client.aa.l.callback('{token}');
                                callbackFired = true;
                            }}
                        }});
                    }}
                }} catch(e) {{}}
                
                return {{ el_found: !!el, callback_fired: callbackFired }};
            }}
        """)
        return inject_result

    async def _resolver_captcha_se_houver(self, page, url: str, tag: str) -> bool:
        """apps-portal launched without a captcha; if a reCAPTCHA comes back, solve it. False when solving failed."""
        widget = page.locator("[data-sitekey]")
        if not await widget.count():
            return True
        sitekey = await widget.first.get_attribute("data-sitekey")
        print(f"[*] [{tag}] Resolvendo Captcha (Provedor: {solver.provider}, Sitekey: {sitekey})")
        token = await solver.solve_recaptcha_v2(sitekey, url, task_type="RecaptchaV2EnterpriseTaskProxyless")
        if not token:
            return False
        await self._inject_recaptcha_token(page, token)
        return True

    async def _consulta_ajax(self, page, tag: str) -> str | None:
        """Clicks #btPesquisar and waits for the AJAX answer. Returns the #erroConsulta text when it failed."""
        await page.click("#btPesquisar")
        # The portal's gateway gives up after 30s with a 504
        await page.wait_for_selector(_AJAX_DONE, timeout=60000)
        erro = page.locator("#erroConsulta:not(.d-none)")
        if await erro.count():
            msg = (await erro.inner_text()).strip()
            print(f"[!] [{tag}] {msg}")
            return msg
        return None

    async def get_cadastro_data(self, placa: str) -> Dict[str, Any]:
        """Scrapes vehicle registration data using Placa with retries."""
        max_retries = 2
        last_error = ""
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-Cadastro] Starting query for Placa: {placa} (Attempt {attempt+1})")
            page = await self.init_browser(use_stealth=False)
            try:
                url_cadastro = BASE_URL + "/veiculos/consultaCadastro"
                await page.goto(url_cadastro, wait_until="domcontentloaded", timeout=60000)
                await page.fill("#placa", placa)
                if not await self._resolver_captcha_se_houver(page, url_cadastro, "DETRAN-Cadastro"):
                    last_error = "Falha ao resolver o captcha"
                    continue

                if erro := await self._consulta_ajax(page, "DETRAN-Cadastro"):
                    last_error = erro
                    continue

                retorno_text = (await page.inner_text("#retorno")).replace('﻿', '').strip()
                print(f"[*] [DETRAN-Cadastro] Texto retorno: '{retorno_text[:100]}'")

                if not await page.locator("#retorno .cadastro-resultado").count():
                    # e.g. "Veículo não cadastrado na base estadual (RJ)!"
                    if "NAO CADASTRADO" in _sem_acento(retorno_text) or "NAO ENCONTRADO" in _sem_acento(retorno_text):
                        return {"status": "success", "data": {}, "message": "Veículo não encontrado"}
                    last_error = retorno_text or "Resultado da consulta não reconhecido"
                    continue

                raw = await page.evaluate(_CADASTRO_JS)
                campos = {_sem_acento(k): v for k, v in raw["campos"].items()}
                observacoes = "" if raw["observacoes"] in ("", "—", "-") else raw["observacoes"]
                # Same keys as the old www2 card (crlv-* ids), which the business rules and n8n flows read
                data = {
                    "licenciamento": campos.get("LICENCIAMENTO", ""),
                    "nome": campos.get("PROPRIETARIO", ""),
                    "placa": raw["placa"],
                    "placa_anterior": campos.get("PLACA ANTERIOR", ""),
                    "especie": campos.get("ESPECIE", ""),
                    "combustivel": campos.get("COMBUSTIVEL", ""),
                    "marca": campos.get("MARCA / MODELO", ""),
                    "ano-fabricacao": campos.get("ANO DE FABRICACAO", ""),
                    "ano-modelo": campos.get("ANO DO MODELO", ""),
                    "categoria": campos.get("CATEGORIA", ""),
                    "cor": campos.get("COR", ""),
                    "observacoes": observacoes,
                    "local": campos.get("MUNICIPIO", ""),
                    "mensagem_detran": " | ".join(a for a in raw["alertas"] if a),
                }
                # "CAPACIDADE / POTÊNCIA / CILINDRADAS", e.g. "5 / 122 / 1587"
                lpc = re.search(r"(\d{1,3})\s*/\s*(\d{1,4})\s*/\s*(\d{1,5})", campos.get("CAPACIDADE / POTENCIA / CILINDRADAS", ""))
                if lpc:
                    data["lotacao"], data["potencia"], data["cilindrada"] = lpc.groups()
                texto = _sem_acento(f"{raw['gravame']} {observacoes} {data['mensagem_detran']}")
                data["has_gravame"] = "SIM" if raw["gravame"] or "ALIENACAO FIDUCIA" in texto or "GRAVAME" in texto or "RESERVA DE DOMINIO" in texto else "NÃO"
                data["comunicacao_venda"] = "SIM" if "COMUNICACAO DE VENDA" in texto or "INTENCAO DE VENDA" in texto else "NÃO"

                return {"status": "success", "data": data}

            except Exception as e:
                last_error = str(e)
                print(f"[!] [DETRAN-Cadastro] Error: {e}")
            finally:
                await self.close()

        return {"status": "error", "message": f"Falha na consulta de cadastro. Último erro: {last_error}"}

    async def get_multas_detalhadas(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Scrapes fine data with detailed parsing for Transitado/Renainf with retries."""
        max_retries = 2
        last_error = ""
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-MultasDetalhe] Starting query for Renavam: {renavam} (Attempt {attempt+1})")
            page = await self.init_browser(use_stealth=False)
            try:
                url_multas = BASE_URL + "/multas/nadaConsta"
                await page.goto(url_multas, wait_until="domcontentloaded", timeout=60000)
                await page.fill("#multas-renavam", renavam)
                await page.fill("#multas-cpfcnpj", cpf)
                if not await self._resolver_captcha_se_houver(page, url_multas, "DETRAN-MultasDetalhe"):
                    last_error = "Falha ao resolver o captcha"
                    continue

                # Unlike the other two, this form is a regular POST that loads a new page
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=90000):
                    await page.click("#multasFormulario button[type=submit]")

                fines = []
                while True:
                    body = await page.inner_text("body")
                    if any(e in body.upper() for e in _GATEWAY_ERRORS):
                        raise Exception("Portal do DETRAN indisponível (gateway)")
                    fines += await page.evaluate(_MULTAS_JS)
                    # "2 multas encontradas. Página 1 de 1"; the next page posts #formMultas
                    pagina = re.search(r"P[áa]gina\s+(\d+)\s+de\s+(\d+)", body)
                    if not pagina or int(pagina.group(1)) >= int(pagina.group(2)):
                        break
                    async with page.expect_navigation(wait_until="domcontentloaded", timeout=90000):
                        await page.evaluate("document.getElementById('formMultas').submit()")

                if not fines:
                    # Errors come as an alert next to the fixed "AS INFORMAÇÕES SÃO VÁLIDAS..." one
                    alertas = [a.strip() for a in await page.locator("main .alert").all_inner_texts()]
                    msg = next((a for a in alertas if "VALIDAS ATE" not in _sem_acento(a)), "")
                    print(f"[*] [DETRAN-MultasDetalhe] Texto retorno: '{msg[:100]}'")
                    if "NAO CORRESPONDE" in _sem_acento(msg) or "NAO CONSTA NO CADASTRO" in _sem_acento(msg):
                        return {"status": "error", "message": msg}
                    if not msg or "NADA CONSTA" in _sem_acento(msg) or "NAO EXISTE" in _sem_acento(msg):
                        return {"status": "success", "data": [], "message": "Nada consta"}
                    return {"status": "error", "message": msg}

                clean_fines = []
                for fine_data in fines:
                    clean = {}
                    for k, v in fine_data.items():
                        # Keys are the labels without accents, e.g. "Auto de Infração:" -> "auto_de_infrao"
                        ck = k.replace(":", "").strip().encode("ascii", errors="ignore").decode("ascii")
                        ck = ck.lower().replace(" ", "_").replace("$", "").replace("valor_original_r", "valor_original").replace("valor_a_ser_pago_r", "valor_pago").strip()
                        while "__" in ck: ck = ck.replace("__", "_")
                        clean[ck.strip("_")] = v
                    clean_fines.append(clean)

                print(f"[+] [DETRAN-MultasDetalhe] {len(clean_fines)} multas encontradas")
                return {"status": "success", "data": clean_fines}

            except Exception as e:
                last_error = str(e)
                print(f"[!] [DETRAN-MultasDetalhe] Erro na tentativa: {e}")
            finally:
                await self.close()

        return {"status": "error", "message": f"Falha na consulta de multas após várias tentativas. Último erro: {last_error}"}

    async def get_multas_data(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Wrapper for multas detailed with retries."""
        return await self.get_multas_detalhadas(renavam, cpf)

    async def get_nada_consta_apreendido_data(self, placa: str, chassi: str, renavam: str, doc_type: str, doc_num: str) -> Dict[str, Any]:
        """Scrapes clearance data for impounded vehicles (Nada Consta Apreendido) with retries."""
        max_retries = 2
        last_error = ""
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-NadaConsta] Starting query for Placa: {placa} (Attempt {attempt+1})")
            page = await self.init_browser(use_stealth=False)
            try:
                url_nc = BASE_URL + "/veiculos/consulta-nada-consta"
                await page.goto(url_nc, wait_until="domcontentloaded", timeout=60000)
                await page.fill("#placa", placa)
                await page.fill("#chassi", chassi)
                await page.fill("#renavam", renavam)
                await page.select_option("#tipo_doc", value=doc_type.lower())
                await page.fill("#num_doc", doc_num)
                if not await self._resolver_captcha_se_houver(page, url_nc, "DETRAN-NadaConsta"):
                    last_error = "Falha ao resolver o captcha"
                    continue

                if erro := await self._consulta_ajax(page, "DETRAN-NadaConsta"):
                    last_error = erro
                    continue

                ret_text = (await page.inner_text("#retorno")).replace('﻿', '').strip()
                print(f"[*] [DETRAN-NadaConsta] Texto retorno: '{ret_text[:100]}'")

                # Each item is a "<débito>: SIM/NÃO" line (an <li> on the old www2 page)
                results = {"debitos": {}}
                itens = await page.locator("#retorno li").all_inner_texts() or ret_text.splitlines()
                for text in itens:
                    if ":" in text:
                        p = text.split(":", 1)
                        if not re.search(r"\b(SIM|N[ÃA]O)\b", p[1].upper()):
                            continue
                        k = p[0].strip().upper().replace(" ", "_")
                        results["debitos"][k] = "SIM" if "SIM" in p[1].upper() else "NÃO"

                # The verdict sits outside the list; the item lines themselves never are it
                titulo = await page.locator("#retorno .alert, #retorno h2, #retorno h3, #retorno .vermelha").all_inner_texts()
                results["status_geral"] = next((t.strip() for t in titulo if t.strip()), "NADA CONSTA")
                if not results["debitos"]:
                    results["texto_retorno"] = ret_text

                return {"status": "success", "data": results}

            except Exception as e:
                last_error = str(e)
                print(f"[!] [DETRAN-NadaConsta] Erro na tentativa: {e}")
            finally:
                await self.close()

        return {"status": "error", "message": f"Falha na consulta de nada consta após várias tentativas. Último erro: {last_error}"}
