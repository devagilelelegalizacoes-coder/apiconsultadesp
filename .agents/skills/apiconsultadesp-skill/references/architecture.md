# Arquitetura do Sistema de Consultas Veiculistas

Este documento descreve a infraestrutura e os padrões arquiteturais do projeto **NovaApideConsultas** para guiar futuros desenvolvimentos e manutenções.

---

## 1. Fluxo Geral de Requisições

A API é construída com **FastAPI** e suporta dois modos de execução:
1. **Síncrono**: O endpoint aguarda a execução direta dos scrapers e retorna o resultado imediatamente.
2. **Assíncrono (Fila de Jobs)**: O endpoint registra um job no Supabase e retorna um `job_id`. O **Worker** orquestra o processamento em background, atualiza o status do job e salva os resultados.

---

## 2. Banco de Dados e Cache Encriptado

Para proteger dados veiculares e dados pessoais sensíveis (como CPF/CNPJ de proprietários), o sistema utiliza uma camada de criptografia simétrica (AES-256) antes de salvar no banco de dados.

*   **Arquivo de Conexão**: `app/infrastructure/supabase_db.py`
*   **Chave de Criptografia**: Configurada em `.env` via `ENCRYPTION_KEY`.
*   **Tabela de Cache**: `vehicle_cache`
*   **Funcionamento**:
    *   Ao salvar dados no cache, o JSON do resultado é encriptado usando a `ENCRYPTION_KEY`.
    *   Ao consultar o cache, o registro é decriptado transparentemente antes de ser retornado à aplicação.
    *   O cache possui tempo de expiração padrão (ex: 24 horas).

---

## 3. Orquestração e Fila de Processamento (Jobs)

A orquestração assíncrona é feita utilizando o próprio Supabase como fila de mensagens relacional:

*   **Tabela de Fila**: `jobs`
*   **Status de Jobs**: `pending` (pendente), `processing` (em processamento), `completed` (concluído), `failed` (falhou).
*   **Worker**: Executa em loop contínuo (`run_worker.py`), puxando registros com status `pending`, alterando para `processing`, chamando os scrapers através do `QueryCoordinator` e finalizando como `completed` ou `failed`.

---

## 4. Query & Budget Coordinator

Para evitar concorrência desordenada e gerenciar o fluxo complexo de orquestração de scrapers, o sistema adota coordenadores centralizados:

*   **`QueryCoordinator` (`app/core/coordinator.py`)**: Descoberta dinâmica de placas e disparo em paralelo de múltiplos scrapers.
*   **`BudgetCoordinator` (`app/core/budget_coordinator.py`)**: Coordena o workflow de 7 passos para cálculo de orçamentos consolidados:
    1. Consulta placa no **DataF5** para obter Renavam, Chassi e CPF do proprietário.
    2. Consulta em paralelo o **DETRAN-RJ** (Cadastro, Multas Detalhadas, Nada Consta).
    3. Se houver Comunicação de Venda, descobre o CPF do novo proprietário via **SEFAZ-RJ**.
    4. Consulta débitos de GRT no **Bradesco** (se aplicável).
    5. Consulta débitos de GRM (Multas) no **Bradesco** (sempre).
    6. Consulta débitos de IPVA e Dívida Ativa na **SEFAZ-RJ** (se aplicável).
    7. Agrupa todos os débitos, calcula o orçamento total consolidado e salva no cache Supabase.
