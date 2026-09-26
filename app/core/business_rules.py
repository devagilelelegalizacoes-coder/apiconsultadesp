from typing import Dict, Any, List
from datetime import datetime

class BusinessRuleAnalyzer:
    """
    Aplica regras de negócio para análise de veículos conforme especificado
    pelo despachante.
    """

    @staticmethod
    def check_gnv_inmetro(cadastro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verifica se veículo com GNV tem vistoria INMETRO no ano vigente."""
        gnv_status = cadastro_data.get("combustivel", "")
        tem_gnv = "GNV" in str(gnv_status).upper() or "GAS NATURAL" in str(gnv_status).upper()

        if not tem_gnv:
            return {"tem_gnv": False, "inmetro_regular": False, "impedimento": False}

        ano_vigente = str(datetime.now().year)
        obs = str(cadastro_data.get("observacoes", "")).upper()
        inmetro_regular = ano_vigente in obs and ("CSV" in obs or "INMETRO" in obs)

        impedimento = tem_gnv and not inmetro_regular

        return {
            "tem_gnv": True,
            "inmetro_regular": inmetro_regular,
            "impedimento": impedimento,
            "motivo": "Necessário fazer vistoria INMETRO no ano vigente" if impedimento else ""
        }

    @staticmethod
    def check_cilindrada_ano(cadastro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verifica se cilindrada zerada + ano < 2018 requer vistoria."""
        cilindrada = str(cadastro_data.get("cilindrada", "")).strip()
        ano_fabricacao = cadastro_data.get("ano_fabricacao", "")

        try:
            ano_int = int(ano_fabricacao) if ano_fabricacao else 0
        except:
            ano_int = 0

        cilindrada_zerada = cilindrada in ["0", "00", "000", "0000", ""]
        ano_baixo = ano_int < 2018 and ano_int > 0

        impedimento = cilindrada_zerada and ano_baixo

        return {
            "cilindrada_zerada": cilindrada_zerada,
            "ano_baixo": ano_baixo,
            "impedimento": impedimento,
            "motivo": "Necessário fazer serviço com vistoria (acerto dados/transferência/2via) ou abrir processo administrativo" if impedimento else ""
        }

    @staticmethod
    def check_comunicacao_venda(cadastro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verifica comunicação/intenção de venda."""
        com_venda = cadastro_data.get("comunicacao_venda", "NÃO")
        tem_com_venda = "SIM" in str(com_venda).upper()

        return {
            "tem_comunicacao_venda": tem_com_venda,
            "acao": "Fazer transferência para novo CPF/nome da comunicação ou cancelar" if tem_com_venda else "Ok"
        }

    @staticmethod
    def check_gravame(cadastro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verifica se há gravame/financiamento."""
        tem_gravame = cadastro_data.get("has_gravame", "NÃO")
        tem_financiamento = "SIM" in str(tem_gravame).upper()

        return {
            "tem_gravame": tem_financiamento,
            "acao": "Necessário fazer inclusão de financiamento no DETRAN c/ ou s/ transferência" if tem_financiamento else "Ok"
        }

    @staticmethod
    def check_divida_ativa(nada_consta_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verifica se há dívida ativa no nada consta."""
        debitos = nada_consta_data.get("debitos", {})
        tem_divida = "SIM" in str(debitos.get("DIVIDA_ATIVA", "")).upper() or \
                     "SIM" in str(debitos.get("DÍVIDA_ATIVA", "")).upper()

        return {
            "tem_divida_ativa": tem_divida,
            "consulta_necessaria": "consultadividaativa.rj.gov.br" if tem_divida else None,
            "motivo": "Verificar dívida ativa no site da Receita" if tem_divida else ""
        }

    @staticmethod
    def determine_fines_to_pay(fines_list: List[Dict[str, Any]], service_type: str = "default") -> Dict[str, Any]:
        """
        Determina quais multas devem ser pagas conforme tipo de serviço.

        service_type: "licenciamento" | "transferencia" | "vistoria" | "default"
        """
        if not fines_list:
            return {"total_obrigatorio": 0.0, "detalhes": [], "observacao": "Sem multas a pagar"}

        fines_to_pay = []

        if service_type == "licenciamento":
            fines_to_pay = [f for f in fines_list if str(f.get("is_transitado", "")).upper() == "SIM"]
            observacao = "Apenas multas transitadas em julgado"

        elif service_type in ["transferencia", "vistoria"]:
            fines_to_pay = [f for f in fines_list
                           if str(f.get("is_transitado", "")).upper() == "SIM" or
                              "PENALIDADE" in str(f.get("tipo", "")).upper() or
                              str(f.get("is_renainf", "")).upper() == "SIM"]
            observacao = "Multas transitadas, penalidades e RENAINF"

        else:  # default - todas as multas
            fines_to_pay = fines_list
            observacao = "Todas as multas"

        return {
            "tipo_servico": service_type,
            "total_multas": len(fines_to_pay),
            "detalhes": fines_to_pay,
            "observacao": observacao
        }

    @staticmethod
    def check_inclusao_impedimento(cadastro_data: Dict[str, Any]) -> Dict[str, Any]:
        """Verifica se há inclusão impedindo licenciamento."""
        restricoes = str(cadastro_data.get("restricoes", "")).upper()
        tem_inclusao = "INCLUSÃO" in restricoes or "INCLUSAO" in restricoes

        return {
            "tem_inclusao": tem_inclusao,
            "impedimento_licenciamento": tem_inclusao,
            "motivo": "Inclusão impede licenciamento" if tem_inclusao else ""
        }

    @staticmethod
    def generate_business_analysis(
        cadastro_data: Dict[str, Any],
        nada_consta_data: Dict[str, Any],
        fines_list: List[Dict[str, Any]],
        service_type: str = "default"
    ) -> Dict[str, Any]:
        """
        Gera análise completa aplicando todas as regras de negócio.
        """
        analysis = {
            "timestamp": datetime.now().isoformat(),
            "impedimentos": [],
            "avisos": [],
            "acoes_necessarias": []
        }

        # Verificação 1: GNV + INMETRO
        gnv_check = BusinessRuleAnalyzer.check_gnv_inmetro(cadastro_data)
        if gnv_check["impedimento"]:
            analysis["impedimentos"].append({
                "categoria": "GNV",
                "motivo": gnv_check["motivo"]
            })
        elif gnv_check["tem_gnv"]:
            analysis["avisos"].append("Veículo com GNV - vistoria INMETRO OK")

        # Verificação 2: Cilindrada + Ano
        cilindrada_check = BusinessRuleAnalyzer.check_cilindrada_ano(cadastro_data)
        if cilindrada_check["impedimento"]:
            analysis["impedimentos"].append({
                "categoria": "Cilindrada/Ano",
                "motivo": cilindrada_check["motivo"]
            })

        # Verificação 3: Comunicação de Venda
        com_venda_check = BusinessRuleAnalyzer.check_comunicacao_venda(cadastro_data)
        if com_venda_check["tem_comunicacao_venda"]:
            analysis["acoes_necessarias"].append({
                "tipo": "Comunicação de Venda",
                "acao": "Fazer transferência para novo CPF/nome ou cancelar comunicação"
            })

        # Verificação 4: Gravame
        gravame_check = BusinessRuleAnalyzer.check_gravame(cadastro_data)
        if gravame_check["tem_gravame"]:
            analysis["acoes_necessarias"].append({
                "tipo": "Financiamento",
                "acao": gravame_check["acao"]
            })

        # Verificação 5: Dívida Ativa
        divida_check = BusinessRuleAnalyzer.check_divida_ativa(nada_consta_data)
        if divida_check["tem_divida_ativa"]:
            analysis["avisos"].append(f"Dívida Ativa detectada - Consultar: {divida_check['consulta_necessaria']}")

        # Verificação 6: Inclusão
        inclusao_check = BusinessRuleAnalyzer.check_inclusao_impedimento(cadastro_data)
        if inclusao_check["impedimento_licenciamento"]:
            analysis["impedimentos"].append({
                "categoria": "Inclusão",
                "motivo": "Inclusão impede licenciamento"
            })

        # Determinação de multas conforme serviço
        fines_analysis = BusinessRuleAnalyzer.determine_fines_to_pay(fines_list, service_type)
        analysis["multas_analise"] = fines_analysis

        # Status final
        analysis["pode_regularizar"] = len(analysis["impedimentos"]) == 0
        analysis["status"] = "LIBERADO" if analysis["pode_regularizar"] else "IMPEDIDO"

        return analysis
