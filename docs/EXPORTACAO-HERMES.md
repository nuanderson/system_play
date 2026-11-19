# Exportação para o agente de relatórios (Hermes)

Documento de referência. Não contém senhas nem chaves. A parte de colunas é gerada do código (`relatorios/hermes/views_sql.py`).

## O que é

Uma ponte **somente de leitura** entre o sistema e o agente de relatórios, em dois níveis:

1. **Schema `relatorios`** no PostgreSQL: views estáveis, lidas por um papel de banco com o mínimo de poder (`hermes_ro`).
2. **Exportação diária**: o comando `exportar_hermes` lê essas views e gera `uruz_export_AAAA-MM-DD.zip`, que vai para uma pasta do Google Drive.

**O agente não conecta no banco.** A porta 5432 não é aberta à internet. O papel `hermes_ro` só é usado pelo próprio job de exportação, dentro do servidor.

Versão do schema das views: **1.0** (vai no `manifest.json`). Nomes e colunas só mudam junto com esse número.

## O que fica de fora (de propósito)

- **Despesas**: contêm nomes e salários de funcionários. Precisa de autorização à parte (e, provavelmente, de anonimização).
- **Chamados de manutenção**, correspondências, usuários, e-mails, telefones, textos livres, boletos e chaves Pix.

## Views e colunas

Valores em reais, com ponto decimal. Datas em ISO 8601. "Hoje" é sempre no fuso `America/Sao_Paulo`. As cobranças **canceladas não aparecem** em nenhuma view.

### `relatorios.vw_pagamentos`

Uma linha por cobrança (aluguel ou condomínio) que não foi cancelada.

| Coluna | Tipo | O que é |
|---|---|---|
| `id` | integer | Código da cobrança no sistema. |
| `contrato_id` | integer | Contrato da unidade e da empresa. Vazio nas cobranças que somam várias unidades. |
| `locatario` | text | Razão social da empresa. |
| `competencia` | text | Mês de referência da cobrança, AAAA-MM. O condomínio de 10/2026 é cobrado em 11/2026. |
| `data_vencimento` | date | Vencimento. |
| `valor_devido` | numeric | Valor cobrado, em reais. |
| `data_pagamento` | date | Dia da baixa. Vazio se não foi paga. |
| `valor_pago` | numeric | Valor efetivamente recebido na baixa. Vazio se não foi paga. |
| `status` | text | 'paga', 'atrasada' (em aberto e vencida) ou 'em_aberto' (no prazo). |
| `tipo` | text | 'aluguel' ou 'condominio'. |
| `sala` | text | Unidade da cobrança; nas somadas, os nomes das unidades separados por vírgula. |

### `relatorios.vw_ocupacao`

Uma linha por unidade ativa, com a situação de ocupação hoje.

| Coluna | Tipo | O que é |
|---|---|---|
| `sala` | text | Nome da unidade (andar inteiro ou sala). |
| `andar` | text | Andar. |
| `area_m2` | numeric | Área em m². É a privativa; quando a unidade não tem, é a área do rateio. A base de cada linha está em base_area. |
| `status_atual` | text | 'ocupada' (contrato vigente), 'ocupada_sem_contrato' ou 'vaga'. |
| `tem_contrato_ativo` | boolean | Há contrato vigente na unidade. |
| `locatario_atual` | text | Razão social de quem ocupa. Vazio se vaga. |
| `valor_mensal_atual` | numeric | Aluguel mensal do contrato vigente, em reais. |
| `inicio_contrato` | date | Início do contrato vigente. |
| `fim_contrato` | date | Fim do contrato vigente. Vazio se indeterminado. |
| `tipo` | text | 'andar' ou 'sala'. |
| `base_area` | text | Qual área está em area_m2, linha a linha: 'privativa' ou 'rateio' (andar inteiro, sem área privativa). Não compare R$/m² entre bases diferentes. Vazio se a unidade não tem nenhuma das duas. |

### `relatorios.vw_contratos`

Uma linha por contrato, em qualquer situação.

| Coluna | Tipo | O que é |
|---|---|---|
| `id` | integer | Código do contrato. |
| `locatario` | text | Razão social da empresa. |
| `documento` | text | CNPJ da empresa, como cadastrado. |
| `sala` | text | Unidade do contrato. |
| `data_inicio` | date | Início. |
| `data_fim` | date | Fim. Vazio se indeterminado. |
| `valor_mensal` | numeric | Aluguel mensal, em reais. |
| `dia_vencimento` | integer | Dia do mês do vencimento. |
| `indice_reajuste` | text | 'igpm', 'ipca', 'fixo' ou 'nenhum'. |
| `status` | text | 'negociacao', 'vigente', 'encerrado' ou 'rescindido'. |

