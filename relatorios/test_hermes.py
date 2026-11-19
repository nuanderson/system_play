"""Exportação para o agente de relatórios: formato dos CSVs, manifest, zip, retenção, Drive e o comando."""

import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import SimpleTestCase

from relatorios.hermes import drive, exportador, views_sql

D = Decimal
DADOS = {
    'vw_pagamentos': (['id', 'locatario', 'valor_devido', 'data_vencimento'],
                      [(1, 'Clínica Vida, Ltda', D('10091.00'), date(2026, 11, 10)),
                       (2, 'Outra "Empresa"', D('5000'), date(2026, 11, 10))]),
    'vw_ocupacao': (['sala', 'tem_contrato_ativo'], [('Sala 01', True), ('Sala 02', False), ('Sala 03', None)]),
    'vw_contratos': (['id', 'documento'], [(7, '12.345.678/0001-90')]),
    'vw_inadimplencia': (['locatario', 'dias_atraso'], []),
    'vw_receita_mensal': (['competencia', 'previsto'], [('2026-10', D('1000.50'))]),
}


class FonteFalsa:
    def __init__(self, dados=DADOS, quebradas=()):
        self.dados, self.quebradas, self.consultadas = dados, set(quebradas), []

    def consultar(self, view):
        self.consultadas.append(view)
        if view in self.quebradas:
            raise RuntimeError('relation does not exist')
        return self.dados[view]


