# Regras de Negócio para Consultas Veiculares

## Visão Geral

O API aplica regras de negócio automaticamente para análise de veículos conforme especificado pelo despachante. O objetivo é identificar impedimentos para regularização antes de sugerir ações.

## Fluxo de Execução

### Step 0: Inicialização - Nada Consta Apreendido (Decision Point)
**Pré-requisito:** Chassi deve ser informado

Essa consulta é o inicializador do workflow. Seu resultado determina:
- Se há débitos de IPVA/GRT → Executa consultas Bradesco/SEFAZ
- Se há dívida ativa → Recomenda consulta em consultadividaativa.rj.gov.br
- Se há restrições → Identifica impedimentos imediatos

### Steps 1-2: DETRAN - Cadastro + Multas
Consultas paralelas para obter:
- Dados de cadastro (combustível, observações, restrições, etc)
- Multas detalhadas

### Step 3: SEFAZ Discovery (Comunicação de Venda)
Executado se: `comunicacao_venda == "SIM"` no cadastro DETRAN
- Descobre CPF/CNPJ real do proprietário
- Esse CPF é usado nas consultas seguintes (GRT, SEFAZ IPVA)

### Steps 4-5: Bradesco GRT + Multas
**Step 4** executado se: Há IPVA/Licenciamento no nada consta  
**Step 5** executado sempre (validar multas)

Obs: Se há comunicação de venda, usa CPF descoberto no Step 3

### Step 6-Part-2: SEFAZ IPVA/Dívida Ativa
Executado se: Há IPVA ou dívida ativa no nada consta

## Regras de Negócio Implementadas

### 1. GNV + INMETRO (Vistoria Obrigatória)
**Condição:** Veículo com combustível GNV

**Validação:** 
- Verifica se INMETRO consta em observações
- Valida se o ano vigente está presente (CSV/INMETRO + ano atual)

**Impedimento:** Se GNV detectado mas INMETRO não está validado no ano vigente
```
Motivo: "Necessário fazer vistoria INMETRO no ano vigente"
```

### 2. Cilindrada Zerada + Ano Fabricação < 2018
**Condição:** 
- Cilindrada = 0 ou vazia
- Ano de fabricação < 2018

**Impedimento:** Requer serviço com vistoria
```
Motivo: "Necessário fazer serviço com vistoria (acerto dados/transferência/2via) 
         ou abrir processo administrativo"
```

### 3. Comunicação/Intenção de Venda
**Condição:** Campo `comunicacao_venda` = "SIM" no DETRAN

**Ação Necessária:**
- Fazer transferência de propriedade para novo CPF/nome
- OU cancelar a comunicação de venda
- GRT deve ser consultado com CPF da comunicação de venda

### 4. Dívida Ativa
**Condição:** Campo `DIVIDA_ATIVA` = "SIM" no nada consta apreendido

**Ação Necessária:**
- Consultar em: consultadividaativa.rj.gov.br/consultadebitosdividaativarj/servlet/StartCISPage

### 5. Gravame/Financiamento
**Condição:** Campo `has_gravame` = "SIM" no cadastro DETRAN

**Ação Necessária:**
- Fazer inclusão de financiamento no DETRAN
- Pode ser feito com ou sem transferência de propriedade

### 6. Inclusão Restrição
**Condição:** Restrições contêm "INCLUSÃO"

**Impedimento:** Impede licenciamento direto
```
Motivo: "Inclusão impede licenciamento"
```

### 7. Multas por Tipo de Serviço
Diferentes serviços exigem pagamento de multas diferentes:

#### Licenciamento
- Pagar apenas: **Multas Transitadas em Julgado**

#### Transferência / Serviços com Vistoria
- Pagar: 
  - Multas Transitadas em Julgado
  - Multas marcadas como Penalidade
  - Multas RENAINF (mesmo que autuação)

#### Padrão
- Todas as multas (para referência)

## Resposta da Análise

### Formato da Resposta - Step 7 (Relatório)

```json
{
  "analise_negocio": {
    "timestamp": "2026-09-26T...",
    "status": "LIBERADO|IMPEDIDO",
    "pode_regularizar": true/false,
    "impedimentos": [
      {
        "categoria": "GNV",
        "motivo": "Necessário fazer vistoria INMETRO no ano vigente"
      }
    ],
    "avisos": [
      "Dívida Ativa detectada - Consultar: consultadividaativa.rj.gov.br"
    ],
    "acoes_necessarias": [
      {
        "tipo": "Comunicação de Venda",
        "acao": "Fazer transferência para novo CPF/nome ou cancelar comunicação"
      }
    ],
    "multas_analise": {
      "tipo_servico": "default|licenciamento|transferencia|vistoria",
      "total_multas": 3,
      "observacao": "Pagar conforme tipo de serviço"
    }
  },
  "pode_regularizar": false,
  "status": "IMPEDIDO",
  "impedimentos": [...],
  "mensagem_ao_cliente": "Existe impedimento para regularização do veículo. Solicite análise e orçamento ao despachante."
}
```

## Comunicação com Cliente

### Se `pode_regularizar = true`
```
"Veículo apto para regularização"
```
Detalhes de débitos e ações podem ser apresentadas.

### Se `pode_regularizar = false`
```
"Existe impedimento para regularização do veículo. Solicite análise e orçamento ao despachante."
```

**Importante:** 
- NÃO informar ao cliente qual é o motivo do impedimento
- NÃO listar detalhes técnicos
- Instruir a buscar despachante para análise profissional
- Os detalhes dos impedimentos ficam disponíveis em `impedimentos` para o sistema interno

## Integração no Endpoint

### GET `/consulta/orcamento`
Query Parameters:
- `placa` (obrigatório)
- `renavam` (obrigatório)
- `cpf` (obrigatório)
- `chassi` (opcional, mas necessário para "nada consta apreendido")

Resposta contém:
- `step_7_relatorio_decisao` com análise completa
- `pode_regularizar` indicando se há impedimentos
- `mensagem_ao_cliente` com texto apropriado

## Cache
Resultado é cacheado por 24h com chave: `budget:{placa}`
