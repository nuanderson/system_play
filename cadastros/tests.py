from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from core.models import Usuario

from .models import Locatario, Unidade


class CargaInicialTests(TestCase):
    def test_areas_do_rateio_somam_o_total_do_projeto(self):
        total = Unidade.objects.aggregate(t=Sum('area_rateio'))['t']
        self.assertEqual(total, Decimal('1471.35'))

    def test_oito_salas_com_area_privativa_total(self):
        salas = Unidade.objects.filter(tipo='sala')
        self.assertEqual(salas.count(), 8)
        self.assertEqual(salas.aggregate(t=Sum('area_privativa'))['t'], Decimal('148.41'))
        self.assertEqual(salas.aggregate(t=Sum('area_rateio'))['t'], Decimal('327.72'))


class PermissoesTests(TestCase):
    def setUp(self):
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.locatario = Usuario.objects.create_user('loc', password='x', perfil=Usuario.Perfil.LOCATARIO)

    def test_anonimo_vai_para_login(self):
        resposta = self.client.get(reverse('unidade_lista'))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn('/entrar/', resposta['Location'])

    def test_locatario_nao_acessa_cadastros(self):
        self.client.force_login(self.locatario)
        self.assertEqual(self.client.get(reverse('unidade_lista')).status_code, 403)

    def test_sindico_acessa_cadastros(self):
        self.client.force_login(self.sindico)
        resposta = self.client.get(reverse('unidade_lista'))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Sala 01')

    def test_sindico_cria_locatario(self):
        self.client.force_login(self.sindico)
        resposta = self.client.post(reverse('locatario_criar'), {'razao_social': 'Empresa Teste', 'ativo': 'on'})
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(Locatario.objects.filter(razao_social='Empresa Teste').exists())

    def test_inicio_para_locatario(self):
        self.client.force_login(self.locatario)
        resposta = self.client.get(reverse('inicio'))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'Locatários</h3>')

    def test_inicio_do_sindico_mostra_modulos_e_indicadores(self):
        self.client.force_login(self.sindico)
        resposta = self.client.get(reverse('inicio'))
        self.assertContains(resposta, 'Unidades</h3>')
        self.assertContains(resposta, 'Condomínio e rateio')
        self.assertContains(resposta, 'unidades ativas')
