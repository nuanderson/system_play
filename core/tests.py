from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from contratos.models import Contrato
from financeiro.models import Cobranca

from .models import Usuario
from .views import ALTO, alertas, plural, resumos


class PluralTests(TestCase):
    def test_singular_e_plural(self):
        self.assertEqual(plural(1, 'contrato', 'contratos'), '1 contrato')
        self.assertEqual(plural(0, 'contrato', 'contratos'), '0 contratos')
        self.assertEqual(plural(3, 'contrato', 'contratos'), '3 contratos')


class PaginaInicialTests(TestCase):
    def setUp(self):
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.equipe = Usuario.objects.create_user('equipe', password='x', perfil=Usuario.Perfil.EQUIPE)
        self.resp = Usuario.objects.create_user('resp', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala1)
        self.membro = Usuario.objects.create_user('membro', password='x', perfil=Usuario.Perfil.MEMBRO, unidade=self.sala1)
        self.locatario = Locatario.objects.create(razao_social='Empresa A')

    def cobranca_atrasada(self, unidade, valor='100.00', dias=5):
        vencimento = timezone.localdate() - timedelta(days=dias)
        return Cobranca.objects.create(
            unidade=unidade, locatario=self.locatario, tipo='aluguel', referencia=vencimento.replace(day=1),
            valor=Decimal(valor), vencimento=vencimento)

    def test_sem_pendencias_mostra_tudo_em_dia(self):
        self.assertEqual(alertas(self.sindico), [])
        self.client.force_login(self.sindico)
        self.assertContains(self.client.get(reverse('inicio')), 'Tudo em dia')

    def test_alerta_de_cobranca_atrasada_com_valor_e_link(self):
        self.cobranca_atrasada(self.sala1, '150.00')
        self.cobranca_atrasada(Unidade.objects.get(nome='Sala 02'), '50.50')
        lista = alertas(self.sindico)
        self.assertEqual(lista[0]['nivel'], ALTO)
        self.assertEqual(lista[0]['texto'], '2 cobranças atrasadas · R$ 200,50')
        self.assertEqual(lista[0]['url'], reverse('cobranca_lista') + '?situacao=atrasada')

    def test_alertas_de_contrato_separam_vencido_de_a_vencer(self):
        hoje = timezone.localdate()
        base = dict(locatario=self.locatario, status='vigente', data_inicio=date(2025, 1, 1), valor_aluguel=Decimal('1'))
        Contrato.objects.create(unidade=self.sala1, data_fim=hoje - timedelta(days=2), **base)
        Contrato.objects.create(unidade=Unidade.objects.get(nome='Sala 02'), data_fim=hoje + timedelta(days=30), **base)
        textos = [a['texto'] for a in alertas(self.sindico)]
        self.assertIn('1 contrato vencido', textos)
        self.assertIn('1 contrato vence em até 90 dias', textos)

    def test_equipe_so_ve_alertas_da_operacao(self):
        self.cobranca_atrasada(self.sala1)
        Chamado.objects.create(unidade=self.sala1, solicitante=self.resp, titulo='x', descricao='y', prioridade=4)
        textos = [a['texto'] for a in alertas(self.equipe)]
        self.assertEqual(textos, ['1 chamado urgente em aberto'])

    def test_responsavel_ve_atraso_so_da_propria_unidade_e_membro_nao_ve_cobranca(self):
        self.cobranca_atrasada(self.sala1, '80.00')
        self.cobranca_atrasada(Unidade.objects.get(nome='Sala 02'), '999.00')
        self.assertEqual([a['texto'] for a in alertas(self.resp)], ['1 cobrança atrasada · R$ 80,00'])
        self.assertEqual(alertas(self.membro), [])

    def test_resumos_dos_modulos(self):
        self.cobranca_atrasada(self.sala1, '120.00')
        dados = resumos(self.sindico)
        self.assertEqual(dados['Cobranças'], 'R$ 120,00 em aberto')
        self.assertEqual(dados['Contratos'], '0 vigentes')
        self.assertEqual(dados['Condomínio e rateio'], 'Nenhum mês importado')
        self.assertEqual(dados['Unidades'], '11 unidades · 0 com locatário')
        self.assertEqual(dados['Chamados de manutenção'], '0 em aberto')

    def test_resumo_do_locatario_nao_vaza_dados_do_predio(self):
        Chamado.objects.create(unidade=Unidade.objects.get(nome='Sala 02'), solicitante=self.sindico, titulo='x', descricao='y')
        dados = resumos(self.resp)
        self.assertEqual(dados['Chamados de manutenção'], '0 em aberto')
        self.assertNotIn('Unidades', dados)
        self.assertNotIn('Condomínio e rateio', dados)

    def test_os_dez_cartoes_soltos_nao_existem_mais(self):
        self.client.force_login(self.sindico)
        resposta = self.client.get(reverse('inicio'))
        self.assertNotContains(resposta, 'class="indicadores"')
        self.assertContains(resposta, 'Precisa de atenção')
