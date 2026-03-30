import asyncio
import re
from typing import Dict, Any, List
from playwright.async_api import Page
from app.scrapers.base_scraper import BaseScraper

class BradescoScraper(BaseScraper):
    """
    Scraper para o portal Bradesco Detran RJ.
    Suporta:
    1. GRT - Licenciamento Anual (Débitos Anuais)
    2. GRM - Multas de Trânsito (Histórico de Infrações)
    Realiza a soma total em ambos os casos.
    """

    async def get_vehicle_data(self, renavam: str, cpf_cnpj: str) -> Dict[str, Any]:
        """
        Consulta consolidada de Débitos (GRT) e Multas (GRM).
        """
        print(f"[*] [Bradesco] Iniciando consulta consolidada para Renavam: {renavam}")
        results = {"source": "Bradesco", "renavam": renavam, "status": "success"}
        
        # 1. Consulta IPVA/GRT (Débitos Anuais)
        print("[*] [Bradesco] Consultando Taxas GRT (IPVA/Licenciamento)...")
        grt_data = await self.get_grt_debts(renavam, cpf_cnpj)
        results["grt"] = grt_data
        
        # 2. Consulta Multas GRM
        print("[*] [Bradesco] Consultando Multas GRM...")
        grm_data = await self.get_fines_data(renavam, cpf_cnpj)
        results["grm"] = grm_data
        
        return results

    async def get_grt_debts(self, renavam: str, cpf_cnpj: str) -> Dict[str, Any]:
        """Consulta débitos de IPVA/GRT (Licenciamento Anual)."""
        page = await self.init_browser()
        scraped_data = []
        total_geral = 0.0

        try:
            url = "https://www.ib7.bradesco.com.br/ibpfdetranrj/DebitoVeiculoRJGRTLoaderAction.do"
            await page.goto(url, wait_until="networkidle")
            
            is_iframe = await page.locator("iframe#body-iframe").count() > 0
            target = page.frame_locator("iframe#body-iframe") if is_iframe else page
            
            await self._fill_login_form(target, renavam, cpf_cnpj)
            await target.get_by_title("Continuar").click()
            
            # Espera lista de exercícios
            try:
                await target.get_by_text("Selecione o exercício").wait_for(timeout=15000)
            except Exception:
                if "RENAVAM" not in (await target.locator("body").inner_text()):
                    error_msg = await target.locator(".erro_msg").inner_text() if await target.locator(".erro_msg").count() > 0 else "Dados não encontrados."
                    return {"status": "error", "message": error_msg}

            radios = await target.get_by_title("Marque para selecionar o", exact=False).all()
            total_exercicios = len(radios)
            
            if total_exercicios == 0:
                raw_text = await target.locator("body").inner_text()
                extracted = self._parse_details(raw_text)
                if extracted.get("valor") != "NADA CONSTA":
                     scraped_data.append(extracted)
                     total_geral += self._to_float(extracted.get("valor"))
                
            for i in range(total_exercicios):
                current_radios = await target.get_by_title("Marque para selecionar o", exact=False).all()
                if i < len(current_radios):
                    await current_radios[i].check()
                    await page.wait_for_load_state("networkidle")
                    
                    container = target.locator("form[name='debitoVeiculoRJForm']")
                    if await container.count() == 0:
                         container = target.locator("body")
                    
                    raw_text = await container.inner_text()
                    extracted_item = self._parse_details(raw_text)
                    
                    if extracted_item.get("exercicio") == "NADA CONSTA":
                        for year in range(2020, 2030):
                             if str(year) in raw_text:
                                 extracted_item["exercicio"] = str(year)
                                 break
                    
                    if extracted_item.get("valor") != "NADA CONSTA":
                         total_geral += self._to_float(extracted_item.get("valor"))
                    
                    scraped_data.append(extracted_item)
                    if i < total_exercicios - 1:
                        await page.go_back()
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(1)

            return {
                "status": "success",
                "total_somado": f"R$ {total_geral:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "detalhes": scraped_data
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            await self.close()

    async def get_fines_data(self, renavam: str, cpf_cnpj: str) -> Dict[str, Any]:
        """Consulta o histórico de Multas de Trânsito (GRM)."""
        page = await self.init_browser()
        fines_list = []
        total_multas = 0.0

        try:
            url = "https://www.ib7.bradesco.com.br/ibpfdetranrj/debitoVeiculoRJGrmConsultar.do"
            await page.goto(url, wait_until="networkidle")
            
            is_iframe = await page.locator("iframe#body-iframe").count() > 0
            target = page.frame_locator("iframe#body-iframe") if is_iframe else page
            
            # Login (Multas usa os mesmos campos de Título)
            await self._fill_login_form(target, renavam, cpf_cnpj)
            
            # Seleciona Tipo de Consulta: "Todas" (value 3)
            # No HTML enviado, o título do rádio de valor 3 é "Marque para selecionar individualizada." (mesmo que o texto seja 'Todas')
            await target.locator("input[name='grm.idSeqFuncao'][value='3']").check()
            
            await target.get_by_title("Continuar").click()
            await page.wait_for_load_state("networkidle")

            # Verifica se há tabela de multas
            table = target.locator("table.table-tp1")
            if await table.count() == 0:
                content = await target.locator("body").inner_text()
                if "Não foram encontrados" in content:
                    return {"status": "success", "total_somado": "R$ 0,00", "detalhes": [], "message": "Nenhuma multa encontrada."}
                return {"status": "error", "message": "Tabela de multas não localizada."}

            # Extração das linhas da tabela
            rows = await table.locator("tbody tr").all()
            for row in rows:
                cells = await row.locator("td").all_text_contents()
                if len(cells) >= 6:
                    auto = cells[0].strip()
                    placa = cells[1].strip()
                    data_infracao = cells[2].strip()
                    valor_str = cells[3].strip()
                    vencimento = cells[4].strip()
                    situacao = cells[5].strip()
                    
                    if valor_str:
                        total_multas += self._to_float(valor_str)
                    
                    fines_list.append({
                        "auto_infracao": auto,
                        "placa": placa,
                        "data_infracao": data_infracao,
                        "valor": valor_str,
                        "vencimento": vencimento,
                        "situacao": situacao
                    })

            return {
                "status": "success",
                "total_somado": f"R$ {total_multas:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "detalhes": fines_list
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}
        finally:
            await self.close()

    async def _fill_login_form(self, target, renavam: str, cpf_cnpj: str):
        """Auxiliar para preencher o formulário comum IPVA/Multas."""
        await target.get_by_title("Informe o número do Renavam.").fill(renavam)
        clean_doc = "".join(filter(str.isdigit, cpf_cnpj))
        
        if len(clean_doc) <= 11:
            await target.get_by_title("Informe o número do CPF.").check()
            if len(clean_doc) == 11:
                await target.get_by_title("Informar o primeiro campo do CPF com três posições.").fill(clean_doc[0:3])
                await target.get_by_title("Informar o segundo campo do CPF com três posições.").fill(clean_doc[3:6])
                await target.get_by_title("Informar o terceiro campo do CPF com três posições.").fill(clean_doc[6:9])
                await target.get_by_title("Informar o quarto campo do CPF com três posições.").fill(clean_doc[9:11])
        else:
            await target.get_by_title("Informe o número do CNPJ.").check()
            # Título pode variar levemente entre GRT e GRM, usamos seletor mais genérico se falhar
            try:
                await target.get_by_title("Informar o primeiro campo do CNPJ", exact=False).first.fill(clean_doc[0:12])
                await target.get_by_title("Informar o quinto campo do CNPJ", exact=False).first.fill(clean_doc[12:14])
            except:
                pass

    def _to_float(self, val_str: str) -> float:
        """Converte string monetária (1.234,56) para float."""
        if not val_str or "NADA CONSTA" in val_str:
             return 0.0
        try:
             clean = val_str.replace("R$", "").replace(".", "").replace(",", ".").replace("\xa0", "").strip()
             return float(clean)
        except:
             return 0.0

    def _parse_details(self, text: str) -> Dict[str, str]:
        """Parser para extração do detalhamento GRT."""
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        labels = ["Exercício", "Valor", "Data de Vencimento", "Total a Pagar", "Identificação GRT"]
        result = {l.replace(" ", "_").lower(): "NADA CONSTA" for l in labels}
        
        monetary_matches = re.findall(r'(\d+[\.,]\d{2})', text)
        date_matches = re.findall(r'\d{2}[\./]\d{2}[\./]\d{4}', text)
        
        for i, line in enumerate(lines):
            for label in labels:
                if label.lower() in line.lower():
                    val = line.split(':', 1)[-1].strip() if ":" in line else ""
                    if (not val or val == label) and (i + 1) < len(lines):
                        next_line = lines[i + 1]
                        if not any(l.lower() in next_line.lower() for l in labels): val = next_line
                    if val and len(val) >= 4: result[label.replace(" ", "_").lower()] = val

        if result.get("total_a_pagar") != "NADA CONSTA": result["valor"] = result["total_a_pagar"]
        if result.get("valor") == "NADA CONSTA" and monetary_matches:
             for m in reversed(monetary_matches):
                  if m != "0,00": 
                       result["valor"] = m
                       break
        if result.get("data_de_vencimento") == "NADA CONSTA" and date_matches:
             result["data_de_vencimento"] = date_matches[0]
        return result