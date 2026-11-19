-- DDL do schema relatorios, versão 1.0. Gerado de relatorios/hermes/views_sql.py.
-- As views só leem tabelas do sistema; o papel hermes_ro só tem SELECT nelas.

CREATE SCHEMA IF NOT EXISTS relatorios;
DROP VIEW IF EXISTS relatorios.vw_receita_mensal CASCADE;
DROP VIEW IF EXISTS relatorios.vw_inadimplencia CASCADE;
DROP VIEW IF EXISTS relatorios.vw_contratos CASCADE;
DROP VIEW IF EXISTS relatorios.vw_ocupacao CASCADE;
DROP VIEW IF EXISTS relatorios.vw_pagamentos CASCADE;
CREATE VIEW relatorios.vw_pagamentos AS
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
            WHEN cb.vencimento < (now() AT TIME ZONE 'America/Sao_Paulo')::date THEN 'atrasada'
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
 ORDER BY cb.vencimento, cb.id;

COMMENT ON VIEW relatorios.vw_pagamentos IS 'Uma linha por cobrança (aluguel ou condomínio) que não foi cancelada.';
COMMENT ON COLUMN relatorios.vw_pagamentos.id IS 'Código da cobrança no sistema.';
COMMENT ON COLUMN relatorios.vw_pagamentos.contrato_id IS 'Contrato da unidade e da empresa. Vazio nas cobranças que somam várias unidades.';
COMMENT ON COLUMN relatorios.vw_pagamentos.locatario IS 'Razão social da empresa.';
COMMENT ON COLUMN relatorios.vw_pagamentos.competencia IS 'Mês de referência da cobrança, AAAA-MM. O condomínio de 10/2026 é cobrado em 11/2026.';
COMMENT ON COLUMN relatorios.vw_pagamentos.data_vencimento IS 'Vencimento.';
COMMENT ON COLUMN relatorios.vw_pagamentos.valor_devido IS 'Valor cobrado, em reais.';
COMMENT ON COLUMN relatorios.vw_pagamentos.data_pagamento IS 'Dia da baixa. Vazio se não foi paga.';
COMMENT ON COLUMN relatorios.vw_pagamentos.valor_pago IS 'Valor efetivamente recebido na baixa. Vazio se não foi paga.';
COMMENT ON COLUMN relatorios.vw_pagamentos.status IS '''paga'', ''atrasada'' (em aberto e vencida) ou ''em_aberto'' (no prazo).';
COMMENT ON COLUMN relatorios.vw_pagamentos.tipo IS '''aluguel'' ou ''condominio''.';
COMMENT ON COLUMN relatorios.vw_pagamentos.sala IS 'Unidade da cobrança; nas somadas, os nomes das unidades separados por vírgula.';
CREATE VIEW relatorios.vw_ocupacao AS
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
 ORDER BY u.ordem, u.id;

COMMENT ON VIEW relatorios.vw_ocupacao IS 'Uma linha por unidade ativa, com a situação de ocupação hoje.';
COMMENT ON COLUMN relatorios.vw_ocupacao.sala IS 'Nome da unidade (andar inteiro ou sala).';
COMMENT ON COLUMN relatorios.vw_ocupacao.andar IS 'Andar.';
COMMENT ON COLUMN relatorios.vw_ocupacao.area_m2 IS 'Área em m². É a privativa; quando a unidade não tem, é a área do rateio. A base de cada linha está em base_area.';
COMMENT ON COLUMN relatorios.vw_ocupacao.status_atual IS '''ocupada'' (contrato vigente), ''ocupada_sem_contrato'' ou ''vaga''.';
COMMENT ON COLUMN relatorios.vw_ocupacao.tem_contrato_ativo IS 'Há contrato vigente na unidade.';
COMMENT ON COLUMN relatorios.vw_ocupacao.locatario_atual IS 'Razão social de quem ocupa. Vazio se vaga.';
COMMENT ON COLUMN relatorios.vw_ocupacao.valor_mensal_atual IS 'Aluguel mensal do contrato vigente, em reais.';
COMMENT ON COLUMN relatorios.vw_ocupacao.inicio_contrato IS 'Início do contrato vigente.';
COMMENT ON COLUMN relatorios.vw_ocupacao.fim_contrato IS 'Fim do contrato vigente. Vazio se indeterminado.';
COMMENT ON COLUMN relatorios.vw_ocupacao.tipo IS '''andar'' ou ''sala''.';
COMMENT ON COLUMN relatorios.vw_ocupacao.base_area IS 'Qual área está em area_m2, linha a linha: ''privativa'' ou ''rateio'' (andar inteiro, sem área privativa). Não compare R$/m² entre bases diferentes. Vazio se a unidade não tem nenhuma das duas.';
CREATE VIEW relatorios.vw_contratos AS
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
 ORDER BY c.id;

