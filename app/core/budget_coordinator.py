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
            print(f"[*] [Budget] Starting Step 1, 2 & 6 (DETRAN RJ)...")
            detran_tasks = [
                detran.get_cadastro_data(placa),
                detran.get_multas_detalhadas(renavam, cpf_proprietario),
                detran.get_nada_consta_apreendido_data(placa, chassi, renavam, "cpf" if len(cpf_proprietario) == 11 else "cnpj", cpf_proprietario)
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

            # --- STEP 4 & 5: Bradesco (GRT & optimized Multas) ---
            print(f"[*] [Budget] Starting Step 4 & 5 (Bradesco) with CPF: {working_cpf}")
            bradesco_res = await bradesco.get_vehicle_data(renavam, working_cpf)
            
            results["step_4_bradesco_grt"] = bradesco_res.get("grt")
            
            # Optimization for Step 5: Merge status from Detran
            fines_bradesco = bradesco_res.get("grm", {}).get("detalhes", [])
            fines_detran = results["step_2_detran_multas"].get("data", []) if results["step_2_detran_multas"].get("status") == "success" else []
            
            for b_fine in fines_bradesco:
                auto = b_fine.get("auto_infracao")
                # Try match by Auto
                match = next((df for df in fines_detran if df.get("Auto") == auto or df.get("Número do Auto") == auto), None)
                if match:
                    b_fine["is_transitado"] = match.get("is_transitado")
                    b_fine["is_renainf"] = match.get("is_renainf")
                else:
                    b_fine["is_transitado"] = "N/A"
                    b_fine["is_renainf"] = "N/A"
            
            results["step_5_bradesco_multas_optimized"] = bradesco_res.get("grm")

            # --- STEP 6 (Part 2): Dívida Ativa in SEFAZ ---
            tiene_divida = False
            nc_data = results["step_6_final_verification"].get("data", {})
            if "SIM" in nc_data.get("debitos", {}).get("DIVIDA_ATIVA", "").upper():
                tiene_divida = True
                
            if tiene_divida:
                print(f"[*] [Budget] Dívida Ativa confirmed. Querying SEFAZ for detailed IPVA/DivAtiva...")
                sefaz_ipva_res = await sefaz_ipva.get_vehicle_data(renavam)
                results["step_6_sefaz_divida_ativa"] = sefaz_ipva_res

            # --- STEP 7: Final persistence ---
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
