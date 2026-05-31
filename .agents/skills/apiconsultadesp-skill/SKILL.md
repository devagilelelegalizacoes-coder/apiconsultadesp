---
name: apiconsultadesp-skill
title: API de Consultas Veiculares (Detran/Sefaz/Bradesco/DataF5)
description: Playbook e Skill customizada para guiar agentes da IA Antigravity no desenvolvimento, manutenção, depuração e orquestração de scrapers veiculares usando Playwright, resolvedor unificado de Captchas, criptografia de cache Supabase e baterias de testes interativos.
version: 2.1.0
language: pt-BR
playbook:
  tasks:
    - name: Adicionar Novo Scraper
      description: Padrão para estender BaseScraper, gerenciar sessões Playwright e estruturar retornos.
    - name: Depuração Exception-Safe
      description: Como tratar eventos do Playwright (console, requestfailed) de forma robusta no Windows e Docker sem derrubar a requisição.
    - name: Resolução de Captcha
      description: Uso do CaptchaSolver unificado (Anti-Captcha/2captcha) para ReCaptcha V2, ReCaptcha Enterprise e Captchas de imagem.
    - name: Executar Testes Locais
      description: Como rodar a bateria de testes interativa test_all_scrapers.py de forma não-blocante e sem buffer no Windows.
---

# Playbook de Desenvolvimento - NovaApideConsultas

Este documento serve como uma Skill e Playbook técnico para qualquer agente de IA (como Antigravity) ou desenvolvedor trabalhando no projeto **NovaApideConsultas**.

Siga rigorosamente as diretrizes abaixo para manter a consistência, segurança e estabilidade das consultas.

---

## 1. Padrão de Desenvolvimento de Scrapers

Todos os scrapers veiculares devem:
1.  **Herdarem de `BaseScraper`** (`app/scrapers/base_scraper.py`).
2.  **Chamar `self.init_browser(use_stealth=...)`** para inicializar o Chromium de forma idêntica (carregando proxies do `.env` e acoplando listeners robustos).
3.  **Implementar Loops de Tentativa (Retries) Obrigatórios**:
    *   Todos os scrapers governamentais (como os do portal do DETRAN-RJ ou SEFAZ-RJ) devem possuir um loop de tentativas (`max_retries = 3` ou similar).
    *   **Regra de Reciclagem de Browser**: A inicialização do browser (`page = await self.init_browser(...)`) deve ocorrer **dentro** do loop de tentativas, e o encerramento (`await self.close()`) deve ser chamado obrigatoriamente no bloco `finally` interno de cada tentativa para garantir que sessões com captchas expirados ou falhas de rede sejam totalmente descartadas e recriadas do zero.
4.  **Validar Mensagens de Erro no Retorno da Página (Captchas Inválidos)**:
    *   Se a resposta da página após o clique no botão de consulta retornar `"CAPTCHA INVÁLIDO"`, o scraper deve registrar o log correspondente, executar um `continue` para reiniciar o loop e tentar resolver o captcha novamente em uma nova sessão do browser.
5.  **Extrair Erros Descritivos e Específicos do Corpo da Página (`document.body.innerText`)**:
    *   Em caso de timeouts aguardando seletores de resultados, o scraper deve ler o conteúdo total da página (`await page.evaluate("document.body.innerText")`).
    *   Se constarem erros do tipo `"não corresponde ao do proprietário registrado"`, `"Renavam incorreto"`, `"inválido"`, `"Veículo não encontrado"` ou `"Não existem débitos"`, o scraper deve retornar a mensagem exata como um erro (`{"status": "error", "message": "..."}`) em vez de cair em falsos-positivos (como retornar Nada Consta vazio) ou falhas genéricas.
6.  **Encapsular toda a lógica em blocos `try/except`** e retornar sempre um dicionário estruturado:
    *   Sucesso: `{"status": "success", "source": "NomePortal", "data": {...}}`
    *   Erro: `{"status": "error", "message": "Descrição detalhada do erro"}`

---

## 2. Tratamento de Plataforma (Windows vs Linux/Docker)

