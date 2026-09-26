import asyncio
from datetime import datetime
from typing import Dict, Any, List
from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.sefaz import SefazDiscoveryScraper
from app.scrapers.bradesco import BradescoScraper
from app.infrastructure.supabase_db import db as database
from app.core.business_rules import BusinessRuleAnalyzer

class BudgetCoordinator:
    async def run_budget_query(self, placa: str, renavam: str, cpf: str, chassi: str | None = None, user: str = "system") -> Dict[str, Any]:
        """
        Executes the budget workflow.

        Renavam/CPF are required; Chassi is optional and only used by the
        "nada consta apreendido" check.
        """
        results = {
            "step_1_detran_cadastro": None,
            "step_2_detran_multas": None,
            "step_3_owner_discovery": None,
            "step_4_bradesco_grt": None,
            "step_5_bradesco_multas_optimized": None,
            "step_6_final_verification": None,
            "step_7_relatorio_decisao": None,
            "errors": []
        }

        # Scrapers
        detran = DetranRJScraper()
        sefaz_disc = SefazDiscoveryScraper()
        sefaz_ipva = SefazRJScraper()
        bradesco = BradescoScraper()

        try:
            # --- Input validation (Renavam/CPF now come from the caller) ---
            renavam = "".join(filter(str.isdigit, str(renavam or "")))
            cpf_proprietario = "".join(filter(str.isdigit, str(cpf or "")))

            if not renavam:
                raise Exception("Renavam is required.")
            if not cpf_proprietario:
                raise Exception("CPF/CNPJ is required.")

            # --- STEP 0: NADA CONSTA APREENDIDO (Decision Initializer) ---
            # This step must run first to determine which other queries to skip
            doc_type = "cpf" if len(cpf_proprietario) == 11 else "cnpj"

            if chassi:
                print(f"[*] [Budget] Step 0: Starting Nada Consta Apreendido (Decision Point)...")
                nada_consta_result = await DetranRJScraper().get_nada_consta_apreendido_data(
                    placa, chassi, renavam, doc_type, cpf_proprietario
                )
                results["step_6_final_verification"] = nada_consta_result
            else:
                print(f"[*] [Budget] Step 0: Skipping Nada Consta (Chassi nao informado)...")
                results["step_6_final_verification"] = {"status": "skipped", "message": "Chassi nao informado."}

            # --- STEP 1 & 2: DETRAN Cadastro & Multas ---
            # NOTE: We use separate instances to avoid concurrency issues with self.browser.close()
            print(f"[*] [Budget] Starting Step 1 & 2 (DETRAN RJ Cadastro & Multas)...")
            detran_tasks = [
                DetranRJScraper().get_cadastro_data(placa),
                DetranRJScraper().get_multas_detalhadas(renavam, cpf_proprietario),
            ]

            detran_raw = await asyncio.gather(*detran_tasks, return_exceptions=True)

            # Step 1: Cadastro (Gravame, Caixa, GNV, Com.Venda)
            results["step_1_detran_cadastro"] = detran_raw[0] if not isinstance(detran_raw[0], Exception) else {"status": "error", "message": str(detran_raw[0])}

            # Step 2: Detailed Multas
            results["step_2_detran_multas"] = detran_raw[1] if not isinstance(detran_raw[1], Exception) else {"status": "error", "message": str(detran_raw[1])}

            # --- STEP 3: SEFAZ (Only if Com. Venda detected) ---
            com_venda = results["step_1_detran_cadastro"].get("data", {}).get("comunicacao_venda") == "SIM" if results["step_1_detran_cadastro"].get("status") == "success" else False
            working_cpf = cpf_proprietario
            
            if com_venda:
                print(f"[*] [Budget] Communication of Sale detected. Discovering actual owner CPF via SEFAZ...")
                sefaz_cpf = await sefaz_disc.discovery_owner_document(renavam)
                if sefaz_cpf:
                    results["step_3_owner_discovery"] = {"status": "success", "cpf": sefaz_cpf}
                    working_cpf = sefaz_cpf
                    print(f"[+] [Budget] New owner CPF discovered: {working_cpf}")
                else:
                    results["step_3_owner_discovery"] = {"status": "error", "message": "Failed to discover new CPF"}

            # --- STEP 4, 5 & 6-Part-2: Bradesco (GRT & Multas) and SEFAZ (IPVA/DívAtiva) ---
            print(f"[*] [Budget] Starting Step 4, 5 & SEFAZ with CPF: {working_cpf}")
            
            # Smart Skip Check from Nada Consta
            nc_debitos = results["step_6_final_verification"].get("data", {}).get("debitos", {})
            has_ipva_grt = "SIM" in str(nc_debitos.get("IPVA", "")).upper() or \
                           "SIM" in str(nc_debitos.get("TAXA_DE_LICENCIAMENTO_ANUAL", "")).upper() or \
                           "SIM" in str(nc_debitos.get("LICENCIAMENTO_ATRASADO", "")).upper()
            
            has_divida = "SIM" in str(nc_debitos.get("DIVIDA_ATIVA", "")).upper() or \
                         "SIM" in str(nc_debitos.get("DÍVIDA_ATIVA", "")).upper() or has_ipva_grt

            bradesco_tasks = []
            task_mapping = [] # To keep track of what results go where
            
            # Step 4: Bradesco GRT
            if has_ipva_grt:
                bradesco_tasks.append(BradescoScraper().get_grt_debts(renavam, working_cpf))
                task_mapping.append("step_4_bradesco_grt")
            else:
                results["step_4_bradesco_grt"] = {"status": "success", "total_somado": "R$ 0,00", "detalhes": [], "message": "Sem débitos (Nada Consta)"}

            # Step 5: Bradesco Multas (Always)
            bradesco_tasks.append(BradescoScraper().get_fines_data(renavam, working_cpf))
            task_mapping.append("step_5_bradesco_multas_optimized")

            # Step 6 Part 2: SEFAZ (IPVA/Dívida Ativa)
            if has_divida:
                bradesco_tasks.append(SefazRJScraper().get_vehicle_data(renavam))
                task_mapping.append("step_6_sefaz_ipva")
            else:
                results["step_6_sefaz_ipva"] = {"status": "success", "message": "Sem débitos na SEFAZ (Nada Consta)"}
            
            # Run all in parallel
            parallel_results = await asyncio.gather(*bradesco_tasks, return_exceptions=True)
            
            for i, task_name in enumerate(task_mapping):
                res = parallel_results[i]
                results[task_name] = res if not isinstance(res, Exception) else {"status": "error", "message": str(res)}
            
            grm_res = results.get("step_5_bradesco_multas_optimized", {"status": "error"})
            
            # Optimization for Step 5: Merge status from Detran
            fines_bradesco = grm_res.get("detalhes", []) if grm_res.get("status") == "success" else []
            fines_detran = results["step_2_detran_multas"].get("data", []) if results["step_2_detran_multas"].get("status") == "success" else []
            
            for b_fine in fines_bradesco:
                auto = b_fine.get("auto_infracao")
                # Try match by Auto
                match = next((df for df in fines_detran if df.get("auto_de_infração") == auto or df.get("auto_infracao") == auto), None)
                if match:
                    tipo_status = (match.get("tipo_status") or "").upper()
                    b_fine["is_transitado"] = match.get("is_transitado") or ("SIM" if "TRANSITADO" in tipo_status else "NÃO")
                    b_fine["is_renainf"] = match.get("is_renainf") or ("SIM" if "RENAINF" in tipo_status else "NÃO")
                else:
                    b_fine["is_transitado"] = "N/A"
                    b_fine["is_renainf"] = "N/A"

            # Step 6-Part-2 already handled in parallel above

            # --- STEP 7: RELATÓRIO PARA TOMADA DE DECISÃO ---
            print(f"[*] [Budget] Generating Decision Report for {placa}...")

            cadastro_data = results.get("step_1_detran_cadastro", {}).get("data", {})
            nada_consta_data = results.get("step_6_final_verification", {}).get("data", {})
            fines_bradesco = results.get("step_5_bradesco_multas_optimized", {}).get("detalhes", [])

            # Aplicar regras de negócio
            business_analysis = BusinessRuleAnalyzer.generate_business_analysis(
                cadastro_data=cadastro_data,
                nada_consta_data=nada_consta_data,
                fines_list=fines_bradesco,
                service_type="default"
            )

            # Multas Transitadas em Julgado
            multas_transitadas = [
                m for m in fines_bradesco
                if str(m.get("is_transitado", "")).upper() == "SIM"
            ]

            relatorio_decisao = {
                "analise_negocio": business_analysis,
                "pode_regularizar": business_analysis["pode_regularizar"],
                "status": business_analysis["status"],
                "impedimentos": business_analysis["impedimentos"],
                "avisos": business_analysis["avisos"],
                "acoes_necessarias": business_analysis["acoes_necessarias"],
                "multas_transitadas_julgado": {
                    "quantidade": len(multas_transitadas),
                    "detalhes": multas_transitadas,
                    "observacao": "Pagar conforme tipo de serviço"
                },
                "mensagem_ao_cliente": "Existe impedimento para regularização do veículo. Solicite análise e orçamento ao despachante." if not business_analysis["pode_regularizar"] else "Veículo apto para regularização"
            }
            results["step_7_relatorio_decisao"] = relatorio_decisao

            # --- STEP 8: CALCULAR RESUMO DE DÉBITOS ---
            def parse_money(val_str):
                if not val_str or not isinstance(val_str, str): return 0.0
                try:
                    # Clear R$, dots, and change comma to dot
                    clean = val_str.replace("R$", "").replace(".", "").replace(",", ".").replace(" ", "").strip()
                    return float(clean)
                except: return 0.0
            
            total_grt = parse_money(results.get("step_4_bradesco_grt", {}).get("total_somado", "0,00"))
            total_grm = parse_money(results.get("step_5_bradesco_multas_optimized", {}).get("total_somado", "0,00"))
            
            # Sefaz Summation
            total_sefaz = 0.0
            sefaz_data = results.get("step_6_sefaz_ipva", {}).get("data", {})
            if isinstance(sefaz_data, dict):
                sefaz_ipva_list = sefaz_data.get("debitos_ipva", [])
                for s_debt in sefaz_ipva_list:
                    total_sefaz += parse_money(s_debt.get("total_a_pagar", "0,00"))
            
            valor_total_debitos = total_grt + total_grm + total_sefaz
            
            results["resumo_orcamento"] = {
                "total_grt_ipva": f"R$ {total_grt:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "total_multas": f"R$ {total_grm:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "total_sefaz_divida": f"R$ {total_sefaz:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "valor_total_debitos": f"R$ {valor_total_debitos:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "data_atualizacao": results.get("step_6_final_verification", {}).get("data", {}).get("data_consulta")
            }

            # --- STEP 9: Final persistence ---
            cache_key = f"budget:{placa}"
            await database.set(cache_key, results, expire=86400) # Save for 24h
            
            print(f"[+] [Budget] Workflow complete for {placa}. Saved to ID: {cache_key}")
            return {
                "status": "success",
                "placa": placa,
                "renavam": renavam,
                "data": results,
                "db_key": cache_key
            }

        except Exception as e:
            print(f"[!] [Budget] Workflow collapsed: {str(e)}")
            return {
                "status": "error",
                "message": str(e),
                "partial_results": results
            }
