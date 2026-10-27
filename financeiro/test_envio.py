"""Envio da cobrança por e-mail pelo sistema."""

import shutil
import tempfile
from datetime import date
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from cadastros.models import Locatario
from core.models import Usuario

from . import servicos
from .models import Cobranca, EnvioCobranca
from .test_unificacao import Base

MES = date(2026, 9, 1)
CONFIG = dict(EMAIL_HOST='smtp.teste', DEFAULT_FROM_EMAIL='Play <cobranca@play.test>', MEDIA_ROOT=tempfile.mkdtemp())


@override_settings(**CONFIG)
class EnvioTests(Base):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(CONFIG['MEDIA_ROOT'], ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.empresa.email_cobranca = 'financeiro@grupovida.test'
        self.empresa.save()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        servicos.gerar_alugueis(MES)
        self.cobranca = Cobranca.objects.get()
        self.url = reverse('cobranca_enviar', args=[self.cobranca.pk])
        self.client.force_login(self.sindico)

    def anexar_boleto(self):
        self.client.post(reverse('boleto_enviar', args=[self.cobranca.pk]),
                         {'arquivo': SimpleUploadedFile('boleto.pdf', b'%PDF-1.4 teste')})

    def test_pagina_traz_o_email_da_empresa_e_os_botoes(self):
        pagina = self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk]))
        self.assertContains(pagina, 'financeiro@grupovida.test')
        self.assertContains(pagina, 'Enviar por e-mail')
        lista = self.client.get(reverse('cobranca_lista'))
        self.assertContains(lista, '#boleto')  # botão de anexar na lista

    def test_envia_com_o_boleto_em_anexo_e_registra(self):
        self.anexar_boleto()
        resposta = self.client.post(self.url, {'para': 'financeiro@grupovida.test', 'mensagem': 'Bom dia!',
                                               'anexar_boleto': 'on'})
        self.assertRedirects(resposta, reverse('cobranca_detalhe', args=[self.cobranca.pk]))
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['financeiro@grupovida.test'])
        self.assertEqual(email.from_email, 'Play <cobranca@play.test>')
        self.assertIn('R$ 2.500,00', email.body)
        self.assertIn('Sala 01', email.body)
        self.assertIn('Bom dia!', email.body)
        self.assertEqual([(nome, conteudo) for nome, conteudo, _ in email.attachments],
                         [('boleto.pdf', b'%PDF-1.4 teste')])
        envio = EnvioCobranca.objects.get()
        self.assertTrue(envio.ok and envio.com_boleto)
        self.assertEqual(envio.enviado_por, self.sindico)

    def test_sem_marcar_anexar_vai_sem_boleto(self):
        self.anexar_boleto()
        self.client.post(self.url, {'para': 'a@b.test'})
        self.assertEqual(mail.outbox[0].attachments, [])
        self.assertFalse(EnvioCobranca.objects.get().com_boleto)

    def test_sem_boleto_salvo_envia_so_o_aviso(self):
        self.client.post(self.url, {'para': 'a@b.test', 'anexar_boleto': 'on'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(EnvioCobranca.objects.get().com_boleto)

    @override_settings(EMAIL_HOST='')
    def test_sem_configuracao_nao_envia(self):
        resposta = self.client.post(self.url, {'para': 'a@b.test'}, follow=True)
        self.assertContains(resposta, 'ainda não foi configurado')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EnvioCobranca.objects.count(), 0)

    def test_falha_do_servidor_fica_registrada(self):
        with mock.patch('django.core.mail.EmailMessage.send', side_effect=OSError('sem rede')):
            resposta = self.client.post(self.url, {'para': 'a@b.test'}, follow=True)
        self.assertContains(resposta, 'Não foi possível enviar')
        envio = EnvioCobranca.objects.get()
        self.assertFalse(envio.ok)
        self.assertIn('sem rede', envio.erro)

    def test_email_invalido_e_recusado(self):
        self.client.post(self.url, {'para': 'isso-nao-e-email'})
        self.assertEqual(len(mail.outbox), 0)

    def test_cobranca_cancelada_nao_envia(self):
        Cobranca.objects.update(status='cancelada')
        self.assertEqual(self.client.post(self.url, {'para': 'a@b.test'}).status_code, 404)

    def test_empresa_nao_dispara_envio(self):
        self.client.force_login(self.responsavel)
        self.assertEqual(self.client.post(self.url, {'para': 'a@b.test'}).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_empresa_nao_ve_o_formulario_de_envio(self):
        self.client.force_login(self.responsavel)
        pagina = self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk]))
        self.assertNotContains(pagina, 'Enviar por e-mail')

    def test_quebra_de_linha_no_destinatario_nao_injeta_cabecalho(self):
        self.client.post(self.url, {'para': 'a@b.test\nBcc: x@y.test'})
        self.assertEqual(len(mail.outbox), 0)
