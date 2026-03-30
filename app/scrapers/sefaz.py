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
        Descobre o CPF/CNPJ do proprietário usando o portal da Fazenda RJ.
        """
        print(f"[*] [Sefaz-RJ] Buscando CPF/CNPJ para o Renavam: {renavam}")
        page = await self.init_browser()
        
        try:
            url = "https://www1.fazenda.rj.gov.br/portaldepagamentos/"
            await page.goto(url, wait_until="networkidle")
            
            # 1. Seleciona Tipo de Pagamento '10' (IPVA)
            await page.locator("#tipoPagamentoLista").select_option("10")
            
            # 2. Preenche o Renavam
            # O sistema do RJ as vezes exige zeros a esquerda (11 digitos)
            full_renavam = renavam.zfill(11)
            await page.locator("#txtNuRenavam").fill(full_renavam)
            
            # 3. Clique em Confirmar para disparar o preenchimento automático
            # Usando seletor de ID ou texto conforme codegen do usuário
            confirm_btn = page.get_by_role("button", name="Confirmar!")
            await confirm_btn.click()
            
            # 4. Aguarda o campo de CPF/CNPJ ser preenchido
            # O portal da Fazenda costuma usar AJAX para preencher o campo sem recarregar a página toda
            cpf_cnpj_field = page.locator("#txtCnpjCpf")
            
            # Esperamos que o valor do campo não esteja vazio
            try:
                # Tenta aguardar até 5 segundos por um valor numérico
                await self._wait_for_value(cpf_cnpj_field, timeout=5000)
            except:
                pass # Prossegue para tentar capturar o que houver
                
            documento = await cpf_cnpj_field.get_attribute("value")
            
            if documento and len(documento.strip()) >= 11:
                clean_doc = "".join(filter(str.isdigit, documento))
                print(f"[+] [Sefaz-RJ] Documento localizado: {clean_doc}")
                return clean_doc
            
            print("[!] [Sefaz-RJ] Não foi possível encontrar o CPF/CNPJ.")
            return None

        except Exception as e:
            print(f"[!] [Sefaz-RJ] Erro na descoberta: {str(e)}")
            return None
        finally:
            await self.close()

    async def _wait_for_value(self, locator, timeout: int = 5000):
        """Helper para aguardar o preenchimento de um valor em um input."""
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout:
            val = await locator.get_attribute("value")
            if val and len(val.strip()) > 1:
                return True
            await asyncio.sleep(0.5)
        raise TimeoutError("Valor não preenchido no tempo esperado.")
