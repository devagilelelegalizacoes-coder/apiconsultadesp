# Guia de Implementação: Adicionando Novas Consultas (Scrapers)

Siga este passo a passo para estender a API com novos portais de consulta veicular.

## Passo 1: Criar o Scraper
Crie um novo arquivo em `app/scrapers/meu_portal.py`. Use a classe `BaseScraper` para herdar funcionalidades de proxy e inicialização de navegador.

```python
from app.scrapers.base_scraper import BaseScraper
from typing import Dict, Any

class MeuPortalScraper(BaseScraper):
    async def get_data(self, renavam: str) -> Dict[str, Any]:
        page = await self.init_browser()
        try:
            await page.goto("https://portal-exemplo.com.br")
            await page.fill("#input_renavam", renavam)
            await page.click("#btn_consultar")
            
            # Extração
            nome = await page.locator(".nome-proprietario").inner_text()
            
            return {
                "source": "MeuPortal",
                "status": "success",
                "data": {"nome": nome.strip()}
            }
        except Exception as e:
            return {"source": "MeuPortal", "status": "error", "message": str(e)}
        finally:
            await self.close()
```

## Passo 2: Registrar no `app/main.py`
Adicione o endpoint individual para que a consulta possa ser testada isoladamente.

```python
from app.scrapers.meu_portal import MeuPortalScraper

@app.get("/meuportal/{renavam}")
async def query_meu_portal(renavam: str):
    scraper = MeuPortalScraper()
    return await scraper.get_data(renavam)
```

## Passo 3: Integrar no `Coordinator` (Opcional)
Para que a nova consulta faça parte do endpoint consolidado `/veiculo/{renavam}`, edite `app/core/coordinator.py`.

1. Importe o novo Scraper.
2. Instancie-o no método `run_parallel_queries`.
3. Adicione-o ao `asyncio.gather`.

## Passo 4: Normalizar os Resultados (Opcional)
Edite `app/core/normalizer.py` para mapear os campos do novo portal para o formato padrão da API.

1. Crie o método `normalize_meu_portal`.
2. Adicione a lógica no método `merge_results`.

---

### Dicas Pro:
- **Captchas**: Use `from app.infrastructure.captcha_solver import solver` para resolver ReCaptchas ou Captchas de Imagem.
- **Stealth**: A `BaseScraper` já utiliza o plugin `Stealth` para evitar detecção por bots.
- **Windows**: Sempre rode o servidor via `python run_api.py` para garantir que o loop do asyncio (`Proactor`) seja configurado corretamente.
