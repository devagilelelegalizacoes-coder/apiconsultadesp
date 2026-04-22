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

    async def get_cadastro_data(self, placa: str) -> Dict[str, Any]:
        """Scrapes vehicle registration data using Placa with retries."""
        max_retries = 2
        for attempt in range(max_retries + 1):
            print(f"[*] [DETRAN-Cadastro] Starting query for Placa: {placa} (Attempt {attempt+1})")
            page = await self.init_browser()
            try:
                url_cadastro = "https://www2.detran.rj.gov.br/portal/veiculos/consultaCadastro"
                await page.goto(url_cadastro)
                await page.fill("#placa", placa)
                
                sitekey_element = await page.wait_for_selector("#divCaptcha", state="attached")
                sitekey = await sitekey_element.get_attribute("data-sitekey")
                captcha_token = await solver.solve_recaptcha_v2(sitekey, url_cadastro, task_type="RecaptchaV2EnterpriseTaskProxyless")
                
                if captcha_token:
                    await page.evaluate(f"() => {{ const el = document.getElementById('g-recaptcha-response'); if (el) el.value = '{captcha_token}'; }}")
                    await page.click("#btPesquisar")
                    await self.human_delay(2000, 4000)
                    
                    retorno_locator = page.locator("#retorno, .alert-danger")
                    retorno_text = await retorno_locator.first.inner_text() if await retorno_locator.count() > 0 else ""

                    if "CAPTCHA INVÁLIDO" in retorno_text.upper():
                        await self.close()
                        continue

                    if "VEÍCULO NÃO ENCONTRADO" in retorno_text.upper():
                        await self.close()
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
                    
                    await self.close()
                    return {"status": "success", "data": data}
                
                await self.close()
            except Exception as e:
                print(f"[!] [DETRAN-Cadastro] Error: {e}")
                await self.close()
        
        return {"status": "error", "message": "Falha na consulta de cadastro."}

    async def get_multas_detalhadas(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Scrapes fine data with detailed parsing for Transitado/Renainf."""
        print(f"[*] [DETRAN-MultasDetalhe] Starting query for Renavam: {renavam}")
        page = await self.init_browser()
        try:
            url_multas = "https://www2.detran.rj.gov.br/portal/multas/nadaConsta"
            await page.goto(url_multas)
            await page.fill("#MultasRenavam", renavam)
            await page.fill("#MultasCpfcnpj", cpf)
            
            sitekey_element = await page.wait_for_selector("#divCaptcha", state="attached")
            sitekey = await sitekey_element.get_attribute("data-sitekey")
            captcha_token = await solver.solve_recaptcha_v2(sitekey, url_multas, task_type="RecaptchaV2EnterpriseTaskProxyless")
            
            if captcha_token:
                await page.evaluate(f"() => {{ const el = document.getElementById('g-recaptcha-response'); if (el) el.value = '{captcha_token}'; }}")
                await page.click("#btPesquisar")
                await self.human_delay(2000, 4000)
                
                try:
                    await page.wait_for_selector(".tabelaDescricao, #retorno, .alert, #multas_nada_consta_mensagem_erro", state="visible", timeout=15000)
                    tables = await page.locator(".tabelaDescricao").all()
                    fines = []
                    
                    if not tables:
                        err_text = await page.locator("#retorno, .alert, #multas_nada_consta_mensagem_erro").first.inner_text()
                        if "NADA CONSTA" in err_text.upper() or "NÃO EXISTE" in err_text.upper():
                            await self.close()
                            return {"status": "success", "data": [], "message": "Nada consta"}
                        await self.close()
                        return {"status": "error", "message": err_text.strip()}
                    
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
                                ck = k.lower().replace(" ", "_").replace("$", "").replace("valor_original_r", "valor_original").replace("valor_a_ser_pago_r", "valor_pago").strip()
                                while "__" in ck: ck = ck.replace("__", "_")
                                clean[ck.strip("_")] = v
                            fines.append(clean)
                    
                    await self.close()
                    return {"status": "success", "data": fines}
                except Exception as e:
                    await self.close()
                    return {"status": "error", "message": str(e)}
            
            await self.close()
            return {"status": "error", "message": "Captcha failed"}
        except Exception as e:
            await self.close()
            return {"status": "error", "message": str(e)}

    async def get_multas_data(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Wrapper for multas detailed with retries."""
        return await self.get_multas_detalhadas(renavam, cpf)

    async def get_nada_consta_apreendido_data(self, placa: str, chassi: str, renavam: str, doc_type: str, doc_num: str) -> Dict[str, Any]:
        """Scrapes clearance data for impounded vehicles (Nada Consta Apreendido)."""
        print(f"[*] [DETRAN-NadaConsta] Starting query for Placa: {placa}")
        page = await self.init_browser()
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
            captcha_token = await solver.solve_recaptcha_v2(sitekey, url_nc, task_type="RecaptchaV2EnterpriseTaskProxyless")
            
            if captcha_token:
                await page.evaluate(f"() => {{ const el = document.getElementById('g-recaptcha-response'); if (el) el.value = '{captcha_token}'; }}")
                await page.click("#btPesquisar")
                await page.wait_for_selector("#retorno", state="visible", timeout=30000)
                
                ret_text = await page.locator("#retorno").inner_text()
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
                
                await self.close()
                return {"status": "success", "data": results}
            
            await self.close()
            return {"status": "error", "message": "Captcha failed"}
        except Exception as e:
            await self.close()
            return {"status": "error", "message": str(e)}
