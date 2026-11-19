"""Schema `relatorios`: views somente de leitura para o agente de relatórios (Hermes).

Contrato estável: nomes e colunas das views só mudam junto com VERSAO_SCHEMA. O agente nunca lê as
tabelas do sistema, só estas views, e por isso elas não trazem e-mails, telefones nem textos livres.
Fica de fora de propósito (precisa de autorização à parte): despesas (têm nomes e salários de funcionários)
e chamados de manutenção.
"""

import re

VERSAO_SCHEMA = '1.0'
SCHEMA = 'relatorios'
PAPEL_LEITURA = 'hermes_ro'
FUSO = 'America/Sao_Paulo'

# "hoje" no fuso do prédio, e não no do servidor (UTC)
HOJE = f"(now() AT TIME ZONE '{FUSO}')::date"

VIEWS = {}

# O que o agente precisa saber para ler os arquivos sem adivinhar. Vai inteiro dentro do manifest.json.
CONVENCOES = dict(
    csv=dict(
        encoding='UTF-8, sem BOM',
        separador_de_campos=',',
        delimitador_de_texto='" (aspas duplas, só quando o texto tem vírgula, aspas ou quebra de linha; aspas internas são dobradas)',
        quebra_de_linha='LF (apenas o caractere \\n, sem \\r)',
        cabecalho='a primeira linha traz o nome das colunas',
    ),
    valores=dict(
        data='AAAA-MM-DD (ISO 8601)',
        data_e_hora='ISO 8601 com o deslocamento do fuso, por exemplo 2026-11-19T06:10:03-03:00',
        decimal='ponto como separador decimal, sem separador de milhar e sem símbolo de moeda; valores em reais (BRL)',
        nulo='campo vazio',
        booleano='true ou false',
    ),
    fuso=FUSO,
    fuso_uso='fuso da data e hora de geração (gerado_em) e do "hoje" usado em status "atrasada" e em dias_atraso',
    competencia=(
        "AAAA-MM do MÊS DE REFERÊNCIA da cobrança (o campo 'referencia' do sistema). NÃO é o mês do vencimento e "
        "NÃO é competência contábil. Aluguel: o mês a que o aluguel se refere (o aluguel de 2026-11 vence em 2026-11-10). "
        "Condomínio: o mês das despesas rateadas (o condomínio de 2026-10 é cobrado e vence em 2026-11-10). Em vw_receita_mensal, "
        "portanto, uma mesma competência soma o aluguel do mês X com o condomínio das despesas do mês X, que vence no mês seguinte."),
    cobrancas_canceladas='não aparecem em nenhuma view',
    area_m2="privativa quando existe; senão a área do rateio. A base de cada linha está em vw_ocupacao.base_area",
)


def _view(nome, descricao, colunas, sql):
    VIEWS[nome] = dict(descricao=descricao, colunas=colunas, sql=sql.strip())


