import asyncio
from typing import Dict, Optional
from app.scrapers.base_scraper import BaseScraper


def _cpf_valido(cpf: str) -> bool:
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(cpf[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(cpf[n]):
            return False
    return True


def _cnpj_valido(cnpj: str) -> bool:
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(cnpj[i]) * pesos[i] for i in range(n)) % 11
        if (0 if resto < 2 else 11 - resto) != int(cnpj[n]):
            return False
    return True


def normalizar_documento(valor: str) -> Optional[str]:
    """
    The portal stores CPF/CNPJ as a number and drops leading zeros
    (CPF 02413757708 comes back as 2413757708), so pad and pick by check digits.
    """
    digitos = "".join(filter(str.isdigit, valor or ""))
    if len(digitos) < 8:
        return None
    if len(digitos) <= 11 and _cpf_valido(digitos.zfill(11)):
        return digitos.zfill(11)
    if len(digitos) <= 14 and _cnpj_valido(digitos.zfill(14)):
        return digitos.zfill(14)
    return None


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
                documento = None
                for _ in range(30):
                    documento = normalizar_documento(await cpf_field.input_value())
                    if documento:
                        break
                    await asyncio.sleep(0.5)

                if documento:
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
