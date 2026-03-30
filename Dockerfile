# Utilizando imagem oficial do Playwright para Python (Garante drivers e navegadores instalados)
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

# Configurações de ambiente
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Diretório de trabalho dentro do container
WORKDIR /app

# Instalação das dependências do sistema necessárias para compilar pacotes Python (se houver)
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia apenas o requirements primeiro para aproveitar o cache de camadas do Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instala apenas o Chromium do Playwright (focado para performance em Docker)
RUN playwright install chromium

# Copia todo o código-fonte do projeto para o container
COPY . .

# Expõe a porta que a API irá rodar (mesma definida no run_api.py)
EXPOSE 8000

# Variáveis de ambiente padrão (Deve ser sobrescrita no Easy Panel/Dashboard)
# ENV SUPABASE_URL=...
# ENV SUPABASE_KEY=...
# ENV CAPTCHA_API_KEY=...

# Comando para iniciar a API
# Usamos 0.0.0.0 para que o container possa receber conexões externas no Docker/Easy Panel
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