class FormatoTests(SimpleTestCase):
    def test_numeros_sem_milhar_e_com_ponto(self):
        self.assertEqual(exportador.formatar(D('1234567.80')), '1234567.80')
        self.assertEqual(exportador.formatar(D('0.5')), '0.5')
        self.assertEqual(exportador.formatar(D('1E+3')), '1000')  # nada de notação científica
        self.assertEqual(exportador.formatar(42), '42')

    def test_datas_em_iso_8601(self):
        self.assertEqual(exportador.formatar(date(2026, 11, 10)), '2026-11-10')
        self.assertEqual(exportador.formatar(datetime(2026, 11, 10, 15, 30, 5)), '2026-11-10T15:30:05')

    def test_data_e_hora_com_fuso_vai_para_sao_paulo(self):
        utc = datetime(2026, 11, 10, 18, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(exportador.formatar(utc), '2026-11-10T15:00:00-03:00')

    def test_nulo_e_booleano(self):
        self.assertEqual(exportador.formatar(None), '')
        self.assertEqual((exportador.formatar(True), exportador.formatar(False)), ('true', 'false'))

    def test_csv_cabecalho_virgula_utf8_e_aspas(self):
        dados = exportador.para_csv(['id', 'locatario', 'valor'], [(1, 'Clínica, "Vida"', D('10.50')), (2, None, D('0'))])
        texto = dados.decode('utf-8')
        self.assertFalse(dados.startswith(b'\xef\xbb\xbf'))  # sem BOM
        self.assertEqual(texto, 'id,locatario,valor\n1,"Clínica, ""Vida""",10.50\n2,,0\n')

    def test_csv_com_quebra_de_linha_no_texto_volta_igual(self):
        import csv
        dados = exportador.para_csv(['t'], [('linha 1\nlinha 2',)])
        self.assertEqual(list(csv.reader(io.StringIO(dados.decode())))[1], ['linha 1\nlinha 2'])

    def test_csv_vazio_tem_so_o_cabecalho(self):
        self.assertEqual(exportador.para_csv(['a', 'b'], []), b'a,b\n')


class ArquivosETestManifest(SimpleTestCase):
    def test_um_csv_por_view_com_contagem_e_sha256(self):
        arquivos, conteudos = exportador.montar_arquivos(FonteFalsa())
        self.assertEqual([a['view'] for a in arquivos], views_sql.NOMES)
        for a in arquivos:
            self.assertEqual(a['status'], 'ok')
            self.assertEqual(a['sha256'], hashlib.sha256(conteudos[a['arquivo']]).hexdigest())
        por_view = {a['view']: a['linhas'] for a in arquivos}
        self.assertEqual(por_view, {'vw_pagamentos': 2, 'vw_ocupacao': 3, 'vw_contratos': 1,
                                    'vw_inadimplencia': 0, 'vw_receita_mensal': 1})

    def test_uma_view_que_falha_entra_no_manifest_e_as_outras_seguem(self):
        fonte = FonteFalsa(quebradas=['vw_contratos'])
        arquivos, conteudos = exportador.montar_arquivos(fonte)
        self.assertEqual(fonte.consultadas, views_sql.NOMES)  # tentou todas
        ruim = next(a for a in arquivos if a['view'] == 'vw_contratos')
        self.assertEqual((ruim['status'], ruim['sha256'], ruim['linhas']), ('erro', None, 0))
        self.assertIn('relation does not exist', ruim['erro'])
        self.assertEqual(len(conteudos), 4)
        self.assertNotIn('vw_contratos.csv', conteudos)

    def test_manifest_tem_o_que_foi_pedido(self):
        arquivos, _ = exportador.montar_arquivos(FonteFalsa(quebradas=['vw_ocupacao']))
        momento = datetime(2026, 11, 19, 6, 10, 3, tzinfo=ZoneInfo('America/Sao_Paulo'))
        m = exportador.montar_manifest(arquivos, dict(destino='google_drive'), momento)
        self.assertEqual(m['gerado_em'], '2026-11-19T06:10:03-03:00')
        self.assertEqual((m['fuso'], m['versao_schema'], m['data']), ('America/Sao_Paulo', views_sql.VERSAO_SCHEMA, '2026-11-19'))
        self.assertEqual(len(m['arquivos']), 5)
        self.assertEqual(len(m['erros']), 1)
        self.assertTrue(m['erros'][0].startswith('vw_ocupacao:'))
        self.assertEqual(m['entrega'], dict(destino='google_drive'))

    def test_a_view_desconhecida_nunca_vira_sql(self):
        from relatorios.hermes.exportador import FontePostgres
        fonte = FontePostgres.__new__(FontePostgres)
        fonte.conn = mock.Mock()
        with self.assertRaises(ValueError):
            fonte.consultar('vw_pagamentos; DROP TABLE x')
        fonte.conn.cursor.assert_not_called()


class ZipETests(SimpleTestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.pasta, ignore_errors=True))

    def gerar(self, dia=date(2026, 11, 19), **kw):
        arquivos, conteudos = exportador.montar_arquivos(FonteFalsa(**kw))
        manifest = exportador.montar_manifest(arquivos, dict(destino='somente_servidor'),
                                              datetime(2026, 11, 19, 6, 10, tzinfo=ZoneInfo('America/Sao_Paulo')))
        return exportador.gravar_zip(self.pasta, dia, conteudos, manifest), conteudos

    def test_nome_e_conteudo_do_zip(self):
        caminho, conteudos = self.gerar()
        self.assertEqual(os.path.basename(caminho), 'uruz_export_2026-11-19.zip')
        with zipfile.ZipFile(caminho) as z:
            self.assertEqual(sorted(z.namelist()), sorted([f'{v}.csv' for v in views_sql.NOMES] + ['manifest.json']))
            self.assertIsNone(z.testzip())
            manifest = json.loads(z.read('manifest.json'))
            for a in manifest['arquivos']:  # o sha256 do manifest bate com o CSV que está dentro do zip
                self.assertEqual(hashlib.sha256(z.read(a['arquivo'])).hexdigest(), a['sha256'])
                self.assertEqual(z.read(a['arquivo']).decode().count('\n') - 1, a['linhas'])

    def test_rodar_de_novo_no_mesmo_dia_substitui_sem_sobras(self):
        self.gerar()
        caminho, _ = self.gerar()
        self.assertEqual(sorted(os.listdir(self.pasta)), ['uruz_export_2026-11-19.zip'])  # sem .tmp e sem duplicata
        self.assertTrue(zipfile.is_zipfile(caminho))

    def test_zip_com_view_quebrada_continua_valido_e_registra_o_erro(self):
        caminho, _ = self.gerar(quebradas=['vw_pagamentos'])
        with zipfile.ZipFile(caminho) as z:
            manifest = json.loads(z.read('manifest.json'))
            self.assertNotIn('vw_pagamentos.csv', z.namelist())
        self.assertEqual(len(manifest['erros']), 1)

    def test_retencao_apaga_so_o_que_passou_de_60_dias_pelo_nome(self):
        hoje = date(2026, 11, 19)
        for dias in (0, 30, 59, 60, 61, 90):
            open(os.path.join(self.pasta, exportador.nome_do_zip(hoje - timedelta(days=dias))), 'wb').close()
        open(os.path.join(self.pasta, 'outro_arquivo.zip'), 'wb').close()
        open(os.path.join(self.pasta, 'uruz_export_2026-01-01.zip.tmp'), 'wb').close()
        apagados = exportador.limpar_antigos(self.pasta, hoje, 60)
        self.assertEqual(apagados, ['uruz_export_2026-08-21.zip', 'uruz_export_2026-09-19.zip'])  # 90 e 61 dias
        restantes = sorted(os.listdir(self.pasta))
        self.assertIn(exportador.nome_do_zip(hoje - timedelta(days=60)), restantes)  # 60 dias exatos ainda fica
        self.assertIn('outro_arquivo.zip', restantes)
        self.assertIn('uruz_export_2026-01-01.zip.tmp', restantes)

    def test_retencao_nao_usa_a_data_de_modificacao(self):
        antigo = os.path.join(self.pasta, exportador.nome_do_zip(date(2026, 11, 18)))
        open(antigo, 'wb').close()
        os.utime(antigo, (0, 0))  # arquivo "modificado em 1970"
        self.assertEqual(exportador.limpar_antigos(self.pasta, date(2026, 11, 19), 60), [])

    def test_retencao_em_pasta_que_nao_existe(self):
        self.assertEqual(exportador.limpar_antigos(os.path.join(self.pasta, 'nada'), date(2026, 11, 19)), [])


