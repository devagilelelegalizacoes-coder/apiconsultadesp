from app.scrapers.base_scraper import BaseScraper
from typing import Dict, Any
import asyncio

class DividaAtivaRJScraper(BaseScraper):
    """
    Scraper para consulta de dívida ativa de veículos no portal da Receita Estadual RJ.
    URL: https://consultadividaativa.rj.gov.br/consultadebitosdividaativarj/

    Consulta débitos de dívida ativa (ICMS, ITCMD, etc) para pessoa física/jurídica.
    """

    async def get_divida_ativa(self, cpf_cnpj: str, nome: str = "") -> Dict[str, Any]:
        """
        Busca débitos de dívida ativa para um CPF/CNPJ.

        Args:
            cpf_cnpj: CPF ou CNPJ do proprietário (apenas dígitos)
            nome: Nome completo ou razão social (opcional)

        Returns:
            {
                "status": "success|error",
                "cpf_cnpj": cpf_cnpj_formatado,
                "nome": nome_proprietario,
                "tem_divida": True/False,
                "debitos": [
                    {
                        "numero_divida": "xxx",
                        "data_inscricao": "xx/xx/xxxx",
                        "valor": "R$ x.xxx,xx",
                        "origem": "ICMS|ITCMD|outro"
                    }
                ],
                "total_divida": "R$ x.xxx,xx",
                "data_consulta": "xx/xx/xxxx HH:MM"
            }
        """

        # Sanitizar entrada
        cpf_cnpj_limpo = "".join(filter(str.isdigit, str(cpf_cnpj or "")))
        if not cpf_cnpj_limpo:
            return {
                "source": "DividaAtivaRJ",
                "status": "error",
                "message": "CPF/CNPJ inválido"
            }

        page = await self.init_browser(use_stealth=False)

        try:
            print(f"[*] [DividaAtivaRJ] Consultando dívida ativa para: {cpf_cnpj_limpo}")

            # Acessar portal da dívida ativa
            url = "https://consultadividaativa.rj.gov.br/consultadebitosdividaativarj/servlet/StartCISPage"
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Aguardar e simular interação
            await self.simulate_interaction(page)
            await self.human_delay(1000, 2000)

            # Preencher formulário de busca
            # Geralmente tem campos: CPF/CNPJ e tipo de pessoa

            # Detectar tipo de documento
            doc_type = "CNPJ" if len(cpf_cnpj_limpo) == 14 else "CPF"

            # Tentar preencher campo de CPF/CNPJ (seletor pode variar)
            try:
                # Campo de CPF/CNPJ - tenta múltiplos seletores comuns
                cpf_field = None
                selectors = [
                    "input[name='cpf']",
                    "input[name='CPF']",
                    "input[name='cnpj']",
                    "input[name='CNPJ']",
                    "input[name='documento']",
                    "input[name='Documento']",
                    "#cpf",
                    "#CPFCNPJ",
                    "input[placeholder*='CPF']",
                    "input[placeholder*='CNPJ']"
                ]

                for selector in selectors:
                    try:
                        if await page.query_selector(selector):
                            cpf_field = page.locator(selector)
                            break
                    except:
                        continue

                if not cpf_field:
                    print("[-] [DividaAtivaRJ] Campo CPF/CNPJ não encontrado")
                    return {
                        "source": "DividaAtivaRJ",
                        "status": "error",
                        "message": "Página do portal pode ter mudado de layout"
                    }

                # Preencher CPF/CNPJ
                await cpf_field.fill(cpf_cnpj_limpo)
                await self.human_delay(500, 1000)

                # Selecionar tipo de pessoa se existir
                try:
                    pessoa_type = "JURIDICA" if len(cpf_cnpj_limpo) == 14 else "FISICA"
                    type_selectors = [
                        "select[name='tipoPessoa']",
                        "select[name='TipoPessoa']",
                        "#tipoPessoa"
                    ]

                    for selector in type_selectors:
                        try:
                            if await page.query_selector(selector):
                                await page.locator(selector).select_option(pessoa_type)
                                await self.human_delay(500, 1000)
                                break
                        except:
                            continue
                except:
                    pass  # Campo tipo de pessoa pode não existir

                # Buscar botão de consulta/pesquisa
                search_button = None
                button_selectors = [
                    "button:has-text('Consultar')",
                    "button:has-text('Pesquisar')",
                    "button:has-text('Buscar')",
                    "input[type='submit']",
                    "button[type='submit']"
                ]

                for selector in button_selectors:
                    try:
                        if await page.query_selector(selector):
                            search_button = page.locator(selector).first
                            break
                    except:
                        continue

                if not search_button:
                    print("[-] [DividaAtivaRJ] Botão de consulta não encontrado")
                    return {
                        "source": "DividaAtivaRJ",
                        "status": "error",
                        "message": "Botão de consulta não localizado na página"
                    }

                # Clicar em buscar
                await search_button.click()
                await self.human_delay(3000, 5000)

                # Verificar resultado
                error_messages = [
                    "não encontrado",
                    "nenhum registro",
                    "sem débitos",
                    "não existe",
                    "não consta"
                ]

                page_text = await page.content()
                page_text_lower = page_text.lower()

                # Verificar se há mensagem de "nada consta"
                if any(msg in page_text_lower for msg in error_messages):
                    print(f"[+] [DividaAtivaRJ] Nada consta de dívida ativa para {cpf_cnpj_limpo}")
                    return {
                        "source": "DividaAtivaRJ",
                        "cpf_cnpj": cpf_cnpj_limpo,
                        "status": "success",
                        "tem_divida": False,
                        "debitos": [],
                        "total_divida": "R$ 0,00",
                        "message": "Nada consta de dívida ativa"
                    }

                # Extrair tabela de débitos
                debitos = []
                try:
                    # Procurar por table, div com dados, etc
                    rows = await page.locator("table tbody tr").all()

                    for row in rows:
                        try:
                            cols = await row.locator("td").all()
                            if len(cols) >= 3:
                                numero = (await cols[0].inner_text()).strip()
                                data_inscricao = (await cols[1].inner_text()).strip()
                                valor = (await cols[2].inner_text()).strip()

                                # Tentar extrair origem se houver coluna adicional
                                origem = ""
                                if len(cols) >= 4:
                                    origem = (await cols[3].inner_text()).strip()

                                debitos.append({
                                    "numero_divida": numero,
                                    "data_inscricao": data_inscricao,
                                    "valor": valor,
                                    "origem": origem
                                })
                        except:
                            continue

                except:
                    print("[!] [DividaAtivaRJ] Erro ao extrair tabela de débitos")

                # Se encontrou débitos
                if debitos:
                    print(f"[+] [DividaAtivaRJ] {len(debitos)} débito(s) encontrado(s)")

                    # Tentar calcular total
                    def parse_money(val_str):
                        try:
                            clean = val_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
                            return float(clean)
                        except:
                            return 0.0

                    total = sum(parse_money(d.get("valor", "0")) for d in debitos)
                    total_formatado = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

                    return {
                        "source": "DividaAtivaRJ",
                        "cpf_cnpj": cpf_cnpj_limpo,
                        "status": "success",
                        "tem_divida": True,
                        "debitos": debitos,
                        "total_divida": total_formatado,
                        "message": f"{len(debitos)} débito(s) de dívida ativa encontrado(s)"
                    }
                else:
                    # Página não retornou resultado claro
                    print("[!] [DividaAtivaRJ] Página retornou mas sem débitos claros")
                    return {
                        "source": "DividaAtivaRJ",
                        "cpf_cnpj": cpf_cnpj_limpo,
                        "status": "success",
                        "tem_divida": False,
                        "debitos": [],
                        "total_divida": "R$ 0,00",
                        "message": "Nada consta de dívida ativa"
                    }

            except Exception as e:
                print(f"[-] [DividaAtivaRJ] Erro durante preenchimento: {str(e)}")
                return {
                    "source": "DividaAtivaRJ",
                    "status": "error",
                    "message": f"Erro ao preencher formulário: {str(e)}"
                }

        except Exception as e:
            print(f"[-] [DividaAtivaRJ] Erro geral: {str(e)}")
            return {
                "source": "DividaAtivaRJ",
                "status": "error",
                "message": f"Erro na consulta: {str(e)}"
            }

        finally:
            await self.close()
