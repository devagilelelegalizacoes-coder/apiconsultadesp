from typing import Dict, Any, List
from pydantic import BaseModel, Field

class Normalizer:
    @staticmethod
    def normalize_detran(data: Dict[str, Any]) -> Dict[str, Any]:
        raw = data.get("data", {})
        return {
            "origem": "DETRAN-RJ",
            "multas": raw.get("multas_bruto", "Nada consta")
        }

    @staticmethod
    def normalize_sefaz(data: Dict[str, Any]) -> Dict[str, Any]:
        raw = data.get("data", {})
        detalhes = raw.get("detalhes", {})
        return {
            "origem": "SEFAZ-RJ",
            "detalhes_veiculo": detalhes,
            "debitos": raw.get("debitos_ipva", []),
            "has_debts": len(raw.get("debitos_ipva", [])) > 0
        }

    @staticmethod
    def normalize_bradesco(data: Dict[str, Any]) -> Dict[str, Any]:
        raw = data.get("data", {})
        return {
            "origem": "Bradesco",
            "proprietario": raw.get("proprietario"),
            "exercicio": raw.get("exercicio"),
            "valor_grt": raw.get("valor_grt")
        }

    @staticmethod
    def normalize_dataf5(data: Dict[str, Any]) -> Dict[str, Any]:
        raw = data.get("data", {})
        return {
            "origem": "DataF5",
            "detalhes": raw
        }

    @staticmethod
    def merge_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        merged_consolidado: Dict[str, Any] = {
            "proprietario": "",
            "placa": "",
            "municipio": "",
            "marca_modelo": "",
            "multas_ativas": False,
            "debitos_ipva": False
        }
        
        merged_detalhes: List[Dict[str, Any]] = []
        renavam = results[0].get("renavam") if results else ""
        
        for res in results:
            if not isinstance(res, dict) or res.get("status") not in ["success", "partial_success"]:
                continue
                
            source = res.get("source")
            data = res.get("data", {})
            
            if source == "DETRAN-RJ":
                # Handle nested structure: {"cadastro": {...}, "multas": {...}}
                cadastro = data.get("cadastro", {}).get("data", {})
                if cadastro:
                    merged_consolidado["proprietario"] = cadastro.get("nome") or merged_consolidado["proprietario"]
                    merged_consolidado["placa"] = cadastro.get("placa") or ""
                    merged_consolidado["marca_modelo"] = cadastro.get("marca") or ""
                    merged_consolidado["municipio"] = cadastro.get("local") or ""
                
                multas = data.get("multas", {})
                if multas.get("status") == "success":
                    if "Nada consta" not in str(multas.get("multas_bruto")):
                        merged_consolidado["multas_ativas"] = True
                
                merged_detalhes.append(Normalizer.normalize_detran(res))

            elif source == "SEFAZ-RJ":
                norm_sefaz = Normalizer.normalize_sefaz(res)
                if norm_sefaz.get("has_debts"):
                    merged_consolidado["debitos_ipva"] = True
                
                # Enrich consolidated data
                detalhes = norm_sefaz.get("detalhes_veiculo", {})
                merged_consolidado["proprietario"] = merged_consolidado["proprietario"] or detalhes.get("contribuinte")
                merged_consolidado["placa"] = merged_consolidado["placa"] or detalhes.get("placa")
                merged_consolidado["marca_modelo"] = merged_consolidado["marca_modelo"] or f"{detalhes.get('marca', '')} {detalhes.get('modelo', '')}".strip()
                merged_consolidado["municipio"] = merged_consolidado["municipio"] or detalhes.get("municipio")
                
                merged_detalhes.append(norm_sefaz)

            elif source == "Bradesco":
                norm_bradesco = Normalizer.normalize_bradesco(res)
                merged_consolidado["proprietario"] = norm_bradesco.get("proprietario") or merged_consolidado["proprietario"]
                merged_detalhes.append(norm_bradesco)
                
            elif source == "DataF5":
                norm_dataf5 = Normalizer.normalize_dataf5(res)
                # DataF5 is usually very rich, use it to fill gaps
                detalhes = norm_dataf5.get("detalhes", {})
                merged_consolidado["proprietario"] = merged_consolidado["proprietario"] or detalhes.get("Proprietário")
                merged_consolidado["placa"] = merged_consolidado["placa"] or detalhes.get("Placa")
                merged_consolidado["marca_modelo"] = merged_consolidado["marca_modelo"] or detalhes.get("Marca/Modelo")
                merged_consolidado["municipio"] = merged_consolidado["municipio"] or detalhes.get("Município")
                merged_detalhes.append(norm_dataf5)
                
        return {
            "renavam": renavam,
            "consolidado": merged_consolidado,
            "detalhes": merged_detalhes
        }