### `relatorios.vw_inadimplencia`

Cobranças em aberto com vencimento já passado.

| Coluna | Tipo | O que é |
|---|---|---|
| `locatario` | text | Razão social da empresa. |
| `sala` | text | Unidade; nas cobranças somadas, os nomes separados por vírgula. |
| `competencia` | text | Mês de referência, AAAA-MM. |
| `data_vencimento` | date | Vencimento. |
| `valor_devido` | numeric | Valor em atraso, em reais. |
| `dias_atraso` | integer | Dias corridos de atraso, contados de hoje (fuso America/Sao_Paulo). |
| `tipo` | text | 'aluguel' ou 'condominio'. |

### `relatorios.vw_receita_mensal`

Totais por mês de referência, somando aluguel e condomínio. Cobranças canceladas não entram.

| Coluna | Tipo | O que é |
|---|---|---|
| `competencia` | text | Mês de referência, AAAA-MM. |
| `previsto` | numeric | Soma do que foi cobrado. |
| `recebido` | numeric | Soma do que foi pago de fato. |
| `em_aberto` | numeric | Soma do que ainda não foi pago, vencido ou não. |

## O zip

`uruz_export_AAAA-MM-DD.zip` contém um CSV por view e o `manifest.json`:

- CSV: UTF-8 (sem BOM), cabeçalho na primeira linha, separador vírgula, quebra de linha `\n`, datas ISO 8601, números sem separador de milhar e com ponto decimal, nulo = campo vazio, booleano = `true`/`false`.
- `manifest.json`: `gerado_em` (America/Sao_Paulo), `versao_schema`, lista de `arquivos` (nome, view, número de `linhas`, `sha256`, `colunas`, `status`, `erro`), `erros` e `entrega`.
- Se uma view falhar, o erro vai no manifest e as demais seguem. O comando sai com código 1 para o systemd marcar a execução como falha.
- Rodar de novo no mesmo dia substitui o zip daquele dia.

## Convenções (também vão dentro do `manifest.json`, chave `convencoes`)

| Item | Regra |
|---|---|
| Encoding | UTF-8, sem BOM |
| Separador de campos | `,` |
| Quebra de linha | LF (apenas o caractere \n, sem \r) |
| Cabeçalho | a primeira linha traz o nome das colunas |
| Data | AAAA-MM-DD (ISO 8601) |
| Data e hora | ISO 8601 com o deslocamento do fuso, por exemplo 2026-11-19T06:10:03-03:00 |
| Decimal | ponto como separador decimal, sem separador de milhar e sem símbolo de moeda; valores em reais (BRL) |
| Nulo / booleano | campo vazio / true ou false |
| Fuso | `America/Sao_Paulo`: fuso da data e hora de geração (gerado_em) e do "hoje" usado em status "atrasada" e em dias_atraso |
| **competencia** | AAAA-MM do MÊS DE REFERÊNCIA da cobrança (o campo 'referencia' do sistema). NÃO é o mês do vencimento e NÃO é competência contábil. Aluguel: o mês a que o aluguel se refere (o aluguel de 2026-11 vence em 2026-11-10). Condomínio: o mês das despesas rateadas (o condomínio de 2026-10 é cobrado e vence em 2026-11-10). Em vw_receita_mensal, portanto, uma mesma competência soma o aluguel do mês X com o condomínio das despesas do mês X, que vence no mês seguinte. |
| Área (`area_m2`) | privativa quando existe; senão a área do rateio. A base de cada linha está em vw_ocupacao.base_area |

Uma view que funciona mas não tem dado no dia aparece no manifest com `vazia: true` e um `aviso`, e não como erro.

O DDL completo das views, com o comentário de cada coluna, está em `docs/hermes-views.sql`.

## Onde roda e quando

- Todo dia às **06:10 (America/Sao_Paulo)**, por um timer do systemd (`deploy/hermes/uruz-export.timer`). `Persistent=true`: se o servidor estiver desligado na hora, roda assim que ligar.
- Comando: `docker compose exec -T web python manage.py exportar_hermes` (serviço `uruz-export.service`).
- Log: `/var/log/uruz-export.log`, rotacionado semanalmente (8 semanas, comprimido).
- Zips: `/opt/uruz/exports` no servidor e a pasta **"Edifício Uruz - Exportações"** no Google Drive. Retenção de **60 dias** nos dois lugares, pela data no nome do arquivo.

## Configuração (no `.env` do servidor, permissão 600, fora do git)