_view(
    'vw_pagamentos',
    'Uma linha por cobrança (aluguel ou condomínio) que não foi cancelada.',
    [
        ('id', 'integer', 'Código da cobrança no sistema.'),
        ('contrato_id', 'integer', 'Contrato da unidade e da empresa. Vazio nas cobranças que somam várias unidades.'),
        ('locatario', 'text', 'Razão social da empresa.'),
        ('competencia', 'text', 'Mês de referência da cobrança, AAAA-MM. O condomínio de 10/2026 é cobrado em 11/2026.'),
        ('data_vencimento', 'date', 'Vencimento.'),
        ('valor_devido', 'numeric', 'Valor cobrado, em reais.'),
        ('data_pagamento', 'date', 'Dia da baixa. Vazio se não foi paga.'),
        ('valor_pago', 'numeric', 'Valor efetivamente recebido na baixa. Vazio se não foi paga.'),
        ('status', 'text', "'paga', 'atrasada' (em aberto e vencida) ou 'em_aberto' (no prazo)."),
        ('tipo', 'text', "'aluguel' ou 'condominio'."),
        ('sala', 'text', 'Unidade da cobrança; nas somadas, os nomes das unidades separados por vírgula.'),
    ],
    f"""
SELECT cb.id,
       (SELECT ct.id FROM contratos_contrato ct
         WHERE ct.unidade_id = cb.unidade_id AND ct.locatario_id = cb.locatario_id
         ORDER BY (ct.status = 'vigente') DESC, ct.data_inicio DESC NULLS LAST, ct.id DESC
         LIMIT 1) AS contrato_id,
       l.razao_social AS locatario,
       to_char(cb.referencia, 'YYYY-MM') AS competencia,
       cb.vencimento AS data_vencimento,
       cb.valor AS valor_devido,
       cb.pago_em AS data_pagamento,
       cb.valor_pago AS valor_pago,
       CASE WHEN cb.status = 'paga' THEN 'paga'
            WHEN cb.vencimento < {HOJE} THEN 'atrasada'
            ELSE 'em_aberto' END AS status,
       cb.tipo AS tipo,
       COALESCE(u.nome,
                (SELECT string_agg(DISTINCT u2.nome, ', ' ORDER BY u2.nome)
                   FROM financeiro_cobrancaitem i
                   JOIN cadastros_unidade u2 ON u2.id = i.unidade_id
                  WHERE i.cobranca_id = cb.id)) AS sala
  FROM financeiro_cobranca cb
  JOIN cadastros_locatario l ON l.id = cb.locatario_id
  LEFT JOIN cadastros_unidade u ON u.id = cb.unidade_id
 WHERE cb.status <> 'cancelada'
 ORDER BY cb.vencimento, cb.id
""",
)

_view(
    'vw_ocupacao',
    'Uma linha por unidade ativa, com a situação de ocupação hoje.',
    [
        ('sala', 'text', 'Nome da unidade (andar inteiro ou sala).'),
        ('andar', 'text', 'Andar.'),
        ('area_m2', 'numeric',
         'Área em m². É a privativa; quando a unidade não tem, é a área do rateio. A base de cada linha está em base_area.'),
        ('status_atual', 'text', "'ocupada' (contrato vigente), 'ocupada_sem_contrato' ou 'vaga'."),
        ('tem_contrato_ativo', 'boolean', 'Há contrato vigente na unidade.'),
        ('locatario_atual', 'text', 'Razão social de quem ocupa. Vazio se vaga.'),
        ('valor_mensal_atual', 'numeric', 'Aluguel mensal do contrato vigente, em reais.'),
        ('inicio_contrato', 'date', 'Início do contrato vigente.'),
        ('fim_contrato', 'date', 'Fim do contrato vigente. Vazio se indeterminado.'),
        ('tipo', 'text', "'andar' ou 'sala'."),
        ('base_area', 'text',
         "Qual área está em area_m2, linha a linha: 'privativa' ou 'rateio' (andar inteiro, sem área privativa). "
         "Não compare R$/m² entre bases diferentes. Vazio se a unidade não tem nenhuma das duas."),
    ],
    """
SELECT u.nome AS sala,
       u.andar AS andar,
       COALESCE(u.area_privativa, u.area_rateio) AS area_m2,
       CASE WHEN c.id IS NOT NULL THEN 'ocupada'
            WHEN u.locatario_id IS NOT NULL THEN 'ocupada_sem_contrato'
            ELSE 'vaga' END AS status_atual,
       (c.id IS NOT NULL) AS tem_contrato_ativo,
       COALESCE(lc.razao_social, lu.razao_social) AS locatario_atual,
       c.valor_aluguel AS valor_mensal_atual,
       c.data_inicio AS inicio_contrato,
       c.data_fim AS fim_contrato,
       u.tipo AS tipo,
       CASE WHEN u.area_privativa IS NOT NULL THEN 'privativa'
            WHEN u.area_rateio IS NOT NULL THEN 'rateio' END AS base_area
  FROM cadastros_unidade u
  LEFT JOIN contratos_contrato c ON c.unidade_id = u.id AND c.status = 'vigente'
  LEFT JOIN cadastros_locatario lc ON lc.id = c.locatario_id
  LEFT JOIN cadastros_locatario lu ON lu.id = u.locatario_id
 WHERE u.ativa
 ORDER BY u.ordem, u.id
""",
)

