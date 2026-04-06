import asyncio
from typing import Dict, Any, List
from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.sefaz import SefazDiscoveryScraper
from app.scrapers.bradesco import BradescoScraper
from app.scrapers.dataf5 import DataF5Scraper
from app.infrastructure.supabase_db import db as database

class BudgetCoordinator:
    async def run_budget_query(self, placa: str, user: str = "system") -> Dict[str, Any]:
        """
        Executes the 7-step budget workflow.
        """
        results = {
            "step_0_dataf5": None,
            "step_1_detran_cadastro": None,
            "step_2_detran_multas": None,
            "step_3_owner_discovery": None,
            "step_4_bradesco_grt": None,
            "step_5_bradesco_multas_optimized": None,
            "step_6_final_verification": None,
            "errors": []
        }

        # Scrapers
        df5 = DataF5Scraper()
        detran = DetranRJScraper()
        sefaz_disc = SefazDiscoveryScraper()
        sefaz_ipva = SefazRJScraper()
        bradesco = BradescoScraper()

        try:
            # --- STEP 0: DataF5 Discovery ---
            print(f"[*] [Budget] Starting Step 0 (DataF5) for Placa: {placa}")
            df5_res = await df5.get_vehicle_data(placa)
            results["step_0_dataf5"] = df5_res
            
            if df5_res.get("status") != "success":
                raise Exception(f"DataF5 failed: {df5_res.get('message')}")

            v_info = df5_res.get("data", {})
            renavam = v_info.get("Renavam")
            chassi = v_info.get("Chassi")
            cpf_proprietario = v_info.get("Nº Doc. Proprietário")
            
            if not renavam:
                raise Exception("Renavam not found in DataF5 query.")

            # --- STEP 1, 2 & 6: DETRAN Parallel Queries ---
            # NOTE: We use separate instances to avoid concurrency issues with self.browser.close()
            print(f"[*] [Budget] Starting Step 1, 2 & 6 (DETRAN RJ)...")
            detran_tasks = [
                DetranRJScraper().get_cadastro_data(placa),
                DetranRJScraper().get_multas_detalhadas(renavam, cpf_proprietario),
                DetranRJScraper().get_nada_consta_apreendido_data(placa, chassi, renavam, "cpf" if len(cpf_proprietario) == 11 else "cnpj", cpf_proprietario)
            ]
            
            detran_raw = await asyncio.gather(*detran_tasks, return_exceptions=True)
            
            # Step 1: Cadastro (Gravame, Caixa, GNV, Com.Venda)
            results["step_1_detran_cadastro"] = detran_raw[0] if not isinstance(detran_raw[0], Exception) else {"status": "error", "message": str(detran_raw[0])}
            
            # Step 2: Detailed Multas
            results["step_2_detran_multas"] = detran_raw[1] if not isinstance(detran_raw[1], Exception) else {"status": "error", "message": str(detran_raw[1])}
            
            # Step 6 (Part 1): Nada Consta Apreendido
            results["step_6_final_verification"] = detran_raw[2] if not isinstance(detran_raw[2], Exception) else {"status": "error", "message": str(detran_raw[2])}

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
                    b_fine["is_transitado"] = match.get("is_transitado") or "SIM" if "TRANSITADO" in (match.get("tipo_status") or "").upper() else "NÃO"
                    b_fine["is_renainf"] = match.get("is_renainf") or "SIM" if "RENAINF" in (match.get("tipo_status") or "").upper() else "NÃO"
                else:
                    b_fine["is_transitado"] = "N/A"
                    b_fine["is_renainf"] = "N/A"

            # Step 6-Part-2 already handled in parallel above

            # --- STEP 7: CALCULAR RESUMO DE DÉBITOS ---
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

            # --- STEP 8: Final persistence ---
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
