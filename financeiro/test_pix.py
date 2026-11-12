"""Pix com QR Code estático: o código tem que ser válido para o app do banco aceitar."""

from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from core.models import Usuario

from . import fatura, pix, servicos
from .models import Cobranca, EnvioCobranca
from .test_fatura import FaturaTests
from .test_unificacao import Base

MES = date(2026, 9, 1)
PIX = dict(PIX_CHAVE='12.345.678/0001-90', PIX_NOME='Play Participações', PIX_CIDADE='Araguaína',
           EMAIL_HOST='smtp.teste', DEFAULT_FROM_EMAIL='Play <c@play.test>')


def ler_campos(texto):
    """Desmonta o formato TLV do BR Code: código (2) + tamanho (2) + valor."""
    campos, i = {}, 0
    while i < len(texto):
        codigo, tamanho = texto[i:i + 2], int(texto[i + 2:i + 4])
        campos[codigo] = texto[i + 4:i + 4 + tamanho]
        i += 4 + tamanho
    return campos


class CodigoPixTests(SimpleTestCase):
    def test_crc_bate_com_o_exemplo_do_manual_do_banco_central(self):
        exemplo = ('00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-4266554400005204000053039865802BR'
                   '5913Fulano de Tal6008BRASILIA62070503***6304')
        self.assertEqual(pix.crc16(exemplo), '1D3D')

    def monta(self, **extra):
        dados = dict(chave='12.345.678/0001-90', nome='Play Participações', cidade='Araguaína',
                     valor=Decimal('1725.2'), ref='URUZ00000042')
        dados.update(extra)
        return pix.payload(**dados)

    def test_estrutura_do_codigo(self):
        codigo = self.monta()
        campos = ler_campos(codigo)
        self.assertEqual(campos['00'], '01')
        self.assertEqual(campos['01'], '11')  # estático
        self.assertEqual(ler_campos(campos['26']), {'00': 'br.gov.bcb.pix', '01': '12345678000190'})
        self.assertEqual((campos['52'], campos['53'], campos['58']), ('0000', '986', 'BR'))
        self.assertEqual(campos['54'], '1725.20')
        self.assertEqual(campos['59'], 'PLAY PARTICIPACOES')
        self.assertEqual(campos['60'], 'ARAGUAINA')
        self.assertEqual(ler_campos(campos['62']), {'05': 'URUZ00000042'})

    def test_o_crc_do_codigo_fecha(self):
        codigo = self.monta()
        self.assertTrue(codigo.endswith(pix.crc16(codigo[:-4])))
        self.assertEqual(codigo[-8:-4], '6304'.rjust(4))  # o campo do CRC vem por último

    def test_valor_sempre_com_duas_casas_e_ponto(self):
        self.assertEqual(ler_campos(self.monta(valor=Decimal('5000')))['54'], '5000.00')
        self.assertEqual(ler_campos(self.monta(valor=Decimal('0.5')))['54'], '0.50')

    def test_nome_e_cidade_respeitam_os_limites(self):
        campos = ler_campos(self.monta(nome='Empresa com um nome muito comprido demais', cidade='Cidade Muito Grande Mesmo'))
        self.assertLessEqual(len(campos['59']), 25)
        self.assertLessEqual(len(campos['60']), 15)

    def test_chaves(self):
        self.assertEqual(pix.normalizar_chave('12.345.678/0001-90'), '12345678000190')
        self.assertEqual(pix.normalizar_chave('123.456.789-09'), '12345678909')
        self.assertEqual(pix.normalizar_chave('+5563999990000'), '+5563999990000')
        self.assertEqual(pix.normalizar_chave('financeiro@play.com.br'), 'financeiro@play.com.br')
        self.assertEqual(pix.normalizar_chave('123e4567-e12b-12d1-a456-426655440000'),
                         '123e4567-e12b-12d1-a456-426655440000')

    def test_qr_gera_svg_e_png(self):
        codigo = self.monta()
        self.assertIn('<svg', pix.qr_svg(codigo))
        self.assertTrue(pix.qr_png(codigo).startswith(b'\x89PNG'))

    def test_svg_se_ajusta_a_caixa_em_vez_de_ter_tamanho_fixo(self):
        svg = pix.qr_svg(self.monta())
        abertura = svg[:svg.index('>')]
        self.assertIn('viewBox=', abertura)
        self.assertNotIn(' width=', abertura)
        self.assertNotIn(' height=', abertura)

    def test_sem_configuracao_nao_ha_pix(self):
        with override_settings(PIX_CHAVE='', PIX_NOME='', PIX_CIDADE=''):
            self.assertFalse(pix.configurado())


@override_settings(**PIX)
class PixNaCobrancaTests(Base):
    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        servicos.gerar_alugueis(MES)
        self.cobranca = Cobranca.objects.get()
        self.url = reverse('cobranca_detalhe', args=[self.cobranca.pk])

    def test_a_empresa_ve_o_qr_com_o_valor_da_cobranca(self):
        self.client.force_login(self.responsavel)
        pagina = self.client.get(self.url)
        self.assertContains(pagina, 'Pagar com Pix')
        self.assertContains(pagina, '<svg')
        codigo = pagina.context['pix_codigo']
        self.assertEqual(ler_campos(codigo)['54'], '2500.00')
        self.assertEqual(ler_campos(ler_campos(codigo)['62'])['05'], f'URUZ{self.cobranca.pk:08d}')

    def test_o_sindico_ve_o_aviso_da_baixa_manual(self):
        self.client.force_login(self.sindico)
        self.assertContains(self.client.get(self.url), 'não avisa o sistema')

    def test_sem_configuracao_o_cartao_some(self):
        self.client.force_login(self.responsavel)
        with override_settings(PIX_CHAVE=''):
            self.assertNotContains(self.client.get(self.url), 'Pagar com Pix')

    def test_cobranca_paga_ou_cancelada_nao_mostra_pix(self):
        self.client.force_login(self.sindico)
        for situacao in ('paga', 'cancelada'):
            Cobranca.objects.update(status=situacao)
            self.assertNotContains(self.client.get(self.url), 'Pagar com Pix')

    def test_email_leva_o_codigo_no_texto_e_o_qr_em_anexo(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('cobranca_enviar', args=[self.cobranca.pk]), {'para': 'a@b.test', 'incluir_pix': 'on'})
        email = mail.outbox[0]
        self.assertIn(pix.codigo_da_cobranca(self.cobranca), email.body)
        self.assertIn('Pix copia e cola', email.body)
        self.assertEqual([a[0] for a in email.attachments], ['pix-qrcode.png'])
        self.assertTrue(EnvioCobranca.objects.get().com_pix)

    def test_email_sem_marcar_o_pix_vai_sem_ele(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('cobranca_enviar', args=[self.cobranca.pk]), {'para': 'a@b.test'})
        self.assertNotIn('Pix', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].attachments, [])
        self.assertFalse(EnvioCobranca.objects.get().com_pix)


@override_settings(**PIX)
class PixNaFaturaTests(FaturaTests):
    """Repete a bateria da fatura com o Pix ligado e confere que o PDF cresce com o QR."""

    def test_pdf_com_pix_e_maior_que_sem_pix(self):
        com = fatura.gerar_pdf(self.cobranca)
        with override_settings(PIX_CHAVE=''):
            sem = fatura.gerar_pdf(self.cobranca)
        self.assertTrue(com.startswith(b'%PDF-'))
        self.assertGreater(len(com), len(sem))
