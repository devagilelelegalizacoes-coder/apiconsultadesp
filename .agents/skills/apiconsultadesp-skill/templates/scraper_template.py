import os
import asyncio
from typing import Dict, Any, Optional
from playwright.async_api import Page
from app.scrapers.base_scraper import BaseScraper
from app.infrastructure.captcha_solver import solver

class TemplateScraper(BaseScraper):
    """
    Template Scraper de referência para desenvolvimento de novos portais.
    Herda de BaseScraper para herdar a inicialização do Chromium, proxy e listeners de debug.
    """
    def __init__(self, use_proxy: bool = True):
        super().__init__(use_proxy=use_proxy)
        # Inicializa credenciais do .env caso o portal exija autenticação
        self.username = os.getenv("TEMPLATE_PORTAL_USER")
        self.password = os.getenv("TEMPLATE_PORTAL_PASS")
        self.base_url = "https://www.exampleportal.com/login"

    async def login(self, page: Page):
        """Implementação padrão de autenticação, se aplicável."""
        print("[*] [Template] Navegando para página de login...")
        await page.goto(self.base_url)
        
        # Preenchimento humano simulação
        print("[*] [Template] Preenchendo credenciais...")
        await page.get_by_role("textbox", name="Usuário").fill(self.username)
        await page.get_by_role("textbox", name="Senha").fill(self.password)
        
        # Atraso humano antes de clicar para evitar detecção bot
        await self.human_delay(500, 1000)
        await page.get_by_role("button", name="Entrar").click()
        
        # Aguarda dashboard ou elemento pós-login
        await page.wait_for_selector(".dashboard-panel", timeout=15000)
        print("[+] [Template] Login bem-sucedido.")

    async def get_vehicle_data(self, placa: str) -> Dict[str, Any]:
        """
        Consulta padrão para coleta de dados de veículo com retries e recycling do browser.
        Substitua com a lógica específica do novo portal.
        """
        max_retries = 2
        for attempt in range(max_retries + 1):
            print(f"[*] [Template] Iniciando consulta para Placa: {placa} (Tentativa {attempt+1})")
            
            # Inicializa o browser de forma limpa DENTRO do loop de tentativas
            page = await self.init_browser(use_stealth=True)
            try:
                # 1. Navegar para a página de consulta
                url_consulta = "https://www.exampleportal.com/consulta"
                await page.goto(url_consulta)
                
                # 2. Preencher os parâmetros
                await page.fill("#placa-input", placa)
                
                # 3. Tratamento de Captcha (ReCaptcha V2 / Enterprise)
                print("[*] [Template] Localizando seletor de Captcha...")
                sitekey_element = await page.wait_for_selector("#recaptcha-widget", state="attached")
                sitekey = await sitekey_element.get_attribute("data-sitekey")
                
                print(f"[*] [Template] Resolvendo Captcha com provedor {solver.provider}...")
                token = await solver.solve_recaptcha_v2(
                    sitekey=sitekey, 
                    url=url_consulta, 
                    task_type="NoCaptchaTaskProxyless"
                )
                
                if not token:
                    print("[!] [Template] Falha ao obter token do Captcha. Tentando novamente...")
                    continue
                    
                print("[*] [Template] Injetando token na página...")
                await page.evaluate(f"document.getElementById('g-recaptcha-response').value = '{token}';")
                
                # 4. Envio do Formulário (com clique robusto JS fallback)
                print("[*] [Template] Enviando consulta...")
                try:
                    await page.click("#btn-consultar", timeout=5000)
                except Exception as e:
                    print(f"[!] Clique direto falhou ({e}), clicando via JS...")
                    await page.evaluate("document.getElementById('btn-consultar').click()")
                    
                # 5. Aguarda retorno e faz o parsing
                try:
                    await page.wait_for_selector("#resultado-dados, .erro-mensagem, .captcha-invalido", state="visible", timeout=25000)
                except Exception as wfs_err:
                    print(f"[!] [Template] Timeout aguardando resposta. Capturando body...")
                    
                # Checar se a página retornou erro de Captcha Inválido para tentar de novo
                body_text = await page.evaluate("document.body.innerText")
                if "captcha inválido" in body_text.lower() or await page.locator(".captcha-invalido").count() > 0:
                    print("[!] [Template] Captcha inválido detectado pelo portal. Tentando novamente...")
                    continue
                    
                # Checar mensagens de erro específicas do portal
                if await page.locator(".erro-mensagem").count() > 0:
                    msg_erro = await page.locator(".erro-mensagem").inner_text()
                    if "não encontrado" in msg_erro.lower() or "não existe" in msg_erro.lower():
                        return {"status": "success", "data": {}, "message": "Veículo não encontrado."}
                    return {"status": "error", "message": msg_erro.strip()}
                    
                # Parsing dos dados estruturados
                data = {
                    "marca_modelo": (await page.locator("#marca-modelo").inner_text()).strip(),
                    "renavam": (await page.locator("#renavam").inner_text()).strip(),
                    "ano": (await page.locator("#ano-fabricacao").inner_text()).strip()
                }
                
                return {
                    "status": "success",
                    "source": "TemplatePortal",
                    "data": data
                }
                
            except Exception as e:
                print(f"[!] [Template] Erro na tentativa {attempt+1}: {e}")
            finally:
                # CRÍTICO: Sempre fechar o browser no final do bloco de cada tentativa
                # para reciclar o contêiner e evitar vazamento de memória.
                await self.close()
                
        return {"status": "error", "message": "Falha na consulta após o número máximo de tentativas."}
