from app.scrapers.base_scraper import BaseScraper
from app.infrastructure.captcha_solver import solver
from typing import Dict, Any
import asyncio

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

    async def get_cadastro_data(self, placa: str) -> Dict[str, Any]:
        """Scrapes vehicle registration data using Placa with retries."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-Cadastro] Starting query for Placa: {placa} (Attempt {attempt+1})")
            page = await self.init_browser(use_stealth=False)
            try:
                url_cadastro = "https://www2.detran.rj.gov.br/portal/veiculos/consultaCadastro"
                await page.goto(url_cadastro)
                await page.fill("#placa", placa)
                
                sitekey_element = await page.wait_for_selector("#divCaptcha", state="attached")
                sitekey = await sitekey_element.get_attribute("data-sitekey")
                print(f"[*] [DETRAN-Cadastro] Resolvendo Captcha (Provedor: {solver.provider}, Sitekey: {sitekey})")
                captcha_token = await solver.solve_recaptcha_v2(sitekey, url_cadastro, task_type="RecaptchaV2EnterpriseTaskProxyless")
                
                if captcha_token:
                    print(f"[*] [DETRAN-Cadastro] Token obtido ({len(captcha_token)} chars). Injetando na pagina...")
                    inject_result = await self._inject_recaptcha_token(page, captcha_token)
                    print(f"[*] [DETRAN-Cadastro] Inject result: {inject_result}")
                    await self.human_delay(800, 1200)
                    
                    print(f"[*] [DETRAN-Cadastro] Clicando em #btPesquisar...")
                    try:
                        await page.click("#btPesquisar", timeout=5000)
                    except Exception as click_err:
                        print(f"[!] [DETRAN-Cadastro] Click falhou ({click_err}), tentando via JS...")
                        await page.evaluate("document.getElementById('btPesquisar').click()")
                    
                    print(f"[*] [DETRAN-Cadastro] Aguardando resposta da pagina...")
                    
                    # Wait for the page response robustly (up to 25s)
                    found_selector = None
                    try:
                        found_selector = await page.wait_for_selector(
                            "#retorno, #crlv-placa, .alert-danger, #erroCaptchaTop",
                            state="visible", timeout=25000
                        )
                        print(f"[*] [DETRAN-Cadastro] Pagina respondeu!")
                    except Exception as wfs_err:
                        current_url = page.url
                        page_title = await page.title()
                        print(f"[!] [DETRAN-Cadastro] Timeout aguardando resposta. URL={current_url}, Title={page_title}")
                        # Dump visible text to understand page state
                        body_text = await page.evaluate("document.body.innerText.substring(0, 500)")
                        print(f"[!] [DETRAN-Cadastro] Conteudo da pagina: {body_text}")
                        await self.human_delay(3000, 4000)
                    
                    retorno_locator = page.locator("#retorno, .alert-danger")
                    retorno_text = await retorno_locator.first.inner_text() if await retorno_locator.count() > 0 else ""
                    retorno_text = retorno_text.replace('\ufeff', '').strip()
                    print(f"[*] [DETRAN-Cadastro] Texto retorno: '{retorno_text[:100]}'")

                    if "CAPTCHA INVÁLIDO" in retorno_text.upper():
                        continue

                    if "VEÍCULO NÃO ENCONTRADO" in retorno_text.upper():
                        return {"status": "success", "data": {}, "message": "Veículo não encontrado"}

                    fields = ["crlv-licenciamento", "crlv-nome", "crlv-placa", "crlv-especie", 
                              "crlv-combustivel", "crlv-marca", "crlv-ano-fabricacao", 
                              "crlv-ano-modelo", "crlv-categoria", "crlv-cor", "crlv-observacoes", "crlv-local"]
                    
                    data = {}
                    for field in fields:
                        try:
                            val = await page.locator(f"#{field}").text_content()
                            data[field.replace("crlv-", "")] = val.strip() if val else ""
                        except: data[field.replace("crlv-", "")] = ""
                    
                    data["has_gravame"] = "SIM" if "ALIENAÇÃO FIDUCIÁ" in retorno_text.upper() or "GRAVAME" in retorno_text.upper() else "NÃO"
                    obs = (data.get("observacoes") or "").upper()
                    data["comunicacao_venda"] = "SIM" if "COMUNICAÇÃO DE VENDA" in obs or "INTENÇÃO DE VENDA" in obs else "NÃO"
                    
                    return {"status": "success", "data": data}
                
                else:
                    print("[!] [DETRAN-Cadastro] Falha ao obter token do Captcha. Pulando tentativa.")
                    
            except Exception as e:
                print(f"[!] [DETRAN-Cadastro] Error: {e}")
            finally:
                await self.close()
        
        return {"status": "error", "message": "Falha na consulta de cadastro."}

    async def get_multas_detalhadas(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Scrapes fine data with detailed parsing for Transitado/Renainf with retries."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-MultasDetalhe] Starting query for Renavam: {renavam} (Attempt {attempt+1})")
            page = await self.init_browser(use_stealth=False)
            try:
                url_multas = "https://www2.detran.rj.gov.br/portal/multas/nadaConsta"
                await page.goto(url_multas)
                await page.fill("#MultasRenavam", renavam)
                await page.fill("#MultasCpfcnpj", cpf)
                
                sitekey_element = await page.wait_for_selector("#divCaptcha", state="attached")
                sitekey = await sitekey_element.get_attribute("data-sitekey")
                print(f"[*] [DETRAN-MultasDetalhe] Resolvendo Captcha (Provedor: {solver.provider}, Sitekey: {sitekey})")
                captcha_token = await solver.solve_recaptcha_v2(sitekey, url_multas, task_type="RecaptchaV2EnterpriseTaskProxyless")
                
                if captcha_token:
                    inject_result = await self._inject_recaptcha_token(page, captcha_token)
                    print(f"[*] [DETRAN-MultasDetalhe] Inject result: {inject_result}")
                    print(f"[*] [DETRAN-MultasDetalhe] Clicando em #btPesquisar...")
                    try:
                        await page.click("#btPesquisar", timeout=5000)
                    except Exception as click_err:
                        print(f"[!] [DETRAN-MultasDetalhe] Click falhou ({click_err}), tentando via JS...")
                        await page.evaluate("document.getElementById('btPesquisar').click()")
                    await self.human_delay(2000, 4000)
                    
                    # Wait for results page elements robustly
                    try:
                        await page.wait_for_selector(
                            ".tabelaDescricao, #retorno, .alert, #multas_nada_consta_mensagem_erro, #erroCaptchaTop", 
                            state="visible", timeout=25000
                        )
                    except Exception as wfs_err:
                        current_url = page.url
                        page_title = await page.title()
                        print(f"[!] [DETRAN-MultasDetalhe] Timeout aguardando resposta. URL={current_url}, Title={page_title}")
                        body_text = await page.evaluate("document.body.innerText.substring(0, 500)")
                        print(f"[!] [DETRAN-MultasDetalhe] Conteudo da pagina: {body_text}")
                    
                    retorno_locator = page.locator("#retorno, .alert, #multas_nada_consta_mensagem_erro, #erroCaptchaTop")
                    retorno_text = ""
                    if await retorno_locator.count() > 0:
                        retorno_text = await retorno_locator.first.inner_text()
                    retorno_text = retorno_text.replace('\ufeff', '').strip()
                    print(f"[*] [DETRAN-MultasDetalhe] Texto retorno: '{retorno_text[:100]}'")
                    
                    if "CAPTCHA INVÁLIDO" in retorno_text.upper():
                        print("[!] [DETRAN-MultasDetalhe] Captcha inválido detectado. Tentando novamente...")
                        continue
                        
                    tables = await page.locator(".tabelaDescricao").all()
                    fines = []
                    
                    if not tables:
                        body_all_text = await page.evaluate("document.body.innerText")
                        if "não corresponde ao do proprietário" in body_all_text:
                            return {"status": "error", "message": "Este CPF/CNPJ não corresponde ao do proprietário registrado no cadastro do Detran-RJ"}
                        if "Renavam incorreto" in body_all_text or "Renavam inválido" in body_all_text:
                            return {"status": "error", "message": "Renavam incorreto ou inválido"}
                        if "NADA CONSTA" in retorno_text.upper() or "NÃO EXISTE" in retorno_text.upper() or not retorno_text:
                            return {"status": "success", "data": [], "message": "Nada consta"}
                        return {"status": "error", "message": retorno_text}
                    
                    for table in tables:
                        fine_data = {}
                        header_els = await table.locator("thead th").all()
                        fine_data["tipo_status"] = (await header_els[0].inner_text()).strip() if header_els else ""
                        
                        cells = await table.locator("tbody td").all()
                        for cell in cells:
                            try:
                                sub = cell.locator("span.sub-titulo")
                                if await sub.count() > 0:
                                    k = (await sub.first.inner_text()).replace(":", "").strip()
                                    v = (await cell.inner_text()).replace(k, "").strip()
                                    if k: fine_data[k] = v
                            except: continue
                        
                        if fine_data:
                            clean = {}
                            for k, v in fine_data.items():
                                # Normaliza a chave: remove chars inválidos (encoding misto Windows/Linux)
                                ck = k.encode("ascii", errors="ignore").decode("ascii")
                                ck = ck.lower().replace(" ", "_").replace("$", "").replace("valor_original_r", "valor_original").replace("valor_a_ser_pago_r", "valor_pago").strip()
                                while "__" in ck: ck = ck.replace("__", "_")
                                # Também normaliza o valor (remove prefixo ": \n" que vem do inner_text)
                                vv = v.strip().lstrip(":\n").strip() if isinstance(v, str) else v
                                clean[ck.strip("_")] = vv
                            fines.append(clean)
                    
                    return {"status": "success", "data": fines}
                
                else:
                    print("[!] [DETRAN-MultasDetalhe] Falha ao obter token do Captcha. Tentando novamente...")
                    
            except Exception as e:
                print(f"[!] [DETRAN-MultasDetalhe] Erro na tentativa: {e}")
            finally:
                await self.close()
                
        return {"status": "error", "message": "Falha na consulta de multas após várias tentativas."}

    async def get_multas_data(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Wrapper for multas detailed with retries."""
        return await self.get_multas_detalhadas(renavam, cpf)

    async def get_nada_consta_apreendido_data(self, placa: str, chassi: str, renavam: str, doc_type: str, doc_num: str) -> Dict[str, Any]:
        """Scrapes clearance data for impounded vehicles (Nada Consta Apreendido) with retries."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-NadaConsta] Starting query for Placa: {placa} (Attempt {attempt+1})")
            page = await self.init_browser(use_stealth=False)
            try:
                url_nc = "https://www2.detran.rj.gov.br/portal/veiculos/consultaNadaConsta"
                await page.goto(url_nc)
                await page.fill("#placa", placa)
                await page.fill("#chassi", chassi)
                await page.fill("#renavam", renavam)
                await page.select_option("#tipo_doc", value=doc_type.lower())
                await page.fill("#num_doc", doc_num)

                sitekey_element = await page.wait_for_selector("#divCaptcha", state="attached")
                sitekey = await sitekey_element.get_attribute("data-sitekey")
                print(f"[*] [DETRAN-NadaConsta] Resolvendo Captcha (Provedor: {solver.provider}, Sitekey: {sitekey})")
                captcha_token = await solver.solve_recaptcha_v2(sitekey, url_nc, task_type="RecaptchaV2EnterpriseTaskProxyless")
                
                if captcha_token:
                    inject_result = await self._inject_recaptcha_token(page, captcha_token)
                    print(f"[*] [DETRAN-NadaConsta] Inject result: {inject_result}")
                    print(f"[*] [DETRAN-NadaConsta] Clicando em #btPesquisar...")
                    try:
                        await page.click("#btPesquisar", timeout=5000)
                    except Exception as click_err:
                        print(f"[!] [DETRAN-NadaConsta] Click falhou ({click_err}), tentando via JS...")
                        await page.evaluate("document.getElementById('btPesquisar').click()")
                    
                    try:
                        await page.wait_for_selector("#retorno, #erroCaptchaTop", state="visible", timeout=30000)
                    except Exception as wfs_err:
                        current_url = page.url
                        page_title = await page.title()
                        print(f"[!] [DETRAN-NadaConsta] Timeout aguardando resposta. URL={current_url}, Title={page_title}")
                    
                    ret_text = ""
                    ret_locator = page.locator("#retorno, #erroCaptchaTop")
                    if await ret_locator.count() > 0:
                        ret_text = await ret_locator.first.inner_text()
                    ret_text = ret_text.replace('\ufeff', '').strip()
                    print(f"[*] [DETRAN-NadaConsta] Texto retorno: '{ret_text[:100]}'")

                    if "CAPTCHA INVÁLIDO" in ret_text.upper():
                        print("[!] [DETRAN-NadaConsta] Captcha inválido detectado. Tentando novamente...")
                        continue

                    results = {"debitos": {}}
                    status_loc = page.locator("#erroCaptchaTop")
                    results["status_geral"] = (await status_loc.inner_text()).strip() if await status_loc.count() > 0 else "NADA CONSTA"

                    items = await page.locator("#retorno ol li").all()
                    for item in items:
                        text = await item.inner_text()
                        if ":" in text:
                            p = text.split(":", 1)
                            k = p[0].strip().upper().replace(" ", "_")
                            v = "SIM" if "SIM" in p[1].upper() else "NÃO"
                            results["debitos"][k] = v
                    
                    return {"status": "success", "data": results}
                
                else:
                    print("[!] [DETRAN-NadaConsta] Falha ao obter token do Captcha. Tentando novamente...")
            except Exception as e:
                print(f"[!] [DETRAN-NadaConsta] Erro na tentativa: {e}")
            finally:
                await self.close()
                
        return {"status": "error", "message": "Falha na consulta de nada consta após várias tentativas."}
