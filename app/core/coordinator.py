import asyncio
from typing import List, Dict, Any
from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.bradesco import BradescoScraper
from app.scrapers.dataf5 import DataF5Scraper
from app.core.normalizer import Normalizer
from app.infrastructure.supabase_db import cache
from app.security.audit import log_audit_event

class QueryCoordinator:
    async def run_parallel_queries(self, renavam: str, cpf: str, placa: str | None = None, user: str = "system") -> Dict[str, Any]:
        """Runs queries to DETRAN, SEFAZ, and Bradesco, handling dependencies."""
        
        # Check cache
        cache_key = f"vehicle:{renavam}:{cpf}:{placa or ''}"
        cached_result = await cache.get(cache_key)
        if cached_result:
            return cached_result

        # Step 1: Initialize scrapers
        detran = DetranRJScraper()
        sefaz = SefazRJScraper()
        bradesco = BradescoScraper()
        dataf5 = DataF5Scraper()

        # If we have placa, we can run everything in parallel
        if placa:
            print(f"[*] Starting parallel queries for Renavam {renavam} with Placa {placa}...")
            results = await asyncio.gather(
                detran.get_vehicle_data(renavam, cpf, placa),
                sefaz.get_vehicle_data(renavam),
                bradesco.get_vehicle_data(renavam, cpf),
                dataf5.get_vehicle_data(placa),
                return_exceptions=True
            )
        else:
            print(f"[*] Placa not provided. Running Bradesco/Sefaz first to discover Placa...")
            # If no placa, run Bradesco and Sefaz first to try and get it
            results_init = await asyncio.gather(
                sefaz.get_vehicle_data(renavam),
                bradesco.get_vehicle_data(renavam, cpf),
                return_exceptions=True
            )
            
            # Extract placa from Bradesco result if possible
            bradesco_res = results_init[1]
            extracted_placa = None
            if not isinstance(bradesco_res, Exception) and isinstance(bradesco_res, dict):
                if bradesco_res.get("status") == "success":
                    extracted_placa = bradesco_res.get("data", {}).get("placa")
                    print(f"[+] Placa discovered via Bradesco: {extracted_placa}")
                else:
                    print(f"[-] Bradesco failed to provide Placa: {bradesco_res.get('message')}")
            
            # Now run Detran with the (possibly) found placa
            print(f"[*] Running Detran query with Placa: {extracted_placa}")
            detran_res = await detran.get_vehicle_data(renavam, cpf, extracted_placa)
            
            # Run DataF5 with discovered placa if available
            dataf5_res = await dataf5.get_vehicle_data(extracted_placa) if extracted_placa else {"source": "DataF5", "status": "error", "message": "Placa not discovered"}
            
            # Combine all results (maintain order: Detran, Sefaz, Bradesco, DataF5)
            results = [detran_res, results_init[0], results_init[1], dataf5_res]

        # Process and handle exceptions
        processed_results = []
        sources = ["DETRAN-RJ", "SEFAZ-RJ", "Bradesco", "DataF5"]
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                processed_results.append({"source": sources[i], "status": "error", "message": str(res)})
            else:
                processed_results.append(res)

        merged_data = Normalizer.merge_results(processed_results)
        await cache.set(cache_key, merged_data, expire=3600)
        return merged_data
