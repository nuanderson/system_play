from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from contratos.models import Contrato
from core.models import Usuario
from correspondencias.models import Correspondencia
from financeiro.calculo import calcular_rateio
from financeiro.models import Cobranca, Competencia, Despesa

from . import dados

D = Decimal
TODAS = ['relatorios', 'relatorio_despesas', 'relatorio_rateio', 'relatorio_inadimplencia',
         'relatorio_chamados', 'relatorio_contratos', 'relatorio_correspondencias']


class Base(TestCase):
    def setUp(self):
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.equipe = Usuario.objects.create_user('equipe', password='x', perfil=Usuario.Perfil.EQUIPE)
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.resp = Usuario.objects.create_user('resp', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala1)

    def contrato(self, unidade, empresa, **extra):
        locatario = Locatario.objects.create(razao_social=empresa)
        dados_contrato = dict(unidade=unidade, locatario=locatario, status='vigente', data_inicio=date(2026, 1, 1),
                              valor_aluguel=D('1000.00'))
        dados_contrato.update(extra)
        return Contrato.objects.create(**dados_contrato)

    def despesa(self, comp, valor, tipo='demais', categoria='Manutenção Predial', incluir=True, multa='0', chave=None):
        return Despesa.objects.create(
            competencia=comp, descricao='x', valor=D(valor), tipo=tipo, categoria=categoria, multa_juros=D(multa),
            vencimento=comp.referencia, entra_no_rateio=incluir, motivo_exclusao='' if incluir else 'Play',
            chave=chave or f'{valor}{tipo}{categoria}{incluir}')


class PermissaoTests(Base):
    def test_so_o_sindico_acessa(self):
        for nome in TODAS:
            self.client.logout()
            self.assertEqual(self.client.get(reverse(nome)).status_code, 302, nome)
            for usuario in (self.equipe, self.resp):
                self.client.force_login(usuario)
                self.assertEqual(self.client.get(reverse(nome)).status_code, 403, nome)
            self.client.force_login(self.sindico)
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)

    def test_csv_tambem_e_so_do_sindico(self):
        self.client.force_login(self.equipe)
        self.assertEqual(self.client.get(reverse('relatorio_chamados') + '?formato=csv').status_code, 403)

    def test_home_do_sindico_tem_o_cartao_de_relatorios(self):
        self.client.force_login(self.sindico)
        self.assertContains(self.client.get(reverse('inicio')), reverse('relatorios'))

    def test_todas_as_telas_abrem_vazias_e_o_csv_tambem(self):
        self.client.force_login(self.sindico)
        for nome in TODAS[1:]:
            self.assertEqual(self.client.get(reverse(nome) + '?formato=csv').status_code, 200, nome)


