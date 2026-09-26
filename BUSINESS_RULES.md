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

### Step 6-Part-2: SEFAZ IPVA
Executado se: Há IPVA ou dívida ativa no nada consta

### Step 6B: Dívida Ativa RJ (PGE-RJ)
Executado se: o nada consta aponta Dívida Ativa
- Portal: www.consultadividaativa.rj.gov.br (consulta por RENAVAM, sem captcha)
- Extrai certidão, situação, natureza, principal, multa, mora, honorários e total
- O total entra em `resumo_orcamento.total_divida_ativa`
- Importante: IPVA antigo inscrito em Dívida Ativa não aparece na SEFAZ ("não foram encontrados débitos")

### Multas consolidadas
- O status de cada multa vem do DETRAN (`tipo_status`); o valor atualizado vem do Bradesco
- Se o Bradesco falhar, usa as multas do DETRAN com valor original (sem juros) e gera aviso

## Regras de Negócio Implementadas

| Regra | Fonte | Resultado |
|---|---|---|
| GNV sem vistoria INMETRO no ano vigente | cadastro `combustivel` + `observacoes` | Impedimento |
| Cilindrada zerada e licenciamento < 2018 | cadastro `cilindrada` (card "lotação / potência / cilindrada") + `licenciamento` | Impedimento |
| Inclusão pendente | `mensagem_detran` / `observacoes` contém "INCLUS" | Impedimento (licenciamento não liberado) |
| DETRAN manda "PROVIDENCIAR" serviço (ex.: baixa de gravame) | `mensagem_detran` | Impedimento |
| Comunicação/intenção de venda | `mensagem_detran` / `observacoes` | Ação: transferir para o CPF da comunicação ou cancelar; GRT/multas consultados com esse CPF |
| Gravame / alienação fiduciária | cadastro `has_gravame` | Ação: inclusão/baixa de financiamento |
| Dívida Ativa | nada consta + PGE-RJ | Ação: quitar (valor no orçamento); se a PGE falhar, aviso para verificar manualmente |

### Multas por tipo de serviço (`multas_por_servico`)
- **licenciamento**: só multas transitadas em julgado
- **transferencia_ou_vistoria**: transitadas + penalidade (`tipo_status` "Multa ...") + RENAINF (mesmo sendo autuação)

## Formato do Step 7 (`step_7_relatorio_decisao`)

```json
{
  "status": "LIBERADO | IMPEDIDO",
  "pode_regularizar": false,
  "impedimentos": [{"categoria": "Pendência DETRAN", "motivo": "..."}],
  "acoes_necessarias": [{"tipo": "Dívida Ativa", "acao": "Quitar débitos inscritos em Dívida Ativa (R$ 13.351,56)"}],
  "avisos": ["..."],
  "multas_por_servico": {
    "licenciamento": {"quantidade": 2, "total": "R$ 325,39", "autos": ["..."]},
    "transferencia_ou_vistoria": {"quantidade": 6, "total": "R$ 846,03", "autos": ["..."]},
    "sem_classificacao": []
  },
  "mensagem_ao_cliente": "Existe impedimento para regularização do veículo. Solicite ao despachante a análise e o orçamento."
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