| Variável | Para quê |
|---|---|
| `HERMES_RO_PASSWORD` | Senha do papel `hermes_ro` (gerada no servidor). |
| `HERMES_EXPORT_DIR` | Pasta dos zips dentro do container (`/exports`, que é `/opt/uruz/exports` no servidor). |
| `GOOGLE_SA_JSON` | Caminho da chave da service account dentro do container (`/secrets/uruz-export.json`). Sem o arquivo, o zip só fica no servidor. |
| `GOOGLE_DRIVE_FOLDER_ID` | Opcional. Id da pasta, se houver duas com o mesmo nome. |

## Operação do dia a dia

```bash
# rodar agora, na mão (como o timer faria)
sudo systemctl start uruz-export.service
# acompanhar
sudo tail -n 40 /var/log/uruz-export.log
systemctl list-timers uruz-export.timer
# reinstalar as views depois de uma mudança no sistema
cd /opt/uruz/app && docker compose -f docker-compose.yml -f /opt/uruz/compose.prod.yml exec -T web python manage.py instalar_views_hermes
```

> As views leem tabelas do sistema. Se um dia uma coluna dessas tabelas for renomeada ou apagada, o PostgreSQL recusa a mudança enquanto a view existir, ou a view para de funcionar. Os testes (`relatorios/test_hermes_postgres.py`) pegam isso antes de publicar.

## Entrega no Google Drive

A service account **só enxerga o que for compartilhado com o e-mail dela**. Compartilhe só a pasta "Edifício Uruz - Exportações", como **Editor**. Coloque a chave em `/opt/uruz/secrets/uruz-export.json` (dono uid 1000, permissão 600). O escopo usado é `drive`, o mínimo que o Google oferece para gravar numa pasta que a conta não criou.

## Revogar tudo

Na ordem abaixo. Cada passo é independente e pode ser repetido sem erro.

1. **Parar o agendamento**
   ```bash
   sudo systemctl disable --now uruz-export.timer
   sudo rm -f /etc/systemd/system/uruz-export.service /etc/systemd/system/uruz-export.timer /etc/logrotate.d/uruz-export
   sudo systemctl daemon-reload
   ```
2. **Remover o acesso à pasta do Drive**: no Google Drive, abra a pasta "Edifício Uruz - Exportações" → Compartilhar → remova o e-mail da service account. Depois, no Google Cloud, apague a chave (ou a service account inteira) e apague o arquivo `/opt/uruz/secrets/uruz-export.json`.
3. **Apagar o papel e as views do banco**
   ```bash
   cd /opt/uruz/app
   docker compose -f docker-compose.yml -f /opt/uruz/compose.prod.yml exec -T web python -c "import os; from relatorios.hermes import views_sql as v; print(chr(59).join(v.sql_de_revogacao(os.environ['DB_NAME'])) + chr(59))" \
     | docker compose -f docker-compose.yml -f /opt/uruz/compose.prod.yml exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
   ```
   Equivale a `REVOKE` em tudo, `DROP OWNED BY hermes_ro`, `DROP ROLE hermes_ro` e `DROP SCHEMA relatorios CASCADE`.
4. **Limpar as variáveis**: apague `HERMES_RO_PASSWORD`, `HERMES_EXPORT_DIR` e `GOOGLE_*` do `.env` e rode `sh /opt/uruz/deploy.sh`.
5. **Apagar os zips**: `sudo rm -rf /opt/uruz/exports` (e, se quiser, os arquivos já enviados à pasta do Drive).

## Ensaio da revogação (19/11/2026, em produção)

Rodado de verdade no servidor, com a exportação já funcionando, e depois tudo foi reinstalado.

| Passo | Ensaiado? | Resultado |
|---|---|---|
| 1. Parar e remover o timer, as units e o logrotate | Sim | Timer sumiu da lista, `systemctl cat` não acha mais as units. |
| 2. Tirar o acesso à pasta do Drive e apagar a chave no Google | **Não** (é no Google, fora do servidor) | Passo manual. Apagar o arquivo local da chave: `shred -u /opt/uruz/secrets/uruz-export.json`. |
| 3. `DROP` das views, do schema e do papel `hermes_ro` | Sim | Papel, schema e 5 views removidos; nenhum objeto restou para o papel. |
| 4. Limpar as variáveis do `.env` | Não | Edição simples de arquivo; sem o papel, o exportador falha com segurança. |
| 5. Apagar os zips | Não | Para não apagar a primeira exportação real. |
| O app depois da revogação | Sim | Login HTTP 200, `manage.py check` sem problemas, mesmos dados (7 empresas, 9 contratos, 14 cobranças). |
| O exportador depois da revogação | Sim | Sai com código 1 ("Não consegui conectar ao banco com o papel de leitura") e **não gera zip**. |