Ao iniciar tarefas em loop de evento ou trabalhar com scripts standalone no Windows:
1. **Loop do Asyncio (Proactor)**: No Windows (`sys.platform == 'win32'`), configure o loop de eventos como `asyncio.WindowsProactorEventLoopPolicy()`. Exemplo configurado em `app/main.py` e `test_all_scrapers.py`:
   ```python
   if sys.platform == 'win32':
       asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
   ```
2. **Buffer de Saída no Terminal**: Sempre execute os testes e scripts Python utilizando a flag `-u` para evitar delay ou enfileiramento de logs de console no terminal:
   ```powershell
   python -u test_all_scrapers.py
   ```

---

## 3. Depuração e Captura de Erros Robustos (Exception-Safe)

Para evitar que erros secundários de rede de terceiros (como imagens de trackers de anúncios não carregadas) ou mensagens estranhas no console do navegador quebrem a execução da API:

1.  **Unicode nos Consoles**: Ao escutar eventos `"console"`, trate possíveis erros de encoding no terminal Windows (CP1252) decodificando e limpando caracteres inválidos de forma segura.
2.  **Request Failed Erros**: O atributo `request.failure` do Playwright pode retornar tanto uma string simples quanto um objeto contendo a propriedade `.error_text` ou um dicionário. Sempre valide a instância antes de acessar o atributo para evitar o crash `AttributeError: 'str' object has no attribute 'error_text'`.
3.  **Lógica Pronta e Protegida**: Use a estrutura estabelecida no `BaseScraper` para tratar eventos:
    ```python
    def safe_print_request_failed(req):
        try:
            failure = req.failure
            err_text = "Unknown error"
            if failure:
                if isinstance(failure, str):
                    err_text = failure
                elif hasattr(failure, "error_text"):
                    err_text = failure.error_text
                elif isinstance(failure, dict) and "errorText" in failure:
                    err_text = failure["errorText"]
                else:
                    err_text = str(failure)
            print(f"[!] [Browser Net Error] {req.url}: {err_text}")
        except:
            pass
    ```

---

## 4. Resolução Unificada de Captcha

O arquivo `app/infrastructure/captcha_solver.py` expõe a instância `solver` que orquestra a resolução automatizada.

### 4.1. ReCaptcha V2 & Enterprise (Detran e Sefaz)
1. Extraia o `sitekey` e a URL da página de destino.
2. Chame o resolvedor passando a task apropriada (`NoCaptchaTaskProxyless` para padrão ou `RecaptchaV2EnterpriseTaskProxyless` para Enterprise):
   ```python
   captcha_token = await solver.solve_recaptcha_v2(sitekey, url, task_type="RecaptchaV2EnterpriseTaskProxyless")
   ```
3. Injete o token robustamente e clique em enviar (com fallback via JS):
   ```python
   await page.evaluate(f"document.getElementById('g-recaptcha-response').value = '{token}';")
   ```

### 4.2. Captcha de Imagem (Sefaz-RJ e Bradesco)
1. Recorte ou capture a imagem do captcha na tela tirando screenshot do elemento (.captcha-img).
2. Converta a imagem para `base64`.
3. Envie para o resolvedor e utilize o retorno do texto para preencher o campo:
   ```python
   captcha_text = await solver.solve_image_captcha(base64_image)
   await page.fill("#campo-captcha", captcha_text)
   ```

---

## 5. Caching Criptografado e Supabase

Para respeitar a LGPD e garantir a privacidade dos dados consultados:
1. **Banco de Dados**: Usamos Supabase para gerenciar sessões e armazenar o cache de consultas.
2. **Criptografia Simétrica (Fernet)**: Dados sensíveis contidos nos resultados das consultas (como CPF, Placa, Renavam e o JSON completo do veículo) devem ser criptografados antes de salvar no banco de dados e descriptografados no momento da leitura usando a classe em `app/security/encryption.py` com a chave configurada no `.env` (`ENCRYPTION_KEY`).

---

## 6. Fluxo de Implantação e Deploy

O projeto é empacotado para o **Easypanel** e contêineres Docker. 

Sempre que realizar correções críticas de infraestrutura ou bugs em scrapers locais, lembre-se de que os contêineres de produção só receberão as novas diretrizes se as alterações forem commitadas e enviadas ao repositório remoto:
```bash
git add app/scrapers/base_scraper.py
git commit -m "fix: resolve erro no base_scraper para estabilização de produção"
git push origin main
```
