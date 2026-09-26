import asyncio
from typing import Dict, Optional
from app.scrapers.base_scraper import BaseScraper


class SefazDiscoveryScraper(BaseScraper):
    """
    Portal de Pagamentos da Fazenda RJ: a partir do RENAVAM, o portal preenche
    o CPF/CNPJ e o nome do contribuinte atual do IPVA (em caso de comunicação
    de venda, é o comprador).
    """

    async def discovery_owner_document(self, renavam: str) -> Optional[Dict[str, str]]:
        """Retorna {"cpf": ..., "nome": ...} ou None."""
        for attempt in range(1, 4):
            print(f"[*] [Sefaz-RJ] Buscando CPF/CNPJ para o Renavam: {renavam} (Tentativa {attempt})")
            page = await self.init_browser(use_stealth=False)
            try:
                await page.goto("https://www1.fazenda.rj.gov.br/portaldepagamentos/", wait_until="networkidle", timeout=60000)
                await page.locator("#tipoPagamentoLista").select_option("10")
                await page.locator("#txtNuRenavam").wait_for(state="visible", timeout=15000)
                await page.locator("#txtNuRenavam").fill(renavam.zfill(11))
                await page.get_by_role("button", name="Confirmar!").click()

                # Values are filled by JS, so read the DOM property (input_value), not the HTML attribute
                cpf_field = page.locator("#txtCnpjCpf")
                documento = ""
                for _ in range(30):
                    documento = "".join(filter(str.isdigit, await cpf_field.input_value()))
                    if len(documento) >= 11:
                        break
                    await asyncio.sleep(0.5)

                if len(documento) >= 11:
                    nome = (await page.locator("#txtNomeRazaoSocial").input_value()).strip()
                    print(f"[+] [Sefaz-RJ] Documento localizado: {documento} ({nome})")
                    return {"cpf": documento, "nome": nome}

                body_text = (await page.evaluate("document.body.innerText")).lower()
                if "inválido" in body_text or "invalido" in body_text or "não cadastrado" in body_text:
                    print("[-] [Sefaz-RJ] Renavam inválido ou não cadastrado na Sefaz. Abortando retries.")
                    return None
                print("[!] [Sefaz-RJ] Não foi possível encontrar o CPF/CNPJ nesta tentativa.")
            except Exception as e:
                print(f"[!] [Sefaz-RJ] Erro na descoberta (Tentativa {attempt}): {e}")
            finally:
                await self.close()
        return None
