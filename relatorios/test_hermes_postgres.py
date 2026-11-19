"""As views do schema relatorios contra um PostgreSQL de verdade: os números têm que bater com os do sistema.

Só roda com PostgreSQL (SQLite não tem schemas nem estas funções). Para rodar:
    docker compose exec web python manage.py test relatorios.test_hermes_postgres
"""

import secrets
from datetime import date, timedelta
from decimal import Decimal
from unittest import skipUnless

import psycopg
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from contratos.models import Contrato
from financeiro.models import Cobranca, CobrancaItem
from relatorios.hermes import views_sql
from relatorios.hermes.exportador import FontePostgres, montar_arquivos

D = Decimal
TIPOS = {'integer': {'integer', 'bigint', 'smallint'}, 'text': {'text', 'character varying'},
         'numeric': {'numeric'}, 'date': {'date'}, 'boolean': {'boolean'}}


def consultar(sql, *params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        colunas = [c.name for c in cursor.description]
        return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


@skipUnless(connection.vendor == 'postgresql', 'precisa de PostgreSQL')
class ViewsDoSchemaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        hoje = timezone.localdate()
        cls.hoje = hoje
        cls.vida = Locatario.objects.create(razao_social='Clínica Vida', cnpj='11.111.111/0001-11')
        cls.bela = Locatario.objects.create(razao_social='Bela Estética', cnpj='22.222.222/0001-22')
        cls.sem = Locatario.objects.create(razao_social='Sem Contrato Ltda')
        u = lambda nome: Unidade.objects.get(nome=nome)
        cls.s1, cls.s2, cls.s3, cls.s4, cls.s5 = (u(f'Sala 0{i}') for i in range(1, 6))
        Unidade.objects.filter(pk=cls.s5.pk).update(locatario=cls.sem)  # ocupada sem contrato
        Unidade.objects.filter(nome='Sala 08').update(ativa=False)       # inativa: fora da ocupação
        cls.c1 = Contrato.objects.create(unidade=cls.s1, locatario=cls.vida, status='vigente', data_inicio=date(2026, 1, 1),
                                         data_fim=date(2027, 12, 31), valor_aluguel=D('1000'), dia_vencimento=10, indice_reajuste='igpm')
        cls.c2 = Contrato.objects.create(unidade=cls.s2, locatario=cls.vida, status='vigente', data_inicio=date(2026, 1, 1),
                                         valor_aluguel=D('1500.50'), dia_vencimento=5, indice_reajuste='ipca')
        cls.c3 = Contrato.objects.create(unidade=cls.s3, locatario=cls.bela, status='vigente', data_inicio=date(2026, 2, 1),
                                         valor_aluguel=D('800'), dia_vencimento=15)
        cls.encerrado = Contrato.objects.create(unidade=cls.s4, locatario=cls.bela, status='encerrado',
                                                data_inicio=date(2025, 1, 1), data_fim=date(2025, 12, 31), valor_aluguel=D('700'))
        atrasada, futura = hoje - timedelta(days=12), hoje + timedelta(days=9)

        def cobrar(unidade, empresa, tipo, ref, valor, venc, status='aberta', valor_pago=None):
            return Cobranca.objects.create(
                unidade=unidade, locatario=empresa, tipo=tipo, referencia=ref, valor=D(valor), vencimento=venc, status=status,
                valor_pago=D(valor_pago) if valor_pago else None, pago_em=hoje - timedelta(days=3) if valor_pago else None)
        cls.paga = cobrar(cls.s3, cls.bela, 'aluguel', date(2026, 9, 1), '800', atrasada, 'paga', '790.50')
        cls.atrasada = cobrar(cls.s3, cls.bela, 'condominio', date(2026, 9, 1), '300.25', atrasada)
        cls.no_prazo = cobrar(cls.s1, cls.vida, 'aluguel', date(2026, 10, 1), '1000', futura)
        cls.cancelada = cobrar(cls.s2, cls.vida, 'aluguel', date(2026, 10, 1), '9999', atrasada, 'cancelada')
        cls.somada = Cobranca.objects.create(unidade=None, locatario=cls.vida, tipo='condominio', referencia=date(2026, 10, 1),
                                             valor=D('2400'), vencimento=atrasada)
        for unidade, valor in ((cls.s2, '1400'), (cls.s1, '1000')):
            CobrancaItem.objects.create(cobranca=cls.somada, unidade=unidade, descricao='Condomínio', valor=D(valor))
        views_sql.instalar(connection.cursor())

    # --- estrutura ---
    def test_as_colunas_do_banco_sao_as_documentadas_na_ordem(self):
        for nome, v in views_sql.VIEWS.items():
            reais = consultar("""SELECT column_name, data_type FROM information_schema.columns
                                  WHERE table_schema = 'relatorios' AND table_name = %s ORDER BY ordinal_position""", nome)
            self.assertEqual([r['column_name'] for r in reais], [c for c, _t, _d in v['colunas']], nome)
            for real, (coluna, tipo, _d) in zip(reais, v['colunas']):
                self.assertIn(real['data_type'], TIPOS[tipo], f'{nome}.{coluna}: {real["data_type"]} não é {tipo}')

    def test_so_existem_as_cinco_views_no_schema(self):
        tabelas = consultar("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'relatorios'")
        self.assertEqual(sorted(t['table_name'] for t in tabelas), sorted(views_sql.NOMES))
        self.assertEqual({t['table_type'] for t in tabelas}, {'VIEW'})  # nenhuma tabela

    def test_instalar_de_novo_nao_quebra(self):
        views_sql.instalar(connection.cursor())
        views_sql.instalar(connection.cursor())
        self.assertEqual(len(consultar('SELECT * FROM relatorios.vw_contratos')), 4)

    def test_toda_view_e_toda_coluna_tem_comentario(self):
        sem = consultar("""SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = 'relatorios' AND c.relkind = 'v' AND obj_description(c.oid) IS NULL""")
        self.assertEqual(sem, [])
        sem_col = consultar("""SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                                JOIN pg_namespace n ON n.oid = c.relnamespace
                               WHERE n.nspname = 'relatorios' AND c.relkind = 'v' AND a.attnum > 0 AND NOT a.attisdropped
                                 AND col_description(c.oid, a.attnum) IS NULL""")
        self.assertEqual(sem_col, [])

    # --- vw_pagamentos ---
    def test_pagamentos_traz_as_nao_canceladas_com_o_status_certo(self):
        linhas = {r['id']: r for r in consultar('SELECT * FROM relatorios.vw_pagamentos')}
        self.assertNotIn(self.cancelada.pk, linhas)
        self.assertEqual(len(linhas), Cobranca.objects.exclude(status='cancelada').count())
        self.assertEqual(linhas[self.paga.pk]['status'], 'paga')
        self.assertEqual(linhas[self.atrasada.pk]['status'], 'atrasada')
        self.assertEqual(linhas[self.no_prazo.pk]['status'], 'em_aberto')

    def test_pagamentos_valores_datas_e_competencia(self):
        r = {x['id']: x for x in consultar('SELECT * FROM relatorios.vw_pagamentos')}[self.paga.pk]
        self.assertEqual((r['valor_devido'], r['valor_pago']), (D('800.00'), D('790.50')))
        self.assertEqual((r['competencia'], r['tipo'], r['sala'], r['locatario']), ('2026-09', 'aluguel', 'Sala 03', 'Bela Estética'))
        self.assertEqual(r['data_pagamento'], self.hoje - timedelta(days=3))
        self.assertEqual(r['data_vencimento'], self.hoje - timedelta(days=12))

    def test_pagamentos_liga_ao_contrato_da_unidade_e_da_empresa(self):
        linhas = {r['id']: r for r in consultar('SELECT * FROM relatorios.vw_pagamentos')}
        self.assertEqual(linhas[self.paga.pk]['contrato_id'], self.c3.pk)
        self.assertEqual(linhas[self.no_prazo.pk]['contrato_id'], self.c1.pk)

    def test_cobranca_somada_lista_as_unidades_e_nao_inventa_contrato(self):
        r = {x['id']: x for x in consultar('SELECT * FROM relatorios.vw_pagamentos')}[self.somada.pk]
        self.assertEqual(r['sala'], 'Sala 01, Sala 02')
        self.assertIsNone(r['contrato_id'])

    # --- vw_inadimplencia ---
    def test_inadimplencia_so_tem_aberta_e_vencida_com_os_dias_certos(self):
        linhas = consultar('SELECT * FROM relatorios.vw_inadimplencia ORDER BY valor_devido')
        esperadas = Cobranca.atrasadas()
        self.assertEqual(len(linhas), esperadas.count())
        self.assertEqual(sum(l['valor_devido'] for l in linhas), sum(c.valor for c in esperadas))
        bela = next(l for l in linhas if l['valor_devido'] == D('300.25'))
        self.assertEqual((bela['locatario'], bela['sala'], bela['competencia'], bela['tipo']),
                         ('Bela Estética', 'Sala 03', '2026-09', 'condominio'))
        self.assertEqual(bela['dias_atraso'], self.atrasada.dias_atraso)  # mesma conta que o sistema faz
        self.assertEqual(bela['dias_atraso'], 12)

    def test_inadimplencia_nao_tem_paga_cancelada_nem_no_prazo(self):
        valores = {l['valor_devido'] for l in consultar('SELECT * FROM relatorios.vw_inadimplencia')}
        self.assertNotIn(D('800.00'), valores)    # paga
        self.assertNotIn(D('9999.00'), valores)   # cancelada
        self.assertNotIn(D('1000.00'), valores)   # no prazo

    # --- vw_receita_mensal ---
    def test_receita_mensal_bate_com_os_totais_do_sistema(self):
        for linha in consultar('SELECT * FROM relatorios.vw_receita_mensal'):
            ano, mes = map(int, linha['competencia'].split('-'))
            t = Cobranca.totais(Cobranca.objects.filter(referencia__year=ano, referencia__month=mes))
            self.assertEqual((linha['previsto'], linha['recebido'], linha['em_aberto']), (t['emitido'], t['recebido'], t['aberto']),
                             linha['competencia'])

    def test_receita_mensal_conta_feita_a_mao(self):
        linhas = {l['competencia']: l for l in consultar('SELECT * FROM relatorios.vw_receita_mensal')}
        # 2026-09: aluguel 800 paga (790,50) + condomínio 300,25 em aberto
        self.assertEqual((linhas['2026-09']['previsto'], linhas['2026-09']['recebido'], linhas['2026-09']['em_aberto']),
                         (D('1100.25'), D('790.50'), D('300.25')))
        # 2026-10: aluguel 1000 no prazo + condomínio somado 2400 em atraso (a cancelada de 9999 não entra)
        self.assertEqual((linhas['2026-10']['previsto'], linhas['2026-10']['recebido'], linhas['2026-10']['em_aberto']),
                         (D('3400.00'), D('0'), D('3400.00')))
        self.assertEqual(list(linhas), sorted(linhas))  # ordenado por competência

    # --- vw_ocupacao ---
    def test_ocupacao_uma_linha_por_unidade_ativa_com_a_situacao_certa(self):
        linhas = {l['sala']: l for l in consultar('SELECT * FROM relatorios.vw_ocupacao')}
        self.assertEqual(len(linhas), Unidade.objects.filter(ativa=True).count())
        self.assertNotIn('Sala 08', linhas)  # inativa
        s1 = linhas['Sala 01']
        self.assertEqual((s1['status_atual'], s1['tem_contrato_ativo'], s1['locatario_atual'], s1['valor_mensal_atual']),
                         ('ocupada', True, 'Clínica Vida', D('1000.00')))
        self.assertEqual((s1['inicio_contrato'], s1['fim_contrato']), (date(2026, 1, 1), date(2027, 12, 31)))
        self.assertEqual(linhas['Sala 02']['fim_contrato'], None)  # sem data de fim
        self.assertEqual(linhas['Sala 04']['status_atual'], 'vaga')  # só tem contrato encerrado
        self.assertFalse(linhas['Sala 04']['tem_contrato_ativo'])
        self.assertEqual((linhas['Sala 05']['status_atual'], linhas['Sala 05']['locatario_atual'], linhas['Sala 05']['tem_contrato_ativo']),
                         ('ocupada_sem_contrato', 'Sem Contrato Ltda', False))
        self.assertEqual(linhas['Sala 01']['area_m2'], self.s1.area_privativa or self.s1.area_rateio)
        andares = [l for l in linhas.values() if l['tipo'] == 'andar']
        self.assertTrue(andares and all(l['area_m2'] for l in andares))  # andar inteiro cai na área do rateio

    def test_base_area_diz_de_onde_veio_a_area_de_cada_linha(self):
        for unidade in Unidade.objects.filter(ativa=True):
            linha = next(l for l in consultar('SELECT * FROM relatorios.vw_ocupacao') if l['sala'] == unidade.nome)
            if unidade.area_privativa is not None:
                esperado, area = 'privativa', unidade.area_privativa
            elif unidade.area_rateio is not None:
                esperado, area = 'rateio', unidade.area_rateio
            else:
                esperado, area = None, None
            self.assertEqual((linha['base_area'], linha['area_m2']), (esperado, area), unidade.nome)
        # as duas bases existem de fato no cadastro, então a coluna precisa separar as duas
        bases = {l['base_area'] for l in consultar('SELECT base_area FROM relatorios.vw_ocupacao')}
        self.assertTrue({'privativa', 'rateio'} <= bases, bases)

    def test_unidade_sem_nenhuma_area_fica_com_base_vazia(self):
        Unidade.objects.create(nome='Sala sem area', tipo='sala', andar='9º andar', ativa=True)
        linha = next(l for l in consultar('SELECT * FROM relatorios.vw_ocupacao') if l['sala'] == 'Sala sem area')
        self.assertEqual((linha['area_m2'], linha['base_area']), (None, None))

    # --- vw_contratos ---
    def test_contratos_traz_todos_em_qualquer_situacao_com_cnpj(self):
        linhas = {l['id']: l for l in consultar('SELECT * FROM relatorios.vw_contratos')}
        self.assertEqual(len(linhas), Contrato.objects.count())
        c = linhas[self.c1.pk]
        self.assertEqual((c['locatario'], c['documento'], c['sala'], c['valor_mensal'], c['dia_vencimento'], c['indice_reajuste'], c['status']),
                         ('Clínica Vida', '11.111.111/0001-11', 'Sala 01', D('1000.00'), 10, 'igpm', 'vigente'))
        self.assertEqual(linhas[self.encerrado.pk]['status'], 'encerrado')


@skipUnless(connection.vendor == 'postgresql', 'precisa de PostgreSQL')
class PapelDeLeituraTests(TransactionTestCase):
    """O papel só lê as views e não escreve em nada. Usa um papel temporário, sem encostar no hermes_ro real."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.papel = 'hermes_ro_t' + secrets.token_hex(4)
        cls.senha = secrets.token_urlsafe(24).replace('=', 'x')
        cls.cfg = connection.settings_dict
        with connection.cursor() as cursor:
            for comando in views_sql.comandos_de_instalacao():
                cursor.execute(comando)
            for comando in views_sql.sql_do_papel(cls.cfg['NAME'], cls.senha, cls.papel):
                cursor.execute(comando)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        with connection.cursor() as cursor:
            for comando in views_sql.sql_de_revogacao(cls.cfg['NAME'], cls.papel, apagar_schema=False):
                cursor.execute(comando)

    def conectar(self):
        return psycopg.connect(host=self.cfg['HOST'], port=self.cfg['PORT'] or 5432, dbname=self.cfg['NAME'],
                               user=self.papel, password=self.senha, autocommit=True)

    def test_o_papel_existe_com_o_minimo_de_poder(self):
        r = consultar("""SELECT rolsuper, rolcreatedb, rolcreaterole, rolinherit, rolcanlogin, rolreplication, rolbypassrls, rolconnlimit,
                                rolconfig FROM pg_roles WHERE rolname = %s""", self.papel)[0]
        self.assertEqual((r['rolsuper'], r['rolcreatedb'], r['rolcreaterole'], r['rolinherit'], r['rolreplication'], r['rolbypassrls']),
                         (False, False, False, False, False, False))
        self.assertEqual((r['rolcanlogin'], r['rolconnlimit']), (True, 2))
        self.assertIn('statement_timeout=30s', r['rolconfig'])
        self.assertIn('default_transaction_read_only=on', r['rolconfig'])

    def test_a_conexao_nasce_somente_leitura_e_com_timeout(self):
        with self.conectar() as c:
            self.assertEqual(c.execute('SHOW default_transaction_read_only').fetchone()[0], 'on')
            self.assertEqual(c.execute('SHOW statement_timeout').fetchone()[0], '30s')
            self.assertEqual(c.execute('SHOW transaction_read_only').fetchone()[0], 'on')

    def test_le_as_views(self):
        with self.conectar() as c:
            for nome in views_sql.NOMES:
                c.execute(f'SELECT * FROM relatorios.{nome} LIMIT 1').fetchall()

    def test_nao_le_as_tabelas_do_sistema(self):
        for tabela in ('cadastros_locatario', 'contratos_contrato', 'financeiro_cobranca', 'core_usuario', 'django_session',
                       'financeiro_despesa', 'chamados_chamado'):
            with self.conectar() as c:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=tabela):
                    c.execute(f'SELECT * FROM public.{tabela} LIMIT 1')

    def test_escrita_e_recusada_de_todas_as_formas(self):
        tentativas = [
            "INSERT INTO public.cadastros_locatario (razao_social, cnpj, contato, email_cobranca, telefone, ativo, cobranca_unificada) "
            "VALUES ('x','','','','',true,true)",
            "UPDATE public.cadastros_locatario SET razao_social = 'x'",
            'DELETE FROM public.financeiro_cobranca',
            'TRUNCATE public.financeiro_cobranca',
            "INSERT INTO relatorios.vw_contratos (id) VALUES (1)",
            'DELETE FROM relatorios.vw_pagamentos',
            'CREATE TABLE relatorios.lixo (a int)',
            'CREATE TABLE public.lixo (a int)',
            'CREATE VIEW relatorios.minha_view AS SELECT 1',
            'DROP VIEW relatorios.vw_contratos',
            'DROP SCHEMA relatorios CASCADE',
            'GRANT SELECT ON ALL TABLES IN SCHEMA public TO PUBLIC',
        ]
        for sql in tentativas:
            with self.conectar() as c:
                with self.assertRaises(psycopg.Error, msg=sql):
                    c.execute(sql)

    def test_escrita_e_recusada_mesmo_pedindo_transacao_de_escrita(self):
        with self.conectar() as c:
            with self.assertRaises(psycopg.Error):
                c.execute('BEGIN READ WRITE')
                c.execute("INSERT INTO public.cadastros_locatario (razao_social) VALUES ('x')")

    def test_nao_consegue_virar_outro_papel_nem_mudar_a_propria_config(self):
        for sql in ('SET ROLE postgres', 'SET SESSION AUTHORIZATION postgres', f'ALTER ROLE {self.papel} SUPERUSER',
                    f'ALTER ROLE {self.papel} RESET ALL', 'CREATE ROLE intruso'):
            with self.conectar() as c:
                with self.assertRaises(psycopg.Error, msg=sql):
                    c.execute(sql)

    def test_o_limite_de_duas_conexoes_vale(self):
        a, b = self.conectar(), self.conectar()
        try:
            with self.assertRaisesRegex(psycopg.OperationalError, 'too many connections for role'):
                self.conectar()
        finally:
            a.close()
            b.close()

    def test_consulta_longa_e_cortada_pelo_timeout(self):
        with self.conectar() as c:
            c.execute("SET statement_timeout = '300ms'")  # o papel pode apertar o próprio limite, nunca soltar além do dele
            with self.assertRaises(psycopg.errors.QueryCanceled):
                c.execute('SELECT pg_sleep(3)')

    def test_revogar_desfaz_tudo_e_o_papel_some(self):
        papel = 'hermes_ro_r' + secrets.token_hex(4)
        with connection.cursor() as cursor:
            for comando in views_sql.sql_do_papel(self.cfg['NAME'], self.senha, papel):
                cursor.execute(comando)
            self.assertEqual(consultar('SELECT 1 FROM pg_roles WHERE rolname = %s', papel), [{'?column?': 1}])
            for comando in views_sql.sql_de_revogacao(self.cfg['NAME'], papel, apagar_schema=False):
                cursor.execute(comando)
        self.assertEqual(consultar('SELECT 1 FROM pg_roles WHERE rolname = %s', papel), [])
        # rodar a revogação de novo, com o papel já apagado, não dá erro
        with connection.cursor() as cursor:
            for comando in views_sql.sql_de_revogacao(self.cfg['NAME'], papel, apagar_schema=False):
                cursor.execute(comando)

    def test_exportacao_de_ponta_a_ponta_pelo_papel_de_leitura(self):
        """O exportador, conectado como o papel de leitura, lê os dados confirmados do banco pelas views."""
        vida = Locatario.objects.create(razao_social='Clínica Vida', cnpj='11.111.111/0001-11')
        unidade = Unidade.objects.create(nome='Sala E2E', tipo='sala', andar='9º andar', area_privativa=D('42.50'), ativa=True)
        Contrato.objects.create(unidade=unidade, locatario=vida, status='vigente', data_inicio=date(2026, 1, 1),
                                valor_aluguel=D('1234.56'), dia_vencimento=7)
        Cobranca.objects.create(unidade=unidade, locatario=vida, tipo='aluguel', referencia=date(2026, 10, 1), valor=D('1234.56'),
                                vencimento=timezone.localdate() - timedelta(days=4))
        fonte = FontePostgres(host=self.cfg['HOST'], port=self.cfg['PORT'] or 5432, dbname=self.cfg['NAME'],
                              user=self.papel, password=self.senha)
        try:
            arquivos, conteudos = montar_arquivos(fonte)
            self.assertTrue(all(a['status'] == 'ok' for a in arquivos), [a for a in arquivos if a['status'] != 'ok'])
            with self.assertRaises(psycopg.Error):  # a própria conexão do exportador é somente leitura
                fonte.conn.execute("INSERT INTO public.cadastros_locatario (razao_social) VALUES ('x')")
        finally:
            fonte.fechar()
        por_arquivo = {a['arquivo']: a for a in arquivos}
        inadimplencia = conteudos['vw_inadimplencia.csv'].decode('utf-8')
        self.assertIn('Clínica Vida,Sala E2E,2026-10,', inadimplencia)  # UTF-8 e competência AAAA-MM
        self.assertIn(',1234.56,4,aluguel', inadimplencia)              # ponto decimal, sem milhar, 4 dias de atraso
        self.assertEqual(por_arquivo['vw_inadimplencia.csv']['linhas'], 1)
        self.assertIn('Sala E2E,9º andar,42.50,ocupada,true,Clínica Vida,1234.56,2026-01-01,', conteudos['vw_ocupacao.csv'].decode())
        self.assertIn('11.111.111/0001-11', conteudos['vw_contratos.csv'].decode())

    def test_senha_fraca_ou_nome_estranho_sao_recusados_antes_de_virar_sql(self):
        with self.assertRaises(ValueError):
            views_sql.sql_do_papel('uruz', 'curta')
        with self.assertRaises(ValueError):
            views_sql.sql_do_papel('uruz', "aaaaaaaaaaaaaaaa'; DROP TABLE x; --")
        with self.assertRaises(ValueError):
            views_sql.sql_do_papel('uruz; DROP DATABASE x', 'a' * 20)
        with self.assertRaises(ValueError):
            views_sql.sql_de_revogacao('uruz', 'hermes_ro; DROP ROLE postgres')