class RespostaFalsa:
    def __init__(self, status=200, corpo=None, texto=''):
        self.status_code, self._corpo, self.text = status, corpo or {}, texto

    def json(self):
        return self._corpo


class SessaoFalsa:
    """Guarda as chamadas e devolve respostas prontas, em ordem, para cada método."""

    def __init__(self, **respostas):
        self.respostas = {k: list(v) for k, v in respostas.items()}
        self.chamadas = []

    def _responder(self, metodo, url, **kw):
        self.chamadas.append((metodo, url, kw))
        return self.respostas[metodo].pop(0)

    def get(self, url, **kw):
        return self._responder('get', url, **kw)

    def post(self, url, **kw):
        return self._responder('post', url, **kw)

    def patch(self, url, **kw):
        return self._responder('patch', url, **kw)

    def delete(self, url, **kw):
        return self._responder('delete', url, **kw)


class ComandoTests(SimpleTestCase):
    def setUp(self):
        self.pasta = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.pasta, ignore_errors=True))
        self.amb = mock.patch.dict(os.environ, {'HERMES_RO_PASSWORD': 'senha-de-teste'}, clear=False)
        self.amb.start()
        self.addCleanup(self.amb.stop)
        os.environ.pop('GOOGLE_SA_JSON', None)

    def rodar(self, *args, fonte=None):
        fonte = fonte or FonteFalsa()
        fonte.fechar = lambda: None
        with mock.patch('relatorios.management.commands.exportar_hermes.exportador.FontePostgres', return_value=fonte):
            call_command('exportar_hermes', '--saida', self.pasta, *args, stdout=io.StringIO())
        return fonte

    def test_gera_o_zip_do_dia_sem_drive_quando_nao_ha_chave(self):
        self.rodar()
        nomes = os.listdir(self.pasta)
        self.assertEqual(len(nomes), 1)
        self.assertRegex(nomes[0], r'^uruz_export_\d{4}-\d{2}-\d{2}\.zip$')
        with zipfile.ZipFile(os.path.join(self.pasta, nomes[0])) as z:
            self.assertEqual(json.loads(z.read('manifest.json'))['entrega']['destino'], 'somente_servidor')

    def test_view_quebrada_gera_o_zip_mesmo_assim_e_sai_com_erro(self):
        with self.assertRaises(SystemExit) as saiu:
            self.rodar(fonte=FonteFalsa(quebradas=['vw_ocupacao']))
        self.assertEqual(saiu.exception.code, 1)
        zips = os.listdir(self.pasta)
        self.assertEqual(len(zips), 1)
        with zipfile.ZipFile(os.path.join(self.pasta, zips[0])) as z:
            self.assertEqual(len(json.loads(z.read('manifest.json'))['erros']), 1)
            self.assertIn('vw_pagamentos.csv', z.namelist())  # as outras seguiram

    def test_sem_senha_do_papel_nao_gera_nada_e_sai_com_erro(self):
        with mock.patch.dict(os.environ, {'HERMES_RO_PASSWORD': ''}):
            with self.assertRaises(SystemExit) as saiu:
                call_command('exportar_hermes', '--saida', self.pasta, stdout=io.StringIO())
        self.assertEqual(saiu.exception.code, 1)
        self.assertEqual(os.listdir(self.pasta), [])

    def chave(self):
        caminho = os.path.join(self.pasta, 'chave.json')
        open(caminho, 'w').write('{}')
        return caminho

    def test_chave_configurada_mas_sem_arquivo_nao_tenta_o_drive_nem_da_erro(self):
        with mock.patch.dict(os.environ, {'GOOGLE_SA_JSON': '/secrets/nao-existe.json'}):
            with mock.patch('relatorios.management.commands.exportar_hermes.drive_mod.Drive') as Drive:
                self.rodar()  # não levanta SystemExit
        Drive.assert_not_called()
        self.assertEqual(len([n for n in os.listdir(self.pasta) if n.endswith('.zip')]), 1)

    def test_com_chave_do_google_envia_ao_drive_e_aplica_a_retencao(self):
        chave = self.chave()
        with mock.patch.dict(os.environ, {'GOOGLE_SA_JSON': chave, 'GOOGLE_SHARED_DRIVE_ID': 'DRIVE123'}):
            with mock.patch('relatorios.management.commands.exportar_hermes.drive_mod.Drive') as Drive:
                Drive.return_value.enviar.return_value = dict(id='ARQ1', nome='x.zip', acao='criado', bytes=10)
                Drive.return_value.limpar.return_value = []
                self.rodar()
        Drive.assert_called_once_with(chave, 'DRIVE123')
        enviado = Drive.return_value.enviar.call_args[0]
        self.assertTrue(enviado[0].endswith('.zip') and enviado[1].startswith('uruz_export_'))
        Drive.return_value.limpar.assert_called_once()

    def test_falha_no_drive_deixa_o_zip_e_sai_com_erro(self):
        with mock.patch.dict(os.environ, {'GOOGLE_SA_JSON': self.chave(), 'GOOGLE_SHARED_DRIVE_ID': 'DRIVE123'}):
            with mock.patch('relatorios.management.commands.exportar_hermes.drive_mod.Drive') as Drive:
                Drive.return_value.enviar.side_effect = drive.DriveErro('Drive respondeu 403')
                with self.assertRaises(SystemExit) as saiu:
                    self.rodar()
        self.assertEqual(saiu.exception.code, 1)
        self.assertEqual(len([n for n in os.listdir(self.pasta) if n.endswith('.zip')]), 1)  # o zip ficou no servidor

    def test_sem_entrega_nao_toca_no_drive(self):
        with mock.patch.dict(os.environ, {'GOOGLE_SA_JSON': self.chave()}):
            with mock.patch('relatorios.management.commands.exportar_hermes.drive_mod.Drive') as Drive:
                self.rodar('--sem-entrega')
        Drive.assert_not_called()

    def test_retencao_local_roda_junto(self):
        velho = os.path.join(self.pasta, 'uruz_export_2020-01-01.zip')
        open(velho, 'wb').close()
        self.rodar()
        self.assertFalse(os.path.exists(velho))

    def test_a_senha_nao_aparece_na_saida_nem_no_manifest(self):
        saida = io.StringIO()
        fonte = FonteFalsa()
        fonte.fechar = lambda: None
        with self.assertLogs('uruz.exportacao', level='INFO') as logs:
            with mock.patch('relatorios.management.commands.exportar_hermes.exportador.FontePostgres', return_value=fonte):
                call_command('exportar_hermes', '--saida', self.pasta, stdout=saida)
        self.assertNotIn('senha-de-teste', saida.getvalue() + '\n'.join(logs.output))
        zips = os.listdir(self.pasta)
        with zipfile.ZipFile(os.path.join(self.pasta, zips[0])) as z:
            self.assertNotIn(b'senha-de-teste', b''.join(z.read(n) for n in z.namelist()))


