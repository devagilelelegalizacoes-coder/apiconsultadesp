from app.scrapers.base_scraper import BaseScraper
from app.infrastructure.captcha_solver import solver
from typing import Dict, Any

class DetranRJScraper(BaseScraper):
    async def get_vehicle_data(self, renavam: str, cpf: str, placa: str | None = None) -> Dict[str, Any]:
        """Orchestrates both Cadastro and Multas queries."""
        results = {}
        
        try:
            # 1. Consulta Cadastro (if placa provided)
            if placa:
                results["cadastro"] = await self.get_cadastro_data(placa)
            
            # 2. Consulta Multas
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
        """Scrapes vehicle registration data using Placa."""
        print(f"[*] [DETRAN-Cadastro] Starting query for Placa: {placa}")
        page = await self.init_browser()
        try:
            url_cadastro = "https://www2.detran.rj.gov.br/portal/veiculos/consultaCadastro"
            await page.goto(url_cadastro)
            await page.fill("#placa", placa)
            
            # Handle ReCaptcha V2
            print("[*] [DETRAN-Cadastro] Solving ReCaptcha...")
            sitekey_element = await page.wait_for_selector("#divCaptcha")
            sitekey = await sitekey_element.get_attribute("data-sitekey")
            captcha_token = await solver.solve_recaptcha_v2(sitekey, url_cadastro)
            
            if captcha_token:
                print("[+] [DETRAN-Cadastro] Captcha solved. Submitting...")
                await page.evaluate(f"""
                    () => {{
                        const el = document.getElementById('g-recaptcha-response');
                        if (el) {{
                            el.value = '{captcha_token}';
                            el.innerHTML = '{captcha_token}';
                        }}
                    }}
                """)
                await page.click("#btPesquisar")
                await self.human_delay(2000, 4000)
                
                # Check for Site/Database errors
                retorno_text = await page.locator("#retorno").inner_text() or ""
                if "ERRO AO ACESSAR O SERVIÇO" in retorno_text.upper() or "NÃO FOI POSSÍVEL CONSULTAR A BASE DE DADOS" in retorno_text.upper():
                    print("[-] [DETRAN-Cadastro] Site is unstable or database is offline.")
                    return {"status": "error", "message": "DETRAN-RJ em manutenção ou com erro na base de dados. Tente mais tarde."}
                
                print("[*] [DETRAN-Cadastro] Extracting fields...")
                fields = ["crlv-licenciamento", "crlv-nome", "crlv-placa", "crlv-especie", 
                          "crlv-combustivel", "crlv-marca", "crlv-ano-fabricacao", 
                          "crlv-ano-modelo", "crlv-categoria", "crlv-cor", "crlv-observacoes", "crlv-local"]
                
                data = {}
                for field in fields:
                    try:
                        val = await page.locator(f"#{field}").text_content()
                        data[field.replace("crlv-", "")] = val.strip() if val else ""
                    except:
                        data[field.replace("crlv-", "")] = ""
                
                # Checks requested by user:
                retorno_text = await page.locator("#retorno").text_content() or ""
                
                # 1. Gravame
                data["has_gravame"] = "SIM" if "ALIENAÇÃO FIDUCIÁ" in retorno_text.upper() or "GRAVAME" in retorno_text.upper() else "NÃO"
                
                # 2. Indicação de Caixa
                data["indicacao_caixa"] = "SIM" if "INDICAÇÃO DE CAIXA" in retorno_text.upper() or "INDICAÇÃO DE CAIXA" in (data.get("observacoes") or "").upper() else "NÃO"
                
                # 3. GNV check
                data["has_gnv"] = "SIM" if "GNV" in (data.get("combustivel") or "").upper() else "NÃO"
                
                # 4. Comunicação/Intenção de Venda
                obs = (data.get("observacoes") or "").upper()
                data["comunicacao_venda"] = "SIM" if "COMUNICAÇÃO DE VENDA" in obs or "INTENÇÃO DE VENDA" in obs else "NÃO"

                print(f"[+] [DETRAN-Cadastro] Extraction complete. Com. Venda: {data['comunicacao_venda']}")
                return {"status": "success", "data": data}
            print("[-] [DETRAN-Cadastro] Captcha solution failed.")
            return {"status": "error", "message": "Captcha failed"}
        except Exception as e:
            print(f"[!] [DETRAN-Cadastro] Error: {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            await self.close()

    async def get_multas_detalhadas(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Scrapes fine data with detailed parsing for Transitado/Renainf."""
        print(f"[*] [DETRAN-Multas] Starting query for Renavam: {renavam}")
        page = await self.init_browser()
        try:
            url_multas = "https://www2.detran.rj.gov.br/portal/multas/nadaConsta"
            await page.goto(url_multas)
            await page.fill("#MultasRenavam", renavam)
            await page.fill("#MultasCpfcnpj", cpf)
            
            print("[*] [DETRAN-Multas] Solving ReCaptcha...")
            sitekey_element = await page.wait_for_selector("#divCaptcha")
            sitekey = await sitekey_element.get_attribute("data-sitekey")
            captcha_token = await solver.solve_recaptcha_v2(sitekey, url_multas)
            
            if captcha_token:
                print("[+] [DETRAN-Multas] Captcha solved. Submitting...")
                await page.evaluate(f"() => {{ const el = document.getElementById('g-recaptcha-response'); if (el) {{ el.value = '{captcha_token}'; }} }}")
                await page.click("#btPesquisar")
                await self.human_delay(2000, 4000)
                
                try:
                    # Check for Site/Database errors
                    retorno_text = await page.locator("#retorno").inner_text() or ""
                    if "ERRO AO ACESSAR O SERVIÇO" in retorno_text.upper() or "NÃO FOI POSSÍVEL CONSULTAR A BASE DE DADOS" in retorno_text.upper():
                        print("[-] [DETRAN-MultasDetalhe] Site is unstable or database is offline.")
                        return {"status": "error", "message": "DETRAN-RJ em manutenção ou com erro na base de dados (Multas). Tente mais tarde."}

                    # Parse the table more intelligently
                    table_rows = await page.locator(".tabelaDescricao tr").all()
                    fines = []
                    
                    if len(table_rows) > 1: # Header + data
                        headers = await table_rows[0].locator("td, th").all_text_contents()
                        for row in table_rows[1:]:
                            cols = await row.locator("td").all_text_contents()
                            if len(cols) >= len(headers):
                                fine_data = dict(zip(headers, cols))
                                # Clean data
                                fine_data = {k.strip(): v.strip() for k, v in fine_data.items()}
                                
                                # Check logic for Renainf and Transitado
                                status = fine_data.get("Descrição da Situação", "").upper()
                                fine_data["is_transitado"] = "SIM" if "TRANSITADO EM JULGADO" in status else "NÃO"
                                fine_data["is_renainf"] = "SIM" if "RENAINF" in status or "ÓRGÃO AUTUADOR" in fine_data else "NÃO" # Heuristic
                                
                                fines.append(fine_data)
                    
                    print(f"[+] [DETRAN-Multas] {len(fines)} fines extracted.")
                    return {"status": "success", "data": fines}
                except Exception as e:
                    print(f"[-] [DETRAN-Multas] Table parsing failed: {e}")
                    # Fallback to bruto if table parsing fails
                    try:
                        tabela_multas = await page.locator(".tabelaDescricao").inner_text()
                        return {"status": "success", "multas_bruto": tabela_multas.strip(), "error": "Table parse failed, returned raw text"}
                    except:
                        return {"status": "error", "message": "Fines table not found"}
            return {"status": "error", "message": "Captcha failed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            await self.close()

    async def get_multas_data(self, renavam: str, cpf: str) -> Dict[str, Any]:
        """Scrapes fine data using Renavam and CPF."""
        print(f"[*] [DETRAN-Multas] Starting query for Renavam: {renavam}")
        page = await self.init_browser()
        try:
            url_multas = "https://www2.detran.rj.gov.br/portal/multas/nadaConsta"
            await page.goto(url_multas)
            await page.fill("#MultasRenavam", renavam)
            await page.fill("#MultasCpfcnpj", cpf)
            
            print("[*] [DETRAN-Multas] Solving ReCaptcha...")
            sitekey_element = await page.wait_for_selector("#divCaptcha")
            sitekey = await sitekey_element.get_attribute("data-sitekey")
            captcha_token = await solver.solve_recaptcha_v2(sitekey, url_multas)
            
            if captcha_token:
                print("[+] [DETRAN-Multas] Captcha solved. Submitting...")
                # Injection more robust
                await page.evaluate(f"""
                    () => {{
                        const el = document.getElementById('g-recaptcha-response');
                        if (el) {{
                            el.value = '{captcha_token}';
                            el.innerHTML = '{captcha_token}';
                        }}
                    }}
                """)
                await page.click("#btPesquisar")
                await self.human_delay(2000, 4000)
                
                try:
                    # Check for Site/Database errors
                    retorno_text = await page.locator("#retorno").inner_text() or ""
                    if "ERRO AO ACESSAR O SERVIÇO" in retorno_text.upper() or "NÃO FOI POSSÍVEL CONSULTAR A BASE DE DADOS" in retorno_text.upper():
                        print("[-] [DETRAN-Multas] Site is unstable or database is offline.")
                        return {"status": "error", "message": "DETRAN-RJ em manutenção ou com erro na base de dados (Multas). Tente mais tarde."}

                    tabela_multas = await page.locator(".tabelaDescricao").text_content()
                    print("[+] [DETRAN-Multas] Data extracted successfully.")
                    return {"status": "success", "multas_bruto": tabela_multas.strip() if tabela_multas else "Nada consta"}
                except:
                    print("[-] [DETRAN-Multas] Table not found or failed to parse.")
                    return {"status": "partial_success", "message": "Failed to parse Multas table"}
            print("[-] [DETRAN-Multas] Captcha solution failed.")
            return {"status": "error", "message": "Captcha failed"}
        except Exception as e:
            print(f"[!] [DETRAN-Multas] Error: {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            await self.close()

    async def get_nada_consta_apreendido_data(self, placa: str, chassi: str, renavam: str, doc_type: str, doc_num: str) -> Dict[str, Any]:
        """Scrapes clearance data for impounded vehicles (Nada Consta Apreendido)."""
        print(f"[*] [DETRAN-NadaConsta] Starting query for Placa: {placa}")
        page = await self.init_browser()
        try:
            url_nada_consta = "https://www2.detran.rj.gov.br/portal/veiculos/consultaNadaConsta"
            await page.goto(url_nada_consta)
            
            # Fill form fields
            await page.fill("#placa", placa)
            await page.fill("#chassi", chassi)
            await page.fill("#renavam", renavam)
            await page.select_option("#tipo_doc", value=doc_type.lower())
            await page.fill("#num_doc", doc_num)

            # Solving ReCaptcha
            print("[*] [DETRAN-NadaConsta] Solving ReCaptcha...")
            sitekey_element = await page.wait_for_selector("#divCaptcha")
            sitekey = await sitekey_element.get_attribute("data-sitekey")
            captcha_token = await solver.solve_recaptcha_v2(sitekey, url_nada_consta)
            
            if captcha_token:
                print("[+] [DETRAN-NadaConsta] Captcha solved. Submitting...")
                await page.evaluate(f"""
                    () => {{
                        const el = document.getElementById('g-recaptcha-response');
                        if (el) {{
                            el.value = '{captcha_token}';
                            el.innerHTML = '{captcha_token}';
                        }}
                    }}
                """)
                await page.click("#btPesquisar")
                
                # Wait for result container to be visible and have content
                # The page uses Ajax.Updater which updates #retorno
                print("[*] [DETRAN-NadaConsta] Waiting for results...")
                
                # Show waiting div might be helpful to monitor
                await page.wait_for_selector("#retorno", state="visible", timeout=45000)
                await self.human_delay(1000, 2000) 
                
                retorno_locator = page.locator("#retorno")
                retorno_text = await retorno_locator.inner_text()
                
                # Check for Site/Database errors
                if "ERRO AO ACESSAR O SERVIÇO" in retorno_text.upper() or "NÃO FOI POSSÍVEL CONSULTAR A BASE DE DADOS" in retorno_text.upper():
                    print("[-] [DETRAN-Cadastro] Site is unstable or database is offline.")
                    return {"status": "error", "message": "DETRAN-RJ em manutenção ou com erro na base de dados. Tente mais tarde."}

                if "Código de segurança inválido" in retorno_text:
                    print("[-] [DETRAN-NadaConsta] Captcha invalid error from site.")
                    return {"status": "error", "message": "Captcha execution failed on server side"}

                if "VEÍCULO NÃO ENCONTRADO" in retorno_text.upper():
                    return {"status": "error", "message": "Veículo não encontrado"}

                # Extraction logic
                print("[+] [DETRAN-NadaConsta] Extracting data from results...")
                results = {}
                
                # Pendency heading
                pendencias_warning = await page.locator("#erroCaptchaTop").text_content() if await page.locator("#erroCaptchaTop").count() > 0 else "NADA CONSTA"
                results["status_geral"] = pendencias_warning.strip() if pendencias_warning else "OK"

                # Extract specific items from the ordered list
                # Each <li> should be a debit type
                items = await page.locator("#retorno ol li").all()
                debitos = {}
                for item in items:
                    text = await item.inner_text()
                    if ":" in text:
                        parts = text.split(":", 1)
                        key = parts[0].strip().upper().replace(" ", "_").replace(".", "")
                        val = parts[1].strip()
                        debitos[key] = val
                
                results["debitos"] = debitos
                
                # Extract date of consultation (usually at the end: Rio de Janeiro, DD/MM/YYYY HH:MM:SS)
                try:
                    import re
                    date_match = re.search(r"Rio de Janeiro, (\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})", retorno_text)
                    if date_match:
                        results["data_consulta"] = date_match.group(1)
                except:
                    pass

                print(f"[+] [DETRAN-NadaConsta] Query complete. Status: {results['status_geral']}")
                return {"status": "success", "data": results}
            
            print("[-] [DETRAN-NadaConsta] Captcha failed to solve.")
            return {"status": "error", "message": "Captcha failed"}
        except Exception as e:
            print(f"[!] [DETRAN-NadaConsta] Error: {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            await self.close()


