"""Totais de cobranças: em aberto, atrasado, recebido e quanto já entrou do que foi emitido."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from core.models import Usuario

from .models import Cobranca

D = Decimal


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TotaisTests(TestCase):
    def setUp(self):
        self.vida = Locatario.objects.create(razao_social='Clínica Vida')
        self.outra = Locatario.objects.create(razao_social='Outra Empresa')
        self.s1, self.s2, self.s3 = (Unidade.objects.get(nome=n) for n in ('Sala 01', 'Sala 02', 'Sala 03'))
        Unidade.objects.filter(pk=self.s1.pk).update(locatario=self.vida)
        Unidade.objects.filter(pk=self.s2.pk).update(locatario=self.vida)
        Unidade.objects.filter(pk=self.s3.pk).update(locatario=self.outra)
        hoje = timezone.localdate()
        self.atrasado, self.futuro = hoje - timedelta(days=5), hoje + timedelta(days=5)
        # vida: aluguel 1000 paga, condomínio 500 em aberto atrasado, aluguel 2000 em aberto no prazo, 300 cancelada
        # outra: aluguel 4000 paga com valor pago diferente (3900), aluguel 700 em aberto atrasado
        self.criar(self.s1, self.vida, 'aluguel', date(2026, 9, 1), '1000', self.atrasado, status='paga', valor_pago='1000')
        self.criar(self.s1, self.vida, 'condominio', date(2026, 9, 1), '500', self.atrasado)
        self.criar(self.s2, self.vida, 'aluguel', date(2026, 10, 1), '2000', self.futuro)
        self.criar(self.s2, self.vida, 'condominio', date(2026, 10, 1), '300', self.atrasado, status='cancelada')
        self.criar(self.s3, self.outra, 'aluguel', date(2026, 9, 1), '4000', self.atrasado, status='paga', valor_pago='3900')
        self.criar(self.s3, self.outra, 'condominio', date(2026, 10, 1), '700', self.atrasado)
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.vida)
        self.client.force_login(self.sindico)

    def criar(self, unidade, empresa, tipo, ref, valor, venc, status='aberta', valor_pago=None):
        return Cobranca.objects.create(
            unidade=unidade, locatario=empresa, tipo=tipo, referencia=ref, valor=D(valor), vencimento=venc,
            status=status, valor_pago=D(valor_pago) if valor_pago else None, pago_em=date(2026, 11, 1) if valor_pago else None)

    # --- o cálculo ---
    def test_totais_de_tudo(self):
        t = Cobranca.totais(Cobranca.objects.all())
        self.assertEqual(t['emitido'], D('8200'))      # 1000+500+2000+4000+700, sem a cancelada
        self.assertEqual(t['aberto'], D('3200'))       # 500+2000+700
        self.assertEqual(t['atrasado'], D('1200'))     # 500+700 (a de 2000 ainda está no prazo)
        self.assertEqual(t['recebido'], D('4900'))     # 1000 + 3900 (o que foi pago de fato)
        self.assertEqual((t['qtd_aberto'], t['qtd_pago']), (3, 2))
        self.assertEqual(t['percentual'], D('59.8'))   # 4900 / 8200

    def test_usa_o_valor_pago_e_nao_o_valor_da_cobranca(self):
        t = Cobranca.totais(Cobranca.objects.filter(locatario=self.outra))
        self.assertEqual(t['recebido'], D('3900'))

    def test_cancelada_nao_conta_em_nada(self):
        t = Cobranca.totais(Cobranca.objects.filter(status='cancelada'))
        self.assertEqual((t['emitido'], t['aberto'], t['recebido'], t['percentual']), (D('0'), D('0'), D('0'), D('0.0')))

    def test_sem_cobrancas_nao_divide_por_zero(self):
        t = Cobranca.totais(Cobranca.objects.none())
        self.assertEqual((t['emitido'], t['recebido'], t['percentual']), (D('0'), D('0'), D('0.0')))

    def test_dar_baixa_tira_do_aberto_e_poe_no_recebido(self):
        antes = Cobranca.totais(Cobranca.objects.all())
        pendente = Cobranca.objects.get(valor=D('500'), tipo='condominio', status='aberta')
        self.client.post(reverse('cobranca_pagar', args=[pendente.pk]), {'pago_em': '2026-11-13', 'valor_pago': '500.00'})
        depois = Cobranca.totais(Cobranca.objects.all())
        self.assertEqual(depois['aberto'], antes['aberto'] - D('500'))
        self.assertEqual(depois['atrasado'], antes['atrasado'] - D('500'))
        self.assertEqual(depois['recebido'], antes['recebido'] + D('500'))
        self.assertEqual(depois['emitido'], antes['emitido'])  # o emitido não muda

    # --- a tela de cobranças ---
    def test_quadro_da_lista_mostra_aberto_atrasado_e_recebido(self):
        pagina = self.client.get(reverse('cobranca_lista'))
        t = pagina.context['totais']
        self.assertEqual((t['aberto'], t['atrasado'], t['recebido']), (D('3200'), D('1200'), D('4900')))
        self.assertContains(pagina, 'Recebido')
        self.assertContains(pagina, '59,8%')
        self.assertContains(pagina, 'R$ 4.900,00')

    def test_filtro_de_situacao_nao_zera_o_quadro(self):
        """Olhando só as pagas, o quadro continua mostrando também o que ainda está em aberto."""
        pagina = self.client.get(reverse('cobranca_lista') + '?situacao=paga')
        self.assertEqual(len(pagina.context['cobrancas']), 2)
        self.assertEqual(pagina.context['totais']['aberto'], D('3200'))
        self.assertEqual(pagina.context['totais']['recebido'], D('4900'))

    def test_quadro_respeita_empresa_tipo_e_mes(self):
        t = self.client.get(reverse('cobranca_lista') + f'?empresa={self.outra.pk}').context['totais']
        self.assertEqual((t['aberto'], t['recebido'], t['emitido']), (D('700'), D('3900'), D('4700')))
        t = self.client.get(reverse('cobranca_lista') + '?tipo=condominio').context['totais']
        self.assertEqual((t['aberto'], t['recebido']), (D('1200'), D('0')))
        t = self.client.get(reverse('cobranca_lista') + '?mes=2026-09').context['totais']
        self.assertEqual(t['recebido'], D('4900'))
        self.assertEqual(t['aberto'], D('500'))  # só o condomínio de 09/2026 da Vida

    def test_a_empresa_so_ve_os_totais_dela(self):
        self.client.force_login(self.responsavel)
        t = self.client.get(reverse('cobranca_lista')).context['totais']
        self.assertEqual((t['emitido'], t['aberto'], t['recebido']), (D('3500'), D('2500'), D('1000')))
        self.assertNotContains(self.client.get(reverse('cobranca_lista')), '4.900,00')

    # --- página inicial ---
    def test_pagina_inicial_mostra_em_aberto_e_recebido(self):
        pagina = self.client.get(reverse('inicio'))
        self.assertContains(pagina, 'R$ 3.200,00 em aberto · R$ 4.900,00 recebido')

    def test_pagina_inicial_sem_recebimentos_mostra_so_o_aberto(self):
        Cobranca.objects.filter(status='paga').update(status='aberta', valor_pago=None, pago_em=None)
        pagina = self.client.get(reverse('inicio'))
        self.assertNotContains(pagina, 'recebido')
        self.assertContains(pagina, 'em aberto')

    def test_pagina_inicial_da_empresa_so_conta_o_que_e_dela(self):
        self.client.force_login(self.responsavel)
        pagina = self.client.get(reverse('inicio'))
        self.assertContains(pagina, 'R$ 2.500,00 em aberto · R$ 1.000,00 recebido')
        self.assertNotContains(pagina, '4.900,00')
