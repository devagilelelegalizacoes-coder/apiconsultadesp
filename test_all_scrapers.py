import asyncio
import sys
import os
from dotenv import load_dotenv

# Ensure Windows Proactor event loop is set for async playwright
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.scrapers.detran_rj import DetranRJScraper
from app.scrapers.sefaz_rj import SefazRJScraper
from app.scrapers.sefaz import SefazDiscoveryScraper
from app.scrapers.bradesco import BradescoScraper
from app.scrapers.dataf5 import DataF5Scraper, DataF5Gravame

load_dotenv()

async def run_test_case(name: str, coro):
    print("\n" + "="*80)
    print(f"[*] INICIANDO TESTE: {name}")
    print("="*80)
    try:
        result = await coro
        print(f"[+] RESULTADO {name}:")
        import pprint
        pprint.pprint(result, indent=2, width=120)
        return True
    except Exception as e:
        print(f"[!] FALHA CRÍTICA NO TESTE {name}: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    print("="*80)
    print("      FERRAMENTA DE TESTES UNIFICADA - CONSULTAS VEICULARES API 2.0      ")
    print("="*80)
    
    # Check credentials
    dataf5_user = os.getenv("DATAF5_USER")
    captcha_key = os.getenv("CAPTCHA_API_KEY")
    print(f"[*] Configurações detectadas:")
    print(f"    - DATAF5_USER: {'Configurado' if dataf5_user else 'NÃO Configurado'}")
    print(f"    - CAPTCHA_API_KEY: {'Configurado' if captcha_key else 'NÃO Configurado'}")
    print(f"    - HEADLESS: {os.getenv('HEADLESS', 'false')}")
    
    # Prompt for input or use defaults
    print("\nEscolha uma opção:")
    print("1) Usar valores de teste padrão (Placa: LKZ2945, Renavam: 123456789, CPF: 12345678909, Chassi: 9BWCA49U2EP000000)")
    print("2) Inserir valores manualmente")
    
    choice = input("Opção (1 ou 2): ").strip()
    
    if choice == "2":
        placa = input("Digite a Placa (ex: LKZ2945): ").strip().upper()
        renavam = input("Digite o Renavam (ex: 123456789): ").strip()
        cpf = input("Digite o CPF/CNPJ (apenas números): ").strip()
        chassi = input("Digite o Chassi (ex: 9BWCA49U2EP000000): ").strip().upper()
    else:
        placa = "LKZ2945"
        renavam = "123456789"
        cpf = "12345678909"
        chassi = "9BWCA49U2EP000000"
        
    print(f"\n[*] Iniciando bateria de testes com:")
    print(f"    - Placa:  {placa}")
    print(f"    - Renavam: {renavam}")
    print(f"    - CPF/CNPJ: {cpf}")
    print(f"    - Chassi: {chassi}")
    
    # Menu for which scraper to run
    print("\nQuais testes deseja executar?")
    print("1) Todos os testes sequencialmente")
    print("2) Apenas DETRAN-RJ (Cadastro e Multas)")
    print("3) Apenas SEFAZ-RJ (IPVA e Descoberta de CPF)")
    print("4) Apenas Bradesco (GRT e Multas)")
    print("5) Apenas DataF5 (Completo e Gravame)")
    
    test_choice = input("Opção (1-5): ").strip()
    
    tests_to_run = []
    
    # DETRAN Tests
    detran_rj = DetranRJScraper()
    detran_cadastro_coro = detran_rj.get_cadastro_data(placa)
    detran_multas_coro = detran_rj.get_multas_detalhadas(renavam, cpf)
    detran_apreendido_coro = detran_rj.get_nada_consta_apreendido_data(
        placa=placa, chassi=chassi, renavam=renavam,
        doc_type="cpf" if len(cpf) <= 11 else "cnpj", doc_num=cpf
    )
    
    # SEFAZ Tests
    sefaz_disc = SefazDiscoveryScraper()
    sefaz_disc_coro = sefaz_disc.discovery_owner_document(renavam)
    sefaz_rj = SefazRJScraper()
    sefaz_ipva_coro = sefaz_rj.get_vehicle_data(renavam)
    
    # Bradesco Tests
    bradesco = BradescoScraper()
    bradesco_grt_coro = bradesco.get_grt_debts(renavam, cpf)
    bradesco_grm_coro = bradesco.get_fines_data(renavam, cpf)
    
    # DataF5 Tests
    dataf5 = DataF5Scraper()
    dataf5_placa_coro = dataf5.get_vehicle_data(placa)
    dataf5_gravame = DataF5Gravame()
    dataf5_gravame_coro = dataf5_gravame.get_gravame_data(chassi)

    if test_choice == "2":
        tests_to_run = [
            ("DETRAN-RJ Cadastro (Placa)", detran_cadastro_coro),
            ("DETRAN-RJ Multas Detalhadas (Renavam + CPF)", detran_multas_coro),
            ("DETRAN-RJ Nada Consta Apreendido (Placa + Chassi + Renavam + CPF)", detran_apreendido_coro)
        ]
    elif test_choice == "3":
        tests_to_run = [
            ("SEFAZ-RJ Descoberta de CPF (Renavam)", sefaz_disc_coro),
            ("SEFAZ-RJ IPVA Débitos (Renavam)", sefaz_ipva_coro)
        ]
    elif test_choice == "4":
        tests_to_run = [
            ("Bradesco GRT Débitos (Renavam + CPF)", bradesco_grt_coro),
            ("Bradesco GRM Multas (Renavam + CPF)", bradesco_grm_coro)
        ]
    elif test_choice == "5":
        tests_to_run = [
            ("DataF5 Consulta Placa Completa", dataf5_placa_coro),
            ("DataF5 Consulta Gravame (Chassi)", dataf5_gravame_coro)
        ]
    else:
        tests_to_run = [
            ("DETRAN-RJ Cadastro (Placa)", detran_cadastro_coro),
            ("DETRAN-RJ Multas Detalhadas (Renavam + CPF)", detran_multas_coro),
            ("DETRAN-RJ Nada Consta Apreendido (Placa + Chassi + Renavam + CPF)", detran_apreendido_coro),
            ("SEFAZ-RJ Descoberta de CPF (Renavam)", sefaz_disc_coro),
            ("SEFAZ-RJ IPVA Débitos (Renavam)", sefaz_ipva_coro),
            ("Bradesco GRT Débitos (Renavam + CPF)", bradesco_grt_coro),
            ("Bradesco GRM Multas (Renavam + CPF)", bradesco_grm_coro),
            ("DataF5 Consulta Placa Completa", dataf5_placa_coro),
            ("DataF5 Consulta Gravame (Chassi)", dataf5_gravame_coro)
        ]

    success_count = 0
    for name, coro in tests_to_run:
        success = await run_test_case(name, coro)
        if success:
            success_count += 1
            
    print("\n" + "="*80)
    print(f"[*] RESUMO DA BATERIA DE TESTES: {success_count}/{len(tests_to_run)} testes concluídos.")
    print("="*80)

if __name__ == "__main__":
    asyncio.run(main())
