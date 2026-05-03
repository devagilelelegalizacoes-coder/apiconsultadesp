# Consulta Fácil Veicular - API 🚗

Uma API de alto desempenho para consultas veiculares automatizadas e consolidadas (DETRAN-RJ, SEFAZ-RJ, Bradesco e DataF5). Construída com FastAPI, Playwright (Scraping) e Supabase (Banco de Dados e Fila).

---

## 📋 Funcionalidades
- **Consultas Independentes**: Cadastro (Detran), IPVA (Sefaz), Débitos GRT/Multas (Bradesco), Gravame (DataF5).
- **Consulta Consolidada**: Workflow completo que consulta todas as bases simultaneamente e unifica os resultados de forma otimizada.
- **Processamento em Background (Worker)**: Para consultas demoradas, permite a execução em fila via Redis/Supabase.
- **Autenticação JWT**: Acesso seguro via Bearer Token com gerenciamento de usuários.
- **Sistema Anti-Captcha Avançado**: Integrado com 2Captcha/Anti-Captcha para contornar verificações do DETRAN.
- **Cross-Platform**: Roda de forma transparente em **Windows** (desenvolvimento) e **Linux/Docker** (produção).

---

## 🛠️ Tecnologias Principais
- **[FastAPI](https://fastapi.tiangolo.com/)**: Framework Python para construção de APIs.
- **[Playwright](https://playwright.dev/python/)**: Automação de navegador para web scraping pesado.
- **[Supabase](https://supabase.com/)**: Backend as a Service (PostgreSQL) usado para Cache e Fila de Jobs.
- **[Docker & Docker Compose](https://www.docker.com/)**: Containerização para deploy padronizado.

---

## ⚙️ Pré-requisitos e Configuração

### 1. Preparar o Arquivo `.env`
Renomeie o arquivo `.env.exemplo` para `.env` e preencha as configurações:

```ini
# Supabase (Obrigatório para usuários e fila do Worker)
SUPABASE_URL=https://<SUA_URL>.supabase.co
SUPABASE_KEY=sua_chave_aqui

# Segurança JWT e Criptografia
JWT_SECRET=super_secret_jwt_key_here
ENCRYPTION_KEY=0123456789abcdef0123456789abcdef

# Captcha Solver (Obrigatório para o DETRAN-RJ)
CAPTCHA_PROVIDER=anticaptcha # ou 2captcha
CAPTCHA_API_KEY=sua_chave_aqui

# Portal DataF5 (Obrigatório para consulta completa/gravame)
DATAF5_USER=seu_usuario
DATAF5_PASS=sua_senha

# Configuração de Navegador
# HEADLESS=false abre a janela visualmente. HEADLESS=true (recomendado) roda em segundo plano.
HEADLESS=true
```

---

## 💻 Rodando Localmente (Windows / Mac)

Recomenda-se rodar sem o Docker para facilitar o desenvolvimento. O projeto inclui o `run_api.py`, que resolve automaticamente os problemas conhecidos do `asyncio` e do `Playwright` no Windows.

### Passo 1: Instalação
```bash
# Crie e ative um ambiente virtual
python -m venv venv
venv\Scripts\activate

# Instale as dependências Python
pip install -r requirements.txt

# Instale os navegadores do Playwright
playwright install --with-deps chromium
```

### Passo 2: Inicializar
Abra **dois terminais**.
1. No primeiro, inicie a API:
```bash
python run_api.py
```
A API estará rodando em `http://127.0.0.1:8000/docs`

2. No segundo, inicie o Worker (para processar consultas em fila):
```bash
python run_worker.py
```

---

## 🚀 Deploy em Servidor (Linux / Docker)

A forma **oficial e recomendada** de rodar o sistema em produção é através do `docker-compose`. Ele garante que todos os requisitos do Linux e do Playwright sejam cumpridos sem conflitos.

### Passo 1: Clonar e Configurar
```bash
git clone <seu_repositorio>
cd NovaApideConsultas
# Crie seu .env baseado no .env.exemplo
```

### Passo 2: Subir a Aplicação
```bash
# Constrói a imagem (baixa o Playwright) e inicia a API e o Worker
docker-compose up -d --build
```

Isso fará com que:
1. A API principal rode na porta `8000` (mapeada para a porta `80` do container).
2. O Worker seja iniciado em background automaticamente.
3. O modo `HEADLESS` seja forçado para `true`.

### Verificando os Logs
```bash
# Logs da API
docker-compose logs -f api

# Logs do Worker
docker-compose logs -f worker
```

---

## 🔒 Autenticação e Uso

1. Acesse `http://<SEU_IP>:8000/docs`
2. Clique no botão **Authorize** no canto superior direito.
3. Insira as credenciais do usuário admin padrão (geralmente `admin` / `admin123`).
4. Após o login, o token JWT será anexado a todas as requisições subsequentes do Swagger.

*(Consulte o arquivo `IMPLEMENTATION_GUIDE.md` para detalhes aprofundados sobre a arquitetura dos Scrapers e o Workflow de consulta orçamentária).*