COMMENT ON VIEW relatorios.vw_contratos IS 'Uma linha por contrato, em qualquer situação.';
COMMENT ON COLUMN relatorios.vw_contratos.id IS 'Código do contrato.';
COMMENT ON COLUMN relatorios.vw_contratos.locatario IS 'Razão social da empresa.';
COMMENT ON COLUMN relatorios.vw_contratos.documento IS 'CNPJ da empresa, como cadastrado.';
COMMENT ON COLUMN relatorios.vw_contratos.sala IS 'Unidade do contrato.';
COMMENT ON COLUMN relatorios.vw_contratos.data_inicio IS 'Início.';
COMMENT ON COLUMN relatorios.vw_contratos.data_fim IS 'Fim. Vazio se indeterminado.';
COMMENT ON COLUMN relatorios.vw_contratos.valor_mensal IS 'Aluguel mensal, em reais.';
COMMENT ON COLUMN relatorios.vw_contratos.dia_vencimento IS 'Dia do mês do vencimento.';
COMMENT ON COLUMN relatorios.vw_contratos.indice_reajuste IS '''igpm'', ''ipca'', ''fixo'' ou ''nenhum''.';
COMMENT ON COLUMN relatorios.vw_contratos.status IS '''negociacao'', ''vigente'', ''encerrado'' ou ''rescindido''.';
CREATE VIEW relatorios.vw_inadimplencia AS
SELECT p.locatario,
       p.sala,
       p.competencia,
       p.data_vencimento,
       p.valor_devido,
       ((now() AT TIME ZONE 'America/Sao_Paulo')::date - p.data_vencimento) AS dias_atraso,
       p.tipo
  FROM relatorios.vw_pagamentos p
 WHERE p.status = 'atrasada'
 ORDER BY p.data_vencimento, p.locatario;

COMMENT ON VIEW relatorios.vw_inadimplencia IS 'Cobranças em aberto com vencimento já passado.';
COMMENT ON COLUMN relatorios.vw_inadimplencia.locatario IS 'Razão social da empresa.';
COMMENT ON COLUMN relatorios.vw_inadimplencia.sala IS 'Unidade; nas cobranças somadas, os nomes separados por vírgula.';
COMMENT ON COLUMN relatorios.vw_inadimplencia.competencia IS 'Mês de referência, AAAA-MM.';
COMMENT ON COLUMN relatorios.vw_inadimplencia.data_vencimento IS 'Vencimento.';
COMMENT ON COLUMN relatorios.vw_inadimplencia.valor_devido IS 'Valor em atraso, em reais.';
COMMENT ON COLUMN relatorios.vw_inadimplencia.dias_atraso IS 'Dias corridos de atraso, contados de hoje (fuso America/Sao_Paulo).';
COMMENT ON COLUMN relatorios.vw_inadimplencia.tipo IS '''aluguel'' ou ''condominio''.';
CREATE VIEW relatorios.vw_receita_mensal AS
SELECT p.competencia,
       SUM(p.valor_devido) AS previsto,
       COALESCE(SUM(COALESCE(p.valor_pago, p.valor_devido)) FILTER (WHERE p.status = 'paga'), 0) AS recebido,
       COALESCE(SUM(p.valor_devido) FILTER (WHERE p.status <> 'paga'), 0) AS em_aberto
  FROM relatorios.vw_pagamentos p
 GROUP BY p.competencia
 ORDER BY p.competencia;

COMMENT ON VIEW relatorios.vw_receita_mensal IS 'Totais por mês de referência, somando aluguel e condomínio. Cobranças canceladas não entram.';
COMMENT ON COLUMN relatorios.vw_receita_mensal.competencia IS 'Mês de referência, AAAA-MM.';
COMMENT ON COLUMN relatorios.vw_receita_mensal.previsto IS 'Soma do que foi cobrado.';
COMMENT ON COLUMN relatorios.vw_receita_mensal.recebido IS 'Soma do que foi pago de fato.';
COMMENT ON COLUMN relatorios.vw_receita_mensal.em_aberto IS 'Soma do que ainda não foi pago, vencido ou não.';
COMMENT ON SCHEMA relatorios IS 'Views somente leitura para o agente de relatórios. Versão 1.0.';