_view(
    'vw_contratos',
    'Uma linha por contrato, em qualquer situação.',
    [
        ('id', 'integer', 'Código do contrato.'),
        ('locatario', 'text', 'Razão social da empresa.'),
        ('documento', 'text', 'CNPJ da empresa, como cadastrado.'),
        ('sala', 'text', 'Unidade do contrato.'),
        ('data_inicio', 'date', 'Início.'),
        ('data_fim', 'date', 'Fim. Vazio se indeterminado.'),
        ('valor_mensal', 'numeric', 'Aluguel mensal, em reais.'),
        ('dia_vencimento', 'integer', 'Dia do mês do vencimento.'),
        ('indice_reajuste', 'text', "'igpm', 'ipca', 'fixo' ou 'nenhum'."),
        ('status', 'text', "'negociacao', 'vigente', 'encerrado' ou 'rescindido'."),
    ],
    """
SELECT c.id,
       l.razao_social AS locatario,
       l.cnpj AS documento,
       u.nome AS sala,
       c.data_inicio,
       c.data_fim,
       c.valor_aluguel AS valor_mensal,
       c.dia_vencimento,
       c.indice_reajuste,
       c.status
  FROM contratos_contrato c
  JOIN cadastros_locatario l ON l.id = c.locatario_id
  JOIN cadastros_unidade u ON u.id = c.unidade_id
 ORDER BY c.id
""",
)

_view(
    'vw_inadimplencia',
    'Cobranças em aberto com vencimento já passado.',
    [
        ('locatario', 'text', 'Razão social da empresa.'),
        ('sala', 'text', 'Unidade; nas cobranças somadas, os nomes separados por vírgula.'),
        ('competencia', 'text', 'Mês de referência, AAAA-MM.'),
        ('data_vencimento', 'date', 'Vencimento.'),
        ('valor_devido', 'numeric', 'Valor em atraso, em reais.'),
        ('dias_atraso', 'integer', 'Dias corridos de atraso, contados de hoje (fuso America/Sao_Paulo).'),
        ('tipo', 'text', "'aluguel' ou 'condominio'."),
    ],
    f"""
SELECT p.locatario,
       p.sala,
       p.competencia,
       p.data_vencimento,
       p.valor_devido,
       ({HOJE} - p.data_vencimento) AS dias_atraso,
       p.tipo
  FROM {{schema}}.vw_pagamentos p
 WHERE p.status = 'atrasada'
 ORDER BY p.data_vencimento, p.locatario
""",
)

_view(
    'vw_receita_mensal',
    'Totais por mês de referência, somando aluguel e condomínio. Cobranças canceladas não entram.',
    [
        ('competencia', 'text', 'Mês de referência, AAAA-MM.'),
        ('previsto', 'numeric', 'Soma do que foi cobrado.'),
        ('recebido', 'numeric', 'Soma do que foi pago de fato.'),
        ('em_aberto', 'numeric', 'Soma do que ainda não foi pago, vencido ou não.'),
    ],
    """
SELECT p.competencia,
       SUM(p.valor_devido) AS previsto,
       COALESCE(SUM(COALESCE(p.valor_pago, p.valor_devido)) FILTER (WHERE p.status = 'paga'), 0) AS recebido,
       COALESCE(SUM(p.valor_devido) FILTER (WHERE p.status <> 'paga'), 0) AS em_aberto
  FROM {schema}.vw_pagamentos p
 GROUP BY p.competencia
 ORDER BY p.competencia
""",
)

# vw_inadimplencia e vw_receita_mensal leem vw_pagamentos, então ela é criada primeiro.
ORDEM_DE_CRIACAO = ['vw_pagamentos', 'vw_ocupacao', 'vw_contratos', 'vw_inadimplencia', 'vw_receita_mensal']
NOMES = list(ORDEM_DE_CRIACAO)  # ordem em que os arquivos aparecem no zip


def _aspas(texto):
    return texto.replace("'", "''")


