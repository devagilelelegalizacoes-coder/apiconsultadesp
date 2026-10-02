import asyncio
import unicodedata
from datetime import datetime
from typing import Dict, Any, List
from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.sefaz import SefazDiscoveryScraper
from app.scrapers.bradesco import BradescoScraper
from app.scrapers.divida_ativa_rj import DividaAtivaRJScraper
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
            "step_6b_divida_ativa": None,
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

            # --- STEP 0, 1 & 2: Nada Consta Apreendido + DETRAN Cadastro & Multas (in parallel) ---
            # Nada Consta decides which debt sources (GRT, SEFAZ, Dívida Ativa) need to be queried
            # NOTE: We use separate instances to avoid concurrency issues with self.browser.close()
            doc_type = "cpf" if len(cpf_proprietario) == 11 else "cnpj"
            detran_tasks = [
                DetranRJScraper().get_cadastro_data(placa),
                DetranRJScraper().get_multas_detalhadas(renavam, cpf_proprietario),
            ]
            if chassi:
                print(f"[*] [Budget] Starting Step 0, 1 & 2 (Nada Consta Apreendido + DETRAN Cadastro & Multas)...")
                detran_tasks.append(DetranRJScraper().get_nada_consta_apreendido_data(
                    placa, chassi, renavam, doc_type, cpf_proprietario
                ))
            else:
                print(f"[*] [Budget] Step 0: Skipping Nada Consta (Chassi nao informado)...")
                print(f"[*] [Budget] Starting Step 1 & 2 (DETRAN RJ Cadastro & Multas)...")
                results["step_6_final_verification"] = {"status": "skipped", "message": "Chassi nao informado."}

            detran_raw = await asyncio.gather(*detran_tasks, return_exceptions=True)

            if chassi:
                results["step_6_final_verification"] = detran_raw[2] if not isinstance(detran_raw[2], Exception) else {"status": "error", "message": str(detran_raw[2])}

            # Step 1: Cadastro (Gravame, Caixa, GNV, Com.Venda)
            results["step_1_detran_cadastro"] = detran_raw[0] if not isinstance(detran_raw[0], Exception) else {"status": "error", "message": str(detran_raw[0])}

            # Step 2: Detailed Multas
            results["step_2_detran_multas"] = detran_raw[1] if not isinstance(detran_raw[1], Exception) else {"status": "error", "message": str(detran_raw[1])}

            # --- STEP 3: SEFAZ (Only if Com. Venda detected) ---
            com_venda = results["step_1_detran_cadastro"].get("data", {}).get("comunicacao_venda") == "SIM" if results["step_1_detran_cadastro"].get("status") == "success" else False
            working_cpf = cpf_proprietario
            
            if com_venda:
                print(f"[*] [Budget] Communication of Sale detected. Discovering actual owner CPF via SEFAZ...")
                comprador = await sefaz_disc.discovery_owner_document(renavam)
                if comprador:
                    results["step_3_owner_discovery"] = {
                        "status": "success",
                        "cpf": comprador["cpf"],
                        "nome": comprador["nome"],
                        "divergente_do_informado": comprador["cpf"] != cpf_proprietario,
                    }
                    working_cpf = comprador["cpf"]
                    print(f"[+] [Budget] New owner CPF discovered: {working_cpf} ({comprador['nome']})")
                else:
                    results["step_3_owner_discovery"] = {"status": "error", "message": "Failed to discover new CPF"}

            # --- STEP 4, 5 & 6-Part-2: Bradesco (GRT & Multas) and SEFAZ (IPVA/DívAtiva) ---
            print(f"[*] [Budget] Starting Step 4, 5 & SEFAZ with CPF: {working_cpf}")
            
            # Smart Skip Check from Nada Consta: each debt source is queried only when its item is "SIM".
            # Without a confirmed Nada Consta (no chassi, or it failed) query everything.
            # Multas are always queried: Nada Consta omits fines still in autuação/recurso.
            nc_res = results["step_6_final_verification"] or {}
            nc_debitos = (nc_res.get("data") or {}).get("debitos") or {}
            nc_confirmado = nc_res.get("status") == "success" and bool(nc_debitos)
            if not nc_confirmado:
                print("[*] [Budget] Nada Consta indisponivel: consultando GRT, SEFAZ e Divida Ativa diretamente.")

            def nc_sim(*keys):
                # Keys come from the DETRAN labels, with or without accents (e.g. DÍVIDA_ATIVA)
                sem_acento = {unicodedata.normalize("NFKD", k).encode("ascii", "ignore").decode(): v for k, v in nc_debitos.items()}
                return not nc_confirmado or any("SIM" in str(sem_acento.get(k, "")).upper() for k in keys)

            has_ipva_grt = nc_sim("TAXA_DE_LICENCIAMENTO_ANUAL", "TAXA_DE_EMISSAO_DE_CRLV", "LICENCIAMENTO_ATRASADO")
            has_divida = nc_sim("IPVA")

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

            # Step 6 Part 2: SEFAZ (IPVA)
            if has_divida:
                bradesco_tasks.append(SefazRJScraper().get_vehicle_data(renavam))
                task_mapping.append("step_6_sefaz_ipva")
            else:
                results["step_6_sefaz_ipva"] = {"status": "success", "message": "Sem débitos na SEFAZ (Nada Consta)"}

            # Step 6B: Dívida Ativa RJ (se detectado no nada consta)
            has_divida_ativa = nc_sim("DIVIDA_ATIVA")

            if has_divida_ativa:
                bradesco_tasks.append(DividaAtivaRJScraper().get_divida_ativa(renavam))
                task_mapping.append("step_6b_divida_ativa")
            else:
                results["step_6b_divida_ativa"] = {"status": "success", "message": "Sem dívida ativa (Nada Consta)"}
            
            # Run all in parallel
            parallel_results = await asyncio.gather(*bradesco_tasks, return_exceptions=True)
            
            for i, task_name in enumerate(task_mapping):
                res = parallel_results[i]
                results[task_name] = res if not isinstance(res, Exception) else {"status": "error", "message": str(res)}

            # A debt source can't silently fail: retry each failed one once more, sequentially
            retry_calls = {
                "step_4_bradesco_grt": lambda: BradescoScraper().get_grt_debts(renavam, working_cpf),
                "step_5_bradesco_multas_optimized": lambda: BradescoScraper().get_fines_data(renavam, working_cpf),
                "step_6_sefaz_ipva": lambda: SefazRJScraper().get_vehicle_data(renavam),
                "step_6b_divida_ativa": lambda: DividaAtivaRJScraper().get_divida_ativa(renavam),
            }
            for task_name in task_mapping:
                if (results[task_name] or {}).get("status") != "error":
                    continue
                print(f"[!] [Budget] {task_name} falhou, repetindo consulta...")
                try:
                    results[task_name] = await retry_calls[task_name]()
                except Exception as e:
                    results[task_name] = {"status": "error", "message": str(e)}

            grm_res = results.get("step_5_bradesco_multas_optimized", {"status": "error"})
            bradesco_ok = grm_res.get("status") == "success"
            fines_detran = results["step_2_detran_multas"].get("data", []) if results["step_2_detran_multas"].get("status") == "success" else []

            def detran_auto(df):
                # DETRAN header "Auto de Infração" is slugified without accents -> "auto_de_infrao"
                return df.get("auto_de_infrao") or df.get("auto_de_infração") or df.get("auto_infracao")

            def status_flags(df):
                tipo_status = (df.get("tipo_status") or "").upper()
                renainf = (df.get("auto_de_renainf") or "").strip()
                return {
                    "tipo_status_detran": df.get("tipo_status"),
                    "is_transitado": "SIM" if "TRANSITADO" in tipo_status else "NÃO",
                    "is_penalidade": "SIM" if tipo_status.startswith("MULTA") or "PENALIDADE" in tipo_status else "NÃO",
                    "is_renainf": "SIM" if "RENAINF" in tipo_status or renainf not in ("", "-") else "NÃO",
                }

            fines = grm_res.get("detalhes", []) if bradesco_ok else []
            for b_fine in fines:
                b_fine["fonte"] = "Bradesco"
                match = next((df for df in fines_detran if b_fine.get("auto_infracao") and detran_auto(df) == b_fine["auto_infracao"]), None)
                if match:
                    b_fine.update(status_flags(match))
                else:
                    b_fine["is_transitado"] = b_fine["is_penalidade"] = b_fine["is_renainf"] = "N/A"

            # Fines only on DETRAN (e.g. RENAINF penalties not payable via Bradesco GRM) still count for transfer
            no_bradesco = {f.get("auto_infracao") for f in fines}
            fines_so_detran = [{
                "auto_infracao": detran_auto(df),
                "placa": df.get("placa_relacionada"),
                "data_infracao": df.get("data_da_infrao"),
                "valor": df.get("valor_original"),
                "fonte": "DETRAN (valor original, sem juros)",
                **status_flags(df),
            } for df in fines_detran
                if detran_auto(df) not in no_bradesco and (df.get("status_de_pagamento") or "").upper() != "PAGO"]
            fines = fines + fines_so_detran
            results["multas_consolidadas"] = fines

            # --- STEP 7: RELATÓRIO PARA TOMADA DE DECISÃO ---
            print(f"[*] [Budget] Generating Decision Report for {placa}...")

            cadastro_data = results.get("step_1_detran_cadastro", {}).get("data", {})
            nada_consta_data = results.get("step_6_final_verification", {}).get("data", {})

            results["step_7_relatorio_decisao"] = BusinessRuleAnalyzer.generate_business_analysis(
                cadastro=cadastro_data,
                nada_consta=nada_consta_data,
                fines=fines,
                divida_ativa=results.get("step_6b_divida_ativa"),
                owner_discovery=results.get("step_3_owner_discovery"),
            )
            avisos = results["step_7_relatorio_decisao"]["avisos"]
            if com_venda and (results.get("step_3_owner_discovery") or {}).get("status") != "success":
                avisos.append("Comunicação de venda: CPF do comprador não obtido na SEFAZ; GRT e multas Bradesco "
                              "foram consultados com o CPF informado e podem estar incompletos")
            if not bradesco_ok and fines:
                avisos.append("Consulta de multas no Bradesco falhou: valores das multas são os originais do DETRAN, sem juros")
            for key, nome in (("step_4_bradesco_grt", "GRT/Licenciamento (Bradesco)"), ("step_6_sefaz_ipva", "IPVA (SEFAZ)"),
                              ("step_6b_divida_ativa", "Dívida Ativa (PGE-RJ)")):
                if (results.get(key) or {}).get("status") == "error":
                    avisos.append(f"Consulta {nome} falhou: total do orçamento pode estar incompleto")

            # --- STEP 8: CALCULAR RESUMO DE DÉBITOS ---
            def parse_money(val_str):
                if not val_str or not isinstance(val_str, str): return 0.0
                try:
                    # Clear R$, dots, and change comma to dot
                    clean = val_str.replace("R$", "").replace(".", "").replace(",", ".").replace(" ", "").strip()
                    return float(clean)
                except: return 0.0
            
            total_grt = parse_money(results.get("step_4_bradesco_grt", {}).get("total_somado", "0,00"))
            total_grm = sum(parse_money(f.get("valor")) for f in fines)
            
            # Sefaz Summation
            total_sefaz = 0.0
            sefaz_data = results.get("step_6_sefaz_ipva", {}).get("data", {})
            if isinstance(sefaz_data, dict):
                sefaz_ipva_list = sefaz_data.get("debitos_ipva", [])
                for s_debt in sefaz_ipva_list:
                    total_sefaz += parse_money(s_debt.get("total_a_pagar", "0,00"))
            
            da_res = results.get("step_6b_divida_ativa") or {}
            total_divida_ativa = parse_money(da_res.get("total_divida", "0,00")) if da_res.get("status") == "success" else 0.0

            valor_total_debitos = total_grt + total_grm + total_sefaz + total_divida_ativa

            results["resumo_orcamento"] = {
                "total_grt_ipva": f"R$ {total_grt:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "total_multas": f"R$ {total_grm:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "total_sefaz_ipva": f"R$ {total_sefaz:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                "total_divida_ativa": f"R$ {total_divida_ativa:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
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
