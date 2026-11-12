"""Reativar uma cobrança cancelada por engano."""

from datetime import date

from django.test import override_settings
from django.urls import reverse

from core.models import Usuario

from . import servicos
from .models import Cobranca
from .test_unificacao import Base

MES = date(2026, 9, 1)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ReativarTests(Base):
    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.admin = Usuario.objects.create_user('adm', password='x', perfil='administrativo')
        self.equipe = Usuario.objects.create_user('eq', password='x', perfil='equipe')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        servicos.gerar_alugueis(MES)
        self.cobranca = Cobranca.objects.get()
        self.client.force_login(self.sindico)
        self.client.post(reverse('cobranca_cancelar', args=[self.cobranca.pk]))
        self.cobranca.refresh_from_db()
        self.url = reverse('cobranca_reativar', args=[self.cobranca.pk])

    def test_o_cancelamento_foi_preparado(self):
        self.assertEqual(self.cobranca.status, 'cancelada')

    def test_reativa_e_volta_para_em_aberto_com_os_mesmos_dados(self):
        valor, vencimento, itens = self.cobranca.valor, self.cobranca.vencimento, self.cobranca.itens.count()
        resposta = self.client.post(self.url, follow=True)
        self.assertContains(resposta, 'Cobrança reativada')
        self.cobranca.refresh_from_db()
        self.assertEqual((self.cobranca.status, self.cobranca.valor, self.cobranca.vencimento, self.cobranca.itens.count()),
                         ('aberta', valor, vencimento, itens))

    def test_depois_de_reativar_da_para_pagar(self):
        self.client.post(self.url)
        resposta = self.client.post(reverse('cobranca_pagar', args=[self.cobranca.pk]),
                                    {'pago_em': '2026-10-05', 'valor_pago': '2500.00'})
        self.assertEqual(resposta.status_code, 302)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'paga')

    def test_administrativo_tambem_reativa(self):
        self.client.force_login(self.admin)
        self.client.post(self.url)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'aberta')

    def test_empresa_e_equipe_nao_reativam(self):
        for usuario in (self.responsavel, self.equipe):
            self.client.force_login(usuario)
            self.assertEqual(self.client.post(self.url).status_code, 403)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'cancelada')

    def test_so_reativa_o_que_esta_cancelado(self):
        self.client.post(self.url)  # volta para aberta
        self.assertEqual(self.client.post(self.url).status_code, 404)
        Cobranca.objects.update(status='paga')
        self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_recusa_quando_ja_existe_outra_cobranca_ativa_no_lugar(self):
        servicos.gerar_alugueis(MES)  # gera de novo, já que a anterior está cancelada
        nova = Cobranca.objects.exclude(pk=self.cobranca.pk).get()
        resposta = self.client.post(self.url, follow=True)
        self.assertContains(resposta, 'Não dá para reativar')
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'cancelada')
        self.assertEqual(Cobranca.objects.filter(status='aberta').get(), nova)

    def test_recusa_quando_so_uma_das_unidades_ja_foi_cobrada_de_novo(self):
        """A cobrança somada cobria Sala 01 e 02. Se a Sala 01 já tem outra ativa, reativar duplicaria a Sala 01."""
        Cobranca.objects.create(unidade=self.sala1, locatario=self.empresa, tipo='aluguel', referencia=MES,
                                valor=1000, vencimento=date(2026, 9, 10))
        self.client.post(self.url)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'cancelada')

    def test_outro_mes_ou_tipo_nao_atrapalha(self):
        servicos.gerar_alugueis(date(2026, 10, 1))
        self.client.post(self.url)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'aberta')

    def test_botao_aparece_na_lista_e_na_cobranca_cancelada(self):
        self.assertContains(self.client.get(reverse('cobranca_lista')), 'Reativar')
        self.assertContains(self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk])), 'Reativar cobrança')

    def test_botao_some_quando_a_cobranca_esta_em_aberto(self):
        self.client.post(self.url)
        self.assertNotContains(self.client.get(reverse('cobranca_lista')), 'Reativar')
        self.assertNotContains(self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk])), 'Reativar cobrança')

    def test_cancelar_e_reativar_varias_vezes_nao_quebra(self):
        for _ in range(3):
            self.client.post(self.url)
            self.client.post(reverse('cobranca_cancelar', args=[self.cobranca.pk]))
        self.client.post(self.url)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'aberta')
        self.assertEqual(Cobranca.objects.filter(status='aberta').count(), 1)