class DespesasTests(Base):
    def test_totais_por_tipo_e_categoria(self):
        comp = Competencia.objects.create(referencia=date(2026, 9, 1))
        self.despesa(comp, '1000.00', 'energia', 'Energia Elétrica')
        self.despesa(comp, '300.00', 'demais', 'Manutenção Predial', chave='a')
        self.despesa(comp, '100.00', 'demais', 'Pessoal', chave='b')
        self.despesa(comp, '50.00', 'demais', 'Manutenção Predial', incluir=False, multa='5.50', chave='c')
        r = dados.despesas(comp)
        self.assertEqual(r['total'], D('1400.00'))
        self.assertEqual(r['por_tipo']['energia'], D('1000.00'))
        self.assertEqual(r['total_excluidas'], D('50.00'))
        self.assertEqual(r['multas'], D('5.50'))
        self.assertEqual(r['categorias'][0][0], 'Energia Elétrica')
        self.assertEqual(round(r['categorias'][0][2], 2), D('71.43'))
        self.assertEqual(sum(c[1] for c in r['categorias']), D('1400.00'))

    def test_evolucao_em_ordem_cronologica(self):
        for mes, valor in ((7, '100'), (8, '300'), (9, '200')):
            comp = Competencia.objects.create(referencia=date(2026, mes, 1))
            self.despesa(comp, valor)
        evolucao = dados.evolucao_despesas()
        self.assertEqual([e['total'] for e in evolucao], [D('100'), D('300'), D('200')])
        self.assertEqual(evolucao[1]['barra'], 100)

    def test_tela_e_csv_de_despesas(self):
        comp = Competencia.objects.create(referencia=date(2026, 9, 1))
        self.despesa(comp, '1234.56', 'energia', 'Energia Elétrica')
        self.client.force_login(self.sindico)
        self.assertContains(self.client.get(reverse('relatorio_despesas')), '1.234,56')
        resposta = self.client.get(reverse('relatorio_despesas') + f'?competencia={comp.pk}&formato=csv')
        self.assertEqual(resposta['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('despesas-2026-09.csv', resposta['Content-Disposition'])
        texto = resposta.content.decode('utf-8')
        self.assertTrue(texto.startswith('﻿'))
        self.assertIn('1234,56', texto)
        self.assertIn(';', texto.splitlines()[0])


class RateioTests(Base):
    def test_relatorio_do_rateio_fecha_com_o_total(self):
        self.contrato(Unidade.objects.get(nome='Térreo (clínica)'), 'Clínica')
        self.contrato(self.sala1, 'Empresa 1')
        comp = Competencia.objects.create(referencia=date(2026, 9, 1))
        self.despesa(comp, '1000.00', 'demais')
        calcular_rateio(comp)
        r = dados.rateio(comp)
        self.assertEqual(r['total'], D('1000.00'))
        self.assertAlmostEqual(sum(l['percentual'] for l in r['linhas']), D('100'), places=1)
        self.assertGreater(r['sem_contrato'], 0)  # as demais unidades ficam com a Play

    def test_sem_rateio_calculado(self):
        comp = Competencia.objects.create(referencia=date(2026, 9, 1))
        self.assertIsNone(dados.rateio(comp))
        self.client.force_login(self.sindico)
        self.assertContains(self.client.get(reverse('relatorio_rateio')), 'ainda não tem rateio')


class InadimplenciaTests(Base):
    def cobranca(self, unidade, locatario, dias_atraso, valor='100.00', tipo='aluguel', status='aberta'):
        hoje = timezone.localdate()
        return Cobranca.objects.create(
            unidade=unidade, locatario=locatario, tipo=tipo, referencia=(hoje - timedelta(days=dias_atraso)).replace(day=1),
            valor=D(valor), vencimento=hoje - timedelta(days=dias_atraso), status=status,
            pago_em=hoje if status == 'paga' else None, valor_pago=D(valor) if status == 'paga' else None)

    def test_faixas_e_devedores(self):
        a = Locatario.objects.create(razao_social='Devedor A')
        b = Locatario.objects.create(razao_social='Devedor B')
        self.cobranca(self.sala1, a, 10, '100.00')
        self.cobranca(self.sala1, a, 45, '200.00', tipo='condominio')
        self.cobranca(self.sala2, b, 120, '50.00')
        self.cobranca(self.sala2, b, 5, '999.00', status='paga', tipo='condominio')
        self.cobranca(Unidade.objects.get(nome='Sala 03'), b, -5, '70.00', tipo='condominio')  # a vencer
        r = dados.inadimplencia()
        self.assertEqual(r['total'], D('350.00'))
        self.assertEqual(r['faixas']['1 a 30 dias']['total'], D('100.00'))
        self.assertEqual(r['faixas']['31 a 60 dias']['total'], D('200.00'))
        self.assertEqual(r['faixas']['Mais de 90 dias']['total'], D('50.00'))
        self.assertEqual(r['a_vencer'], D('70.00'))
        self.assertEqual([d['locatario'].razao_social for d in r['devedores']], ['Devedor A', 'Devedor B'])
        self.assertEqual(r['devedores'][0]['maior_atraso'], 45)

    def test_resumo_mensal(self):
        a = Locatario.objects.create(razao_social='A')
        self.cobranca(self.sala1, a, 0, '100.00', status='paga')
        self.cobranca(self.sala2, a, 40, '300.00')
        resumo = dados.resumo_mensal_cobrancas()
        self.assertEqual(sum(m['emitido'] for m in resumo), D('400.00'))
        self.assertEqual(sum(m['pago'] for m in resumo), D('100.00'))
        self.assertEqual(sum(m['atrasado'] for m in resumo), D('300.00'))

    def test_csv(self):
        a = Locatario.objects.create(razao_social='=SOMA(A1)')
        self.cobranca(self.sala1, a, 10, '100.50')
        self.client.force_login(self.sindico)
        texto = self.client.get(reverse('relatorio_inadimplencia') + '?formato=csv').content.decode('utf-8')
        self.assertIn("'=SOMA(A1)", texto)  # texto digitado não vira fórmula no Excel
        self.assertIn('100,50', texto)


class ChamadosTests(Base):
    def chamado(self, dias_atras, **extra):
        campos = dict(unidade=self.sala1, solicitante=self.resp, titulo='T', descricao='d')
        campos.update(extra)
        c = Chamado.objects.create(**campos)
        Chamado.objects.filter(pk=c.pk).update(aberto_em=timezone.now() - timedelta(days=dias_atras))
        c.refresh_from_db()
        return c

    def test_metricas_do_periodo(self):
        a = self.chamado(10, categoria='eletrica', custo=D('150.00'))
        b = self.chamado(8, categoria='eletrica', custo=D('50.00'))
        self.chamado(5, categoria='ar')
        self.chamado(200, categoria='ar')  # fora do período
        Chamado.objects.filter(pk=a.pk).update(status='resolvido', resolvido_em=a.aberto_em + timedelta(days=2))
        Chamado.objects.filter(pk=b.pk).update(status='resolvido', resolvido_em=b.aberto_em + timedelta(days=4))
        de, ate = dados.periodo_padrao(30)
        r = dados.chamados(de, ate)
        self.assertEqual((r['total'], r['resolvidos'], r['em_aberto']), (3, 2, 1))
        self.assertEqual(r['tempo_medio_dias'], 3.0)
        self.assertEqual(r['custo_total'], D('200.00'))
        self.assertEqual(dict(r['custo_categoria']), {'Elétrica': D('200.00')})
        self.assertEqual(dict(r['por_categoria'])['Elétrica'], 2)

    def test_filtro_de_datas_e_data_invalida_cai_no_padrao(self):
        self.chamado(5)
        self.client.force_login(self.sindico)
        hoje = timezone.localdate()
        url = reverse('relatorio_chamados')
        self.assertContains(self.client.get(url + f'?de={hoje}&ate={hoje}'), 'Nenhum chamado no período')
        self.assertEqual(self.client.get(url + '?de=lixo&ate=outra').status_code, 200)
        invertido = self.client.get(url + f'?de={hoje}&ate={hoje - timedelta(days=30)}')
        self.assertEqual(invertido.status_code, 200)

    def test_csv(self):
        self.chamado(3, titulo='Lâmpada', custo=D('12.30'))
        self.client.force_login(self.sindico)
        texto = self.client.get(reverse('relatorio_chamados') + '?formato=csv').content.decode('utf-8')
        self.assertIn('12,30', texto)
        self.assertIn('Aberto', texto)


class ContratosTests(Base):
    def test_receita_vencimentos_e_vagas(self):
        hoje = timezone.localdate()
        self.contrato(self.sala1, 'A', valor_aluguel=D('3000.00'), data_fim=hoje + timedelta(days=30))
        self.contrato(self.sala2, 'B', valor_aluguel=D('2000.50'), data_fim=hoje - timedelta(days=1))
        self.contrato(Unidade.objects.get(nome='Sala 03'), 'C', valor_aluguel=D('1000.00'), data_fim=hoje + timedelta(days=900))
        r = dados.contratos()
        self.assertEqual(r['receita_mensal'], D('6000.50'))
        self.assertEqual((len(r['a_vencer']), len(r['vencidos'])), (1, 1))
        self.assertEqual(len(r['vagas']), Unidade.objects.filter(ativa=True).count() - 3)
        self.assertEqual(r['vigentes'][0].locatario.razao_social, 'B')  # o que vence primeiro vem antes

    def test_csv(self):
        self.contrato(self.sala1, 'Empresa X', valor_aluguel=D('4500.00'), pct_energia=D('60'))
        self.client.force_login(self.sindico)
        texto = self.client.get(reverse('relatorio_contratos') + '?formato=csv').content.decode('utf-8')
        self.assertIn('4500,00', texto)
        self.assertIn('60,00', texto)


class CorrespondenciasTests(Base):
    def test_metricas_e_pendentes(self):
        agora = timezone.now()
        a = Correspondencia.objects.create(unidade=self.sala1, tipo='carta', recebido_em=agora - timedelta(days=5))
        Correspondencia.objects.filter(pk=a.pk).update(status='retirada', retirada_em=agora - timedelta(days=3), retirada_por='João')
        Correspondencia.objects.create(unidade=self.sala1, tipo='encomenda', recebido_em=agora - timedelta(days=9))
        Correspondencia.objects.create(unidade=self.sala2, tipo='encomenda', recebido_em=agora - timedelta(days=1))
        b = Correspondencia.objects.create(unidade=self.sala2, tipo='carta', recebido_em=agora - timedelta(days=2))
        Correspondencia.objects.filter(pk=b.pk).update(status='devolvida', retirada_em=agora)
        de, ate = dados.periodo_padrao(30)
        r = dados.correspondencias(de, ate)
        self.assertEqual((r['total'], r['retiradas'], r['devolvidas']), (4, 1, 1))
        self.assertEqual(r['tempo_medio_dias'], 2.0)
        self.assertEqual(r['pendentes_agora'].count(), 2)
        self.assertEqual(dict(r['por_unidade']), {'Sala 01': 2, 'Sala 02': 2})

    def test_csv(self):
        Correspondencia.objects.create(unidade=self.sala1, tipo='carta', destinatario='Maria', recebido_em=timezone.now())
        self.client.force_login(self.sindico)
        texto = self.client.get(reverse('relatorio_correspondencias') + '?formato=csv').content.decode('utf-8')
        self.assertIn('Maria', texto)
        self.assertIn('Aguardando retirada', texto)
