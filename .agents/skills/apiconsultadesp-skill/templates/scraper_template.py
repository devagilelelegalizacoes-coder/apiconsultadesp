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
        Consulta padrão para coleta de dados de veículo.
        Substitua com a lógica específica do novo portal.
        """
        print(f"[*] [Template] Iniciando consulta para Placa: {placa}")
        
        # Inicializa o browser e abre uma nova aba com os listeners e stealth (se ativado)
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
            # Tipos de tarefas comuns: NoCaptchaTaskProxyless ou RecaptchaV2EnterpriseTaskProxyless
            token = await solver.solve_recaptcha_v2(
                sitekey=sitekey, 
                url=url_consulta, 
                task_type="NoCaptchaTaskProxyless"
            )
            
            if not token:
                raise Exception("Falha na resolução do Captcha (Timeout/API Key errada).")
                
            print("[*] [Template] Injetando token na página...")
            # Chama o utilitário de injeção padrão (ou implemente localmente caso mude o campo)
            await page.evaluate(f"document.getElementById('g-recaptcha-response').value = '{token}';")
            
            # 4. Envio do Formulário (com clique robusto JS fallback)
            print("[*] [Template] Enviando consulta...")
            try:
                await page.click("#btn-consultar", timeout=5000)
            except Exception as e:
                print(f"[!] Clique direto falhou ({e}), clicando via JS...")
                await page.evaluate("document.getElementById('btn-consultar').click()")
                
            # 5. Aguarda retorno e faz o parsing
            await page.wait_for_selector("#resultado-dados, .erro-mensagem", state="visible", timeout=15000)
            
            # Checar mensagens de erro do portal
            if await page.locator(".erro-mensagem").count() > 0:
                msg_erro = await page.locator(".erro-mensagem").inner_text()
                if "não encontrado" in msg_erro.lower():
                    return {"status": "success", "data": {}, "message": "Veículo não encontrado."}
                raise Exception(f"Erro retornado pelo portal: {msg_erro}")
                
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
            print(f"[!] [Template] Erro na consulta: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
        finally:
            # CRÍTICO: Sempre fechar o browser no final do bloco para evitar leaks de memória no servidor
            await self.close()