class ContratoDasViewsTests(SimpleTestCase):
    """O que as views expõem é um contrato: só leitura, sem dados pessoais além do combinado."""

    def sql_completo(self):
        return '\n'.join(views_sql.comandos_de_instalacao()).lower()

    def test_so_as_cinco_views_autorizadas(self):
        self.assertEqual(sorted(views_sql.NOMES),
                         sorted(['vw_pagamentos', 'vw_ocupacao', 'vw_contratos', 'vw_inadimplencia', 'vw_receita_mensal']))
        self.assertNotIn('vw_despesas', views_sql.VIEWS)
        self.assertNotIn('vw_manutencao', views_sql.VIEWS)

    def test_nao_le_tabelas_de_despesas_chamados_nem_usuarios(self):
        sql = self.sql_completo()
        for tabela in ('financeiro_despesa', 'chamados_', 'core_usuario', 'correspondencias_', 'contatos_', 'django_session',
                       'cadastros_pessoaempresa', 'auth_'):
            self.assertNotIn(tabela, sql, tabela)

    def test_nao_expoe_email_telefone_nem_texto_livre(self):
        for colunas in (v['colunas'] for v in views_sql.VIEWS.values()):
            for nome, _t, _d in colunas:
                for proibida in ('email', 'telefone', 'contato', 'observ', 'descricao', 'senha', 'password', 'boleto'):
                    self.assertNotIn(proibida, nome)
        sql = self.sql_completo()
        for proibido in ('email_cobranca', 'telefone', 'observacoes', 'pix', 'boleto'):
            self.assertNotIn(proibido, sql)

    def test_o_sql_so_cria_views_nunca_escreve_em_tabela_do_sistema(self):
        for comando in views_sql.comandos_de_instalacao():
            inicio = comando.strip().split()[0].upper()
            self.assertIn(inicio, {'CREATE', 'DROP', 'COMMENT'})
            if inicio == 'DROP':
                self.assertIn('DROP VIEW IF EXISTS relatorios.', comando)  # só derruba views do próprio schema
            if inicio == 'CREATE':
                self.assertTrue(comando.startswith(('CREATE SCHEMA IF NOT EXISTS relatorios', 'CREATE VIEW relatorios.')))
        sql = self.sql_completo()
        for perigoso in ('insert into', 'update ', 'delete from', 'truncate', 'drop table', 'alter table', 'grant all'):
            self.assertNotIn(perigoso, sql)

    def test_toda_coluna_tem_descricao_e_a_ordem_pedida(self):
        for nome, v in views_sql.VIEWS.items():
            self.assertTrue(v['descricao'], nome)
            self.assertTrue(all(d for _c, _t, d in v['colunas']), nome)
        pedidas = {
            'vw_ocupacao': ['sala', 'andar', 'area_m2', 'status_atual', 'tem_contrato_ativo', 'locatario_atual',
                            'valor_mensal_atual', 'inicio_contrato', 'fim_contrato'],
            'vw_contratos': ['id', 'locatario', 'documento', 'sala', 'data_inicio', 'data_fim', 'valor_mensal',
                             'dia_vencimento', 'indice_reajuste', 'status'],
            'vw_pagamentos': ['id', 'contrato_id', 'locatario', 'competencia', 'data_vencimento', 'valor_devido',
                              'data_pagamento', 'valor_pago', 'status'],
            'vw_inadimplencia': ['locatario', 'sala', 'competencia', 'data_vencimento', 'valor_devido', 'dias_atraso'],
            'vw_receita_mensal': ['competencia', 'previsto', 'recebido', 'em_aberto'],
        }
        for nome, esperadas in pedidas.items():
            reais = [c for c, _t, _d in views_sql.VIEWS[nome]['colunas']]
            self.assertEqual(reais[:len(esperadas)], esperadas, nome)  # as pedidas vêm primeiro, na ordem; extras no fim
