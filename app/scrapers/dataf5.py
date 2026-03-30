import os
import asyncio
from typing import Dict, Any, Optional
from playwright.async_api import Page
from app.scrapers.base_scraper import BaseScraper
from dotenv import load_dotenv

load_dotenv()

class DataF5Scraper(BaseScraper):
    def __init__(self, use_proxy: bool = True):
        super().__init__(use_proxy=use_proxy)
        self.username = os.getenv("DATAF5_USER")
        self.password = os.getenv("DATAF5_PASS")
        self.base_url = "https://www.dataf5.com.br/login"

    async def login(self, page: Page):
        """Perform login on DataF5."""
        print("[*] [DataF5] Navigating to login page...")
        await page.goto(self.base_url)
        
        print("[*] [DataF5] Filling credentials...")
        await page.get_by_role("textbox", name="Nome de usuário").fill(self.username)
        await page.get_by_role("button", name="Continuar").click()
        
        await page.get_by_role("textbox", name="Senha").fill(self.password)
        await page.get_by_role("button", name="Acessar Sistema").click()
        
        # Wait for dashboard to load
        await page.wait_for_selector("div:nth-child(9) > .glass-panel", timeout=15000)
        print("[+] [DataF5] Login successful.")

    async def get_vehicle_data(self, placa: str) -> Dict[str, Any]:
        """Scrapes vehicle data from DataF5 using Plate."""
        print(f"[*] [DataF5] Starting query for Placa: {placa}")
        page = await self.init_browser()
        
        try:
            await self.login(page)
            
            # Locate the 9th card (Consulta Placa Completa)
            print("[*] [DataF5] Navigating to plate query card...")
            input_selector = "div:nth-child(9) > .glass-panel .form-control"
            await page.wait_for_selector(input_selector)
            
            # Apply the specific interaction (4 ArrowLeft presses)
            # This is likely to bypass some UI masking/formatting
            await page.focus(input_selector)
            for _ in range(4):
                await page.keyboard.press("ArrowLeft")
                await asyncio.sleep(0.1)
                
            await page.fill(input_selector, placa)
            
            # Click Consultar
            print("[*] [DataF5] Submitting query...")
            # The user's code used get_by_text("Consultar Gerando PDFs...")
            # But browser exploration showed just "Consultar" might work or be the actual text.
            # We'll try to be flexible.
            try:
                await page.click("div:nth-child(9) > .glass-panel .btn-accent", timeout=5000)
            except:
                await page.get_by_text("Consultar").first.click()

            # Wait for Result Modal
            print("[*] [DataF5] Waiting for results...")
            # Use specifically the visible modal ('show' class) to avoid conflicts with hidden ones
            await page.wait_for_selector("#placaDataModal.show .modal-body", timeout=20000)
            
            # Extract Data
            print("[*] [DataF5] Extracting data from modal...")
            modal_body = page.locator("#placaDataModal .modal-body")
            
            # We'll use a dictionary to map labels to keys
            data = {}
            labels_to_extract = [
                "Placa", "Renavam", "Chassi", "Marca/Modelo", "Cor", 
                "Ano Fabricação", "Ano Modelo", "Ano Fabricação/Modelo", 
                "Combustível", "Categoria", "Espécie", "Tipo Veículo", 
                "Nº Motor", "Origem", "Tipo Carroceria", "Cilindrada", 
                "Potência", "Peso Bruto", "Nº Eixos", "Capacidade Passageiros", 
                "Capacidade Carga", "VIA DO CRV", "Data CRV", "Motivo Emissão", 
                "CRLV Digital", "CRV Digital", "Pendência Emissão", 
                "Existe Comunicação Venda", "Logradouro", "Número", 
                "Complemento", "Cidade", "CEP", "Município Registro", "UF", 
                "Estado", "Nome Possuidor", "Tipo Doc. Possuidor", 
                "Nº Doc. Possuidor", "Tipo Doc. Proprietário", 
                "Nº Doc. Proprietário", "Restrição 1", "Restrição 2", 
                "Restrição 3", "Restrição 4"
            ]
            
            all_text = await modal_body.inner_text()
            lines = [l.strip() for l in all_text.split("\n") if l.strip()]
            
            # Robust parser for Label-Value pairs
            for i, line in enumerate(lines):
                # Clean line from snapshot noise
                line = line.replace(" [level=6]", "").strip()
                
                for label in labels_to_extract:
                    lbl_lower = label.lower()
                    line_lower = line.lower()
                    
                    if lbl_lower in line_lower:
                        # Case 1: Label occurs in the line (e.g., "Placa: KWH6E62" or "Placa KWH6E62")
                        # Try to find where label ends
                        lbl_pos = line_lower.find(lbl_lower)
                        after_lbl = line[lbl_pos + len(label):].strip()
                        
                        # Remove leading colons or spaces
                        if after_lbl.startswith(":"):
                            after_lbl = after_lbl[1:].strip()
                            
                        # If after_lbl is not empty, it's likely the value
                        if after_lbl and len(after_lbl) > 1:
                            data[label] = after_lbl
                        elif i + 1 < len(lines):
                            # Case 2: Value is in the NEXT line
                            if label not in data:
                                next_line = lines[i+1].strip()
                                # Simple heuristic to avoid taking another label as value
                                if not any(next_line.lower().startswith(l.lower()) for l in labels_to_extract[:10]):
                                    data[label] = next_line
            
            # Post-processing for combined fields
            if "Ano Fabricação" not in data and "Ano Fabricação/Modelo" in data:
                 val = data["Ano Fabricação/Modelo"]
                 if "/" in val:
                     parts = val.split("/")
                     data["Ano Fabricação"] = parts[0].strip()
                     data["Ano Modelo"] = parts[1].strip()
            
            print(f"[+] [DataF5] Extraction complete for {placa}. {len(data)} fields found.")
            return {
                "source": "DataF5",
                "placa": placa,
                "status": "success",
                "data": data
            }

        except Exception as e:
            print(f"[!] [DataF5] Error: {str(e)}")
            return {
                "source": "DataF5",
                "placa": placa,
                "status": "error",
                "message": str(e)
            }
        finally:
            await self.close()

    async def get_gravame_data(self, chassi: str) -> Dict[str, Any]:
        """Scrapes Gravame data from DataF5 using Chassi."""
        print(f"[*] [DataF5] Starting Gravame query for Chassi: {chassi}")
        page = await self.init_browser()
        
        try:
            await self.login(page)
            
            print("[*] [DataF5] Navigating to Gravame query...")
            card = page.locator(".glass-panel:has-text('Consulta Gravame')")
            
            # Fill the Chassi input within that card
            await card.get_by_placeholder("Digite o Chassi").first.fill(chassi)
            # Find the Consultar button in that card
            await card.get_by_role("button", name="Consultar").first.click()
            
            # Wait for Result Modal
            print("[*] [DataF5] Waiting for Gravame results...")
            await page.wait_for_selector("#placaDataModal.show .modal-body", timeout=20000)
            
            # Extract Data from modal using Label-Value pairs
            modal_body = page.locator("#placaDataModal.show .modal-body")
            all_text = await modal_body.inner_text()
            lines = [l.strip() for l in all_text.split("\n") if l.strip()]
            
            data = {}
            labels_to_extract = [
                "Chassi", "Placa", "renavam", "ano modelo", "ano fabricação",
                "Status do veículo", "Data status", "Informante restrição",
                "Assinatura eletrônica", "Gravame", "UF Gravame",
                "Financiado - CPF/CNPJ", "Financiado - Nome",
                "Agente financeiro - Código", "Agente financeiro - Nome",
                "Contrato - Número", "Contrato - Data"
            ]
            
            # Parser for Gravame (often "Label: Value" or split lines)
            for i, line in enumerate(lines):
                for label in labels_to_extract:
                    lbl_lower = label.lower()
                    line_lower = line.lower()
                    
                    if lbl_lower in line_lower:
                        # Case 1: Label and value in the same line
                        # Try to split by colon
                        parts = line.split(":", 1)
                        after_lbl = parts[1].strip() if len(parts) > 1 else ""
                        
                        # If not split by colon, check text after label
                        if not after_lbl:
                            idx = line_lower.find(lbl_lower)
                            potential_val = line[idx + len(label):].strip()
                            if potential_val:
                                after_lbl = potential_val
                        
                        # Store if value is significantly long
                        if after_lbl and len(after_lbl) > 1:
                            data[label] = after_lbl
                        elif i + 1 < len(lines):
                            # Case 2: Value is in the NEXT line
                            if label not in data:
                                next_line = lines[i+1].strip()
                                # Guard against picking another label as value
                                if not any(next_line.lower().startswith(l.lower()) for l in labels_to_extract[:10]):
                                    data[label] = next_line

            print(f"[+] [DataF5] Gravame extraction complete for {chassi}")
            return {
                "source": "DataF5Gravame",
                "chassi": chassi,
                "status": "success",
                "data": data
            }

        except Exception as e:
            print(f"[!] [DataF5] Gravame Error: {str(e)}")
            return {
                "source": "DataF5Gravame",
                "chassi": chassi,
                "status": "error",
                "message": str(e)
            }
        finally:
            await self.close()

class DataF5Gravame(DataF5Scraper):
    async def get_gravame_data(self, chassi: str) -> Dict[str, Any]:
        """Scrapes Gravame data from DataF5 using Chassi."""
        return await super().get_gravame_data(chassi)
        


    