def comandos_de_instalacao():
    """Os comandos SQL que (re)criam o schema e as views, na ordem certa. Seguro rodar de novo."""
    comandos = [f'CREATE SCHEMA IF NOT EXISTS {SCHEMA}']
    for nome in reversed(ORDEM_DE_CRIACAO):
        comandos.append(f'DROP VIEW IF EXISTS {SCHEMA}.{nome} CASCADE')
    for nome in ORDEM_DE_CRIACAO:
        v = VIEWS[nome]
        comandos.append(f'CREATE VIEW {SCHEMA}.{nome} AS\n' + v['sql'].replace('{schema}', SCHEMA))
        comandos.append(f"COMMENT ON VIEW {SCHEMA}.{nome} IS '{_aspas(v['descricao'])}'")
        for coluna, _tipo, descricao in v['colunas']:
            comandos.append(f"COMMENT ON COLUMN {SCHEMA}.{nome}.{coluna} IS '{_aspas(descricao)}'")
    comandos.append(f"COMMENT ON SCHEMA {SCHEMA} IS 'Views somente leitura para o agente de relatórios. Versão {VERSAO_SCHEMA}.'")
    return comandos


def instalar(cursor, papel=PAPEL_LEITURA):
    """Cria o schema e as views com o cursor dado. Se o papel de leitura já existe, devolve o SELECT nas views."""
    for comando in comandos_de_instalacao():
        cursor.execute(comando)
    cursor.execute('SELECT 1 FROM pg_roles WHERE rolname = %s', [papel])
    if cursor.fetchone():
        cursor.execute(f'GRANT USAGE ON SCHEMA {SCHEMA} TO {papel}')
        cursor.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {papel}')
        return True
    return False


def _identificador(nome):
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', nome):
        raise ValueError(f'Nome inválido para o banco: {nome!r}')
    return nome


def sql_do_papel(banco, senha, papel=PAPEL_LEITURA):
    """
    Comandos (para rodar como administrador do Postgres) que criam o papel de leitura com o mínimo de poder.
    Se o papel já existe, só troca a senha e reaplica os limites. A senha NUNCA entra em arquivo versionado:
    quem chama a recebe do ambiente e manda os comandos por entrada padrão, não por argumento de linha de comando.
    """
    papel, banco = _identificador(papel), _identificador(banco)
    if not re.fullmatch(r'[A-Za-z0-9_.\-]{16,128}', senha):
        raise ValueError('A senha deve ter de 16 a 128 caracteres, só letras, números e _ . -')
    atributos = 'LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2'
    return [
        f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{papel}') "
        f"THEN CREATE ROLE {papel} {atributos} PASSWORD '{senha}'; "
        f"ELSE ALTER ROLE {papel} WITH {atributos} PASSWORD '{senha}'; END IF; END $$",
        f'GRANT CONNECT ON DATABASE {banco} TO {papel}',
        f'GRANT USAGE ON SCHEMA {SCHEMA} TO {papel}',
        f'GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {papel}',
        f"ALTER ROLE {papel} SET statement_timeout = '30s'",
        f"ALTER ROLE {papel} SET idle_in_transaction_session_timeout = '30s'",
        f'ALTER ROLE {papel} SET default_transaction_read_only = on',
        f'ALTER ROLE {papel} SET search_path = {SCHEMA}',
    ]


def sql_de_revogacao(banco, papel=PAPEL_LEITURA, apagar_schema=True):
    """Comandos que desfazem tudo: tiram o acesso, apagam o papel e (opcionalmente) o schema de views."""
    papel, banco = _identificador(papel), _identificador(banco)
    comandos = [
        f"DO $$ BEGIN IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{papel}') THEN "
        f"EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {papel}'; "
        f"EXECUTE 'REVOKE ALL ON SCHEMA {SCHEMA} FROM {papel}'; "
        f"EXECUTE 'REVOKE ALL ON DATABASE {banco} FROM {papel}'; "
        f"EXECUTE 'DROP OWNED BY {papel}'; EXECUTE 'DROP ROLE {papel}'; END IF; END $$",
    ]
    if apagar_schema:
        comandos.append(f'DROP SCHEMA IF EXISTS {SCHEMA} CASCADE')
    return comandos
