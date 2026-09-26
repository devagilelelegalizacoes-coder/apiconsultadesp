import unicodedata
from typing import Dict, Any, List
from datetime import datetime

MSG_IMPEDIDO = "Existe impedimento para regularização do veículo. Solicite ao despachante a análise e o orçamento."
MSG_LIBERADO = "Veículo apto para regularização."


def _norm(s: Any) -> str:
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().upper()


def _int(s: Any) -> int:
    digits = "".join(ch for ch in str(s or "") if ch.isdigit())
    return int(digits) if digits else 0


def _sim(v: Any) -> bool:
    return "SIM" in _norm(v)


class BusinessRuleAnalyzer:
    """Regras de análise para serviços no DETRAN-RJ definidas pelo despachante."""

    @staticmethod
    def check_gnv_inmetro(cadastro: Dict[str, Any]) -> Dict[str, Any]:
        combustivel = _norm(cadastro.get("combustivel"))
        tem_gnv = "GNV" in combustivel or "GAS NATURAL" in combustivel
        if not tem_gnv:
            return {"tem_gnv": False, "impedimento": False}
        obs = _norm(cadastro.get("observacoes"))
        regular = str(datetime.now().year) in obs and ("CSV" in obs or "INMETRO" in obs)
        return {
            "tem_gnv": True,
            "inmetro_regular": regular,
            "impedimento": not regular,
            "motivo": "" if regular else "GNV sem vistoria do INMETRO no ano vigente",
        }

    @staticmethod
    def check_cilindrada_ano(cadastro: Dict[str, Any]) -> Dict[str, Any]:
        ano_lic = _int(cadastro.get("licenciamento"))
        if "cilindrada" not in cadastro:
            return {"verificavel": False, "impedimento": False, "ano_licenciamento": ano_lic,
                    "motivo": "Cilindrada não retornada pela consulta de cadastro"}
        zerada = _int(cadastro.get("cilindrada")) == 0
        impedimento = zerada and 0 < ano_lic < 2018
        return {
            "verificavel": True,
            "cilindrada_zerada": zerada,
            "ano_licenciamento": ano_lic,
            "impedimento": impedimento,
            "motivo": "Cilindrada zerada com licenciamento anterior a 2018: exige serviço com vistoria "
                      "(acerto de dados, transferência, 2ª via) ou processo administrativo" if impedimento else "",
        }

    @staticmethod
    def check_inclusao(cadastro: Dict[str, Any]) -> Dict[str, Any]:
        texto = _norm(f"{cadastro.get('mensagem_detran', '')} {cadastro.get('observacoes', '')}")
        tem = "INCLUS" in texto
        return {"tem_inclusao": tem, "impedimento_licenciamento": tem,
                "motivo": "Inclusão pendente: licenciamento não liberado" if tem else ""}

    @staticmethod
    def check_pendencia_detran(cadastro: Dict[str, Any]) -> Dict[str, Any]:
        """DETRAN message ordering a service (e.g. 'PROVIDENCIAR O SERVIÇO DE BAIXA DE GRAVAME')."""
        msg = cadastro.get("mensagem_detran") or ""
        tem = "PROVIDENCIAR" in _norm(msg)
        return {"tem_pendencia": tem, "motivo": f"Pendência no cadastro DETRAN: {msg}" if tem else ""}

    @staticmethod
    def classify_fines(fines: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Licenciamento: só transitadas em julgado. Transferência/vistoria: transitadas + penalidade + RENAINF."""
        def valor(f):
            try:
                return float(str(f.get("valor", "0")).replace(".", "").replace(",", "."))
            except ValueError:
                return 0.0

        def fmt(v):
            return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        lic = [f for f in fines if f.get("is_transitado") == "SIM"]
        transf = [f for f in fines if "SIM" in (f.get("is_transitado"), f.get("is_penalidade"), f.get("is_renainf"))]
        return {
            "licenciamento": {"quantidade": len(lic), "total": fmt(sum(map(valor, lic))), "autos": [f.get("auto_infracao") for f in lic]},
            "transferencia_ou_vistoria": {"quantidade": len(transf), "total": fmt(sum(map(valor, transf))), "autos": [f.get("auto_infracao") for f in transf]},
            "sem_classificacao": [f.get("auto_infracao") for f in fines if f.get("is_transitado") == "N/A"],
        }

    @staticmethod
    def generate_business_analysis(
        cadastro: Dict[str, Any],
        nada_consta: Dict[str, Any],
        fines: List[Dict[str, Any]],
        divida_ativa: Dict[str, Any] | None = None,
        owner_discovery: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        impedimentos: List[Dict[str, str]] = []
        acoes: List[Dict[str, str]] = []
        avisos: List[str] = []

        gnv = BusinessRuleAnalyzer.check_gnv_inmetro(cadastro)
        if gnv["impedimento"]:
            impedimentos.append({"categoria": "GNV", "motivo": gnv["motivo"]})

        cil = BusinessRuleAnalyzer.check_cilindrada_ano(cadastro)
        if cil["impedimento"]:
            impedimentos.append({"categoria": "Cilindrada/Ano", "motivo": cil["motivo"]})
        elif not cil["verificavel"]:
            avisos.append(cil["motivo"])

        inc = BusinessRuleAnalyzer.check_inclusao(cadastro)
        if inc["impedimento_licenciamento"]:
            impedimentos.append({"categoria": "Inclusão", "motivo": inc["motivo"]})

        if _sim(cadastro.get("comunicacao_venda")):
            acao = {"tipo": "Comunicação de Venda",
                    "acao": "Transferir para o CPF/nome da comunicação de venda ou cancelar a comunicação"}
            if owner_discovery and owner_discovery.get("status") == "success":
                acao["cpf_comunicacao_venda"] = owner_discovery.get("cpf")
            acoes.append(acao)

        if _sim(cadastro.get("has_gravame")):
            acoes.append({"tipo": "Financiamento",
                          "acao": "Necessário fazer a inclusão/baixa de financiamento no DETRAN, com ou sem transferência"})

        pend = BusinessRuleAnalyzer.check_pendencia_detran(cadastro)
        if pend["tem_pendencia"]:
            impedimentos.append({"categoria": "Pendência DETRAN", "motivo": pend["motivo"]})
        elif cadastro.get("mensagem_detran"):
            avisos.append(f"DETRAN: {cadastro['mensagem_detran']}")

        debitos_nc = nada_consta.get("debitos", {}) if isinstance(nada_consta, dict) else {}
        if _sim(debitos_nc.get("DIVIDA_ATIVA")) or _sim(debitos_nc.get("DÍVIDA_ATIVA")):
            da = divida_ativa or {}
            if da.get("status") == "success" and da.get("tem_divida"):
                acoes.append({"tipo": "Dívida Ativa", "acao": f"Quitar débitos inscritos em Dívida Ativa ({da.get('total_divida')})"})
            elif da.get("status") != "success":
                avisos.append("Nada consta aponta Dívida Ativa, mas a consulta à PGE-RJ falhou: verificar manualmente")

        multas = BusinessRuleAnalyzer.classify_fines(fines)
        if multas["sem_classificacao"]:
            avisos.append(f"{len(multas['sem_classificacao'])} multa(s) do Bradesco sem correspondência no DETRAN")

        pode = not impedimentos
        return {
            "timestamp": datetime.now().isoformat(),
            "status": "LIBERADO" if pode else "IMPEDIDO",
            "pode_regularizar": pode,
            "impedimentos": impedimentos,
            "acoes_necessarias": acoes,
            "avisos": avisos,
            "multas_por_servico": multas,
            "mensagem_ao_cliente": MSG_LIBERADO if pode else MSG_IMPEDIDO,
        }
