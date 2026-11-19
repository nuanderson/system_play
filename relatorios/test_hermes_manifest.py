"""O manifest tem que dizer, sozinho, como ler os arquivos: convenções, competência e views vazias."""

import json
import zipfile
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from relatorios.hermes import exportador, views_sql
from relatorios.test_hermes import DADOS, FonteFalsa

MOMENTO = datetime(2026, 11, 19, 6, 10, 3, tzinfo=ZoneInfo('America/Sao_Paulo'))


class ConvencoesNoManifestTests(SimpleTestCase):
    def manifest(self, **kw):
        arquivos, _ = exportador.montar_arquivos(FonteFalsa(**kw))
        return exportador.montar_manifest(arquivos, dict(destino='somente_servidor'), MOMENTO)

    def test_o_manifest_traz_as_convencoes_de_leitura(self):
        c = self.manifest()['convencoes']
        self.assertEqual(c['csv']['encoding'], 'UTF-8, sem BOM')
        self.assertEqual(c['csv']['separador_de_campos'], ',')
        self.assertIn('primeira linha', c['csv']['cabecalho'])
        self.assertIn('AAAA-MM-DD', c['valores']['data'])
        self.assertIn('ISO 8601', c['valores']['data_e_hora'])
        self.assertIn('ponto', c['valores']['decimal'])
        self.assertIn('sem separador de milhar', c['valores']['decimal'])
        self.assertEqual((c['valores']['nulo'], c['valores']['booleano']), ('campo vazio', 'true ou false'))
        self.assertEqual(c['fuso'], 'America/Sao_Paulo')

    def test_competencia_diz_que_e_mes_de_referencia_e_nao_vencimento_nem_contabil(self):
        texto = self.manifest()['convencoes']['competencia']
        self.assertIn('MÊS DE REFERÊNCIA', texto)
        self.assertIn('NÃO é o mês do vencimento', texto)
        self.assertIn('NÃO é competência contábil', texto)
        self.assertIn('Aluguel', texto)
        self.assertIn('Condomínio', texto)

    def test_as_convencoes_do_manifest_batem_com_o_que_o_csv_faz_de_verdade(self):
        """Evita o manifest prometer um formato que o exportador não entrega."""
        csv = exportador.para_csv(['d', 'v', 'b', 'n'], [(datetime(2026, 11, 10).date(), Decimal('1234567.8'), True, None)])
        self.assertEqual(csv.decode('utf-8'), 'd,v,b,n\n2026-11-10,1234567.8,true,\n')
        self.assertNotIn(b'\r', csv)

    def test_as_convencoes_vao_dentro_do_zip(self):
        import os
        import tempfile
        pasta = tempfile.mkdtemp()
        arquivos, conteudos = exportador.montar_arquivos(FonteFalsa())
        caminho = exportador.gravar_zip(pasta, MOMENTO.date(), conteudos,
                                        exportador.montar_manifest(arquivos, dict(destino='somente_servidor'), MOMENTO))
        with zipfile.ZipFile(caminho) as z:
            m = json.loads(z.read('manifest.json'))
        self.assertIn('convencoes', m)
        self.assertIn('MÊS DE REFERÊNCIA', m['convencoes']['competencia'])
        os.remove(caminho)


class ViewVaziaTests(SimpleTestCase):
    def test_view_sem_linhas_e_marcada_como_vazia_com_aviso(self):
        arquivos, _ = exportador.montar_arquivos(FonteFalsa())
        vazia = next(a for a in arquivos if a['view'] == 'vw_inadimplencia')  # o DADOS de teste a deixa sem linhas
        self.assertEqual((vazia['status'], vazia['linhas'], vazia['vazia']), ('ok', 0, True))
        self.assertIn('não retornou nenhuma linha', vazia['aviso'])
        cheia = next(a for a in arquivos if a['view'] == 'vw_pagamentos')
        self.assertEqual((cheia['vazia'], cheia['aviso']), (False, None))

    def test_manifest_lista_as_views_vazias_nos_avisos_e_nao_nos_erros(self):
        arquivos, _ = exportador.montar_arquivos(FonteFalsa())
        m = exportador.montar_manifest(arquivos, {}, MOMENTO)
        self.assertEqual(len(m['avisos']), 1)
        self.assertTrue(m['avisos'][0].startswith('vw_inadimplencia:'))
        self.assertEqual(m['erros'], [])  # vazia não é erro, e o comando não sai com falha por isso

    def test_view_que_falhou_nao_e_chamada_de_vazia(self):
        arquivos, _ = exportador.montar_arquivos(FonteFalsa(quebradas=['vw_contratos']))
        ruim = next(a for a in arquivos if a['view'] == 'vw_contratos')
        self.assertEqual((ruim['status'], ruim['vazia'], ruim['aviso']), ('erro', False, None))

    def test_o_csv_de_view_vazia_continua_com_o_cabecalho(self):
        _, conteudos = exportador.montar_arquivos(FonteFalsa())
        self.assertEqual(conteudos['vw_inadimplencia.csv'], b'locatario,dias_atraso\n')

    def test_cada_arquivo_traz_a_descricao_da_view(self):
        arquivos, _ = exportador.montar_arquivos(FonteFalsa())
        for a in arquivos:
            self.assertEqual(a['descricao'], views_sql.VIEWS[a['view']]['descricao'])


class BaseDaAreaTests(SimpleTestCase):
    def test_base_area_esta_documentada_e_e_a_ultima_coluna_de_ocupacao(self):
        colunas = views_sql.VIEWS['vw_ocupacao']['colunas']
        self.assertEqual(colunas[-1][0], 'base_area')
        self.assertIn("'privativa' ou 'rateio'", colunas[-1][2])
        self.assertIn('R$/m²', colunas[-1][2])

    def test_o_sql_decide_a_base_linha_a_linha(self):
        sql = views_sql.VIEWS['vw_ocupacao']['sql']
        self.assertIn("WHEN u.area_privativa IS NOT NULL THEN 'privativa'", sql)
        self.assertIn("WHEN u.area_rateio IS NOT NULL THEN 'rateio'", sql)
        self.assertIn('COALESCE(u.area_privativa, u.area_rateio) AS area_m2', sql)  # mesma regra do COALESCE, sem divergir
