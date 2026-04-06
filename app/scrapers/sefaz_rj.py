from app.scrapers.base_scraper import BaseScraper
from app.infrastructure.captcha_solver import solver
import base64
from typing import Dict, Any

class SefazRJScraper(BaseScraper):
    async def get_vehicle_data(self, renavam: str) -> Dict[str, Any]:
        """Scrapes vehicle data from the NEW SEFAZ-RJ portal with retry logic."""
        MAX_RETRIES = 3
        last_error = "Unknown error"
        
        for attempt in range(1, MAX_RETRIES + 1):
            page = await self.init_browser()
            try:
                print(f"[*] [SEFAZ] Attempt {attempt}/{MAX_RETRIES} for Renavam {renavam}")
                await page.goto("https://darj-ipva-web.fazenda.rj.gov.br/darj-ipva-web/#/", wait_until="networkidle")
                
                await self.simulate_interaction(page)
                
                # Fill Renavam
                await page.wait_for_selector("#renavam")
                await page.fill("#renavam", renavam)
                
                # Handle Image Captcha
                captcha_img = await page.wait_for_selector(".captcha-img")
                await self.human_delay(500, 1000)
                captcha_box = await captcha_img.bounding_box()
                if not captcha_box:
                    raise Exception("Could not find captcha box")
                    
                captcha_bytes = await page.screenshot(clip=captcha_box)
                captcha_b64 = base64.b64encode(captcha_bytes).decode('utf-8')
                
                print(f"[*] [SEFAZ] Solving Image Captcha (Attempt {attempt})...")
                captcha_text = await solver.solve_image_captcha(captcha_b64)
                
                if not captcha_text:
                    print(f"[-] [SEFAZ] Captcha solving failed on attempt {attempt}.")
                    last_error = "Captcha solving service failed"
                    continue
                    
                await page.fill("#captcha", captcha_text)
                await self.human_delay(1000, 2000)
                await page.click(".btn-consultar")
                
                # Wait for results or error
                await self.human_delay(3000, 5000)
                
                # Check for error toast or messages
                if await page.query_selector(".p-toast-message-content"):
                    error_msg = await page.inner_text(".p-toast-message-content")
                    print(f"[-] [SEFAZ] Portal returned error: {error_msg}")
                    
                    if "captcha inválido" in error_msg.lower() and attempt < MAX_RETRIES:
                        print(f"[*] [SEFAZ] Captcha was incorrect. Retrying...")
                        await self.close() # Close current browser for clean state
                        continue
                    
                    if "NÃO ENCONTRADO" in error_msg.upper() or "NÃO EXISTEM" in error_msg.upper():
                        return {"source": "SEFAZ-RJ", "status": "success", "data": {"detalhes": {}, "debitos_ipva": []}, "message": "Nada consta ou veículo não encontrado"}
                    
                    return {"source": "SEFAZ-RJ", "status": "error", "message": error_msg}

                # Result extraction
                print("[*] [SEFAZ] Waiting for results table...")
                try:
                    await page.wait_for_selector("app-emissao-darj-resultado", timeout=15000)
                except:
                    # If not found, check if still on form with an error not caught by toast
                    if await page.query_selector("#renavam"):
                        last_error = "Permaneceu na página de formulário (possível erro não detectado)"
                        continue
                    raise

                # Extract Vehicle & Tax Details
                print("[*] [SEFAZ] Extracting details using text-subtraction...")
                details = {}
                label_map = {
                    "renavam": "RENAVAM",
                    "placa": "Placa", 
                    "marca": "Marca", 
                    "modelo": "Modelo", 
                    "ano_fabricacao": "Ano de fabricação",
                    "contribuinte": "Contribuinte",
                    "cpf_cnpj": "CPF/CNPJ",
                    "municipio": "Município de emplacamento",
                    "base_calculo": "Base de Cálculo",
                    "aliquota": "Alíquota",
                    "duodecimos": "N° de duodecimos"
                }

                for key, label in label_map.items():
                    try:
                        # Find the label element
                        # Strategy: Find element with exact text, then check parent inner_text
                        label_loc = page.get_by_text(label, exact=True).first
                        if await label_loc.count() > 0:
                            # Get parent container
                            parent = label_loc.locator("xpath=..")
                            parent_text = (await parent.inner_text()).strip()
                            # Value is the text minus the label
                            # Common formats: "Label\nValue" or "Label: Value"
                            value = parent_text.replace(label, "").replace(":", "").strip()
                            if value:
                                details[key] = value
                            else:
                                # Fallback: try next container sibling
                                sibling = label_loc.locator("xpath=following-sibling::*").first
                                if await sibling.count() > 0:
                                    details[key] = (await sibling.inner_text()).strip()
                        else:
                            details[key] = None
                    except: details[key] = None
                
                print(f"[+] [SEFAZ] Details extracted: {details}")

                # Extract Detailed Debts Table
                debts = []
                rows = await page.locator(".p-datatable-tbody tr").all()
                for row in rows:
                    cols = await row.locator("td").all()
                    if len(cols) >= 7:
                        debts.append({
                            "cota": (await cols[0].inner_text()).strip(),
                            "vencimento": (await cols[1].inner_text()).strip(),
                            "valor_principal": (await cols[2].inner_text()).strip(),
                            "juros": (await cols[3].inner_text()).strip(),
                            "multa": (await cols[4].inner_text()).strip(),
                            "desconto": (await cols[5].inner_text()).strip(),
                            "total_a_pagar": (await cols[6].inner_text()).strip()
                        })

                return {
                    "source": "SEFAZ-RJ",
                    "renavam": renavam,
                    "status": "success",
                    "data": {
                        "detalhes": details, 
                        "debitos_ipva": debts
                    }
                }

            except Exception as e:
                print(f"[-] [SEFAZ] Error on attempt {attempt}: {str(e)}")
                last_error = str(e)
            finally:
                await self.close()

        return {"source": "SEFAZ-RJ", "renavam": renavam, "status": "error", "message": f"Failed after {MAX_RETRIES} attempts. Last error: {last_error}"}
