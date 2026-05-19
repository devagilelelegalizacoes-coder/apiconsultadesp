import asyncio
from typing import Dict, Any, Optional
from playwright.async_api import Page
from app.scrapers.base_scraper import BaseScraper

class SefazDiscoveryScraper(BaseScraper):
    """
    Scraper para o Portal de Pagamentos da Fazenda RJ (Sefaz-RJ).
    Permite descobrir o CPF/CNPJ do proprietário a partir do Renavam.
    """

    async def discovery_owner_document(self, renavam: str) -> Optional[str]:
        """
        Descobre o CPF/CNPJ do proprietário usando o portal da Fazenda RJ com retries.
        """
        max_retries = 2
        for attempt in range(max_retries + 1):
            print(f"[*] [Sefaz-RJ] Buscando CPF/CNPJ para o Renavam: {renavam} (Tentativa {attempt+1})")
            page = await self.init_browser()
            
            try:
                url = "https://www1.fazenda.rj.gov.br/portaldepagamentos/"
                await page.goto(url, wait_until="networkidle")
                
                # 1. Seleciona Tipo de Pagamento '10' (IPVA)
                await page.locator("#tipoPagamentoLista").select_option("10")
                
                # 2. Preenche o Renavam
                full_renavam = renavam.zfill(11)
                await page.locator("#txtNuRenavam").fill(full_renavam)
                
                # 3. Clique em Confirmar para disparar o preenchimento automático
                confirm_btn = page.get_by_role("button", name="Confirmar!")
                await confirm_btn.click()
                
                # 4. Aguarda o campo de CPF/CNPJ ser preenchido
                cpf_cnpj_field = page.locator("#txtCnpjCpf")
                
                try:
                    await self._wait_for_value(cpf_cnpj_field, timeout=5000)
                except:
                    pass # Prossegue para checar o que houver
                    
                documento = await cpf_cnpj_field.get_attribute("value")
                
                if documento and len(documento.strip()) >= 11:
                    clean_doc = "".join(filter(str.isdigit, documento))
                    print(f"[+] [Sefaz-RJ] Documento localizado: {clean_doc}")
                    return clean_doc
                
                # Se não localizou o documento, vamos checar se a página exibiu algum erro impeditivo
                body_text = await page.evaluate("document.body.innerText")
                if "inválido" in body_text.lower() or "invalido" in body_text.lower() or "não cadastrado" in body_text.lower():
                    print(f"[-] [Sefaz-RJ] Renavam inválido ou não cadastrado na Sefaz. Abortando retries.")
                    return None
                
                print("[!] [Sefaz-RJ] Não foi possível encontrar o CPF/CNPJ nesta tentativa.")
                
            except Exception as e:
                print(f"[!] [Sefaz-RJ] Erro na descoberta (Tentativa {attempt+1}): {str(e)}")
            finally:
                await self.close()
                
        return None

    async def _wait_for_value(self, locator, timeout: int = 5000):
        """Helper para aguardar o preenchimento de um valor em um input."""
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout:
            val = await locator.get_attribute("value")
            if val and len(val.strip()) > 1:
                return True
            await asyncio.sleep(0.5)
        raise TimeoutError("Valor não preenchido no tempo esperado.")
