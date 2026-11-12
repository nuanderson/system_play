"""Envio em lote das cobranças e vários e-mails por empresa."""

from datetime import date
from unittest import mock

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from cadastros.emails import separar_emails, validar_lista_de_emails
from cadastros.forms import LocatarioForm
from cadastros.models import Locatario, Unidade
from core.models import Usuario

from . import servicos, views
from .models import Cobranca, EnvioCobranca
from .test_unificacao import Base

MES = date(2026, 9, 1)
CONFIG = dict(EMAIL_HOST='smtp.teste', DEFAULT_FROM_EMAIL='Play <c@play.test>')


class ListaDeEmailsTests(SimpleTestCase):
    def test_separa_por_virgula_ponto_e_virgula_e_espaco(self):
        self.assertEqual(separar_emails('a@x.com, b@y.com;c@z.com  d@w.com\ne@v.com'),
                         ['a@x.com', 'b@y.com', 'c@z.com', 'd@w.com', 'e@v.com'])

    def test_tira_repetidos_sem_ligar_para_maiusculas(self):
        self.assertEqual(separar_emails('A@x.com, a@X.com, b@y.com'), ['A@x.com', 'b@y.com'])

    def test_vazio(self):
        self.assertEqual(separar_emails(''), [])
        self.assertEqual(separar_emails(None), [])

    def test_valida_e_aponta_o_endereco_errado(self):
        self.assertEqual(validar_lista_de_emails('a@x.com, b@y.com'), ['a@x.com', 'b@y.com'])
        with self.assertRaisesMessage(ValidationError, 'nao-e-email'):
            validar_lista_de_emails('a@x.com, nao-e-email')

    def test_obrigatorio_e_limite(self):
        with self.assertRaises(ValidationError):
            validar_lista_de_emails('', obrigatorio=True)
        with self.assertRaises(ValidationError):
            validar_lista_de_emails(', '.join(f'p{i}@x.com' for i in range(11)))

    def test_cabecalho_injetado_e_recusado(self):
        with self.assertRaises(ValidationError):
            validar_lista_de_emails('a@x.com\nBcc: alguem@y.com')


class EmpresaComVariosEmailsTests(Base):
    def dados(self, emails):
        return dict(razao_social='Empresa X', email_cobranca=emails, cobranca_unificada='on', ativo='on')

    def test_empresa_aceita_varios_emails_e_guarda_arrumado(self):
        form = LocatarioForm(self.dados('a@x.com;  b@y.com ,a@X.com'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['email_cobranca'], 'a@x.com, b@y.com')

    def test_empresa_recusa_email_invalido(self):
        form = LocatarioForm(self.dados('a@x.com, errado'))
        self.assertFalse(form.is_valid())
        self.assertIn('email_cobranca', form.errors)

    def test_pode_ficar_sem_email(self):
        self.assertTrue(LocatarioForm(self.dados('')).is_valid())


@override_settings(**CONFIG)
class EnvioComVariosDestinatariosTests(Base):
    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        servicos.gerar_alugueis(MES)
        self.cobranca = Cobranca.objects.get()
        self.url = reverse('cobranca_enviar', args=[self.cobranca.pk])
        self.client.force_login(self.sindico)

    def test_envia_para_todos_os_enderecos_num_e_mail_so(self):
        self.client.post(self.url, {'para': 'x@a.com; y@b.com, X@a.com'})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['x@a.com', 'y@b.com'])
        self.assertEqual(EnvioCobranca.objects.get().para, 'x@a.com, y@b.com')

    def test_um_endereco_errado_barra_o_envio(self):
        self.client.post(self.url, {'para': 'x@a.com, errado'})
        self.assertEqual(len(mail.outbox), 0)

    def test_mais_de_dez_enderecos_e_recusado(self):
        self.client.post(self.url, {'para': ', '.join(f'p{i}@x.com' for i in range(11))})
        self.assertEqual(len(mail.outbox), 0)

    def test_a_tela_mostra_todos_os_emails_do_cadastro(self):
        self.empresa.email_cobranca = 'a@vida.test, b@vida.test'
        self.empresa.save()
        pagina = self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk]))
        self.assertContains(pagina, 'a@vida.test, b@vida.test')


@override_settings(**CONFIG)
class EnvioEmLoteTests(Base):
    def setUp(self):
        super().setUp()
        self.empresa.email_cobranca = 'a@vida.test, b@vida.test'
        self.empresa.save()
        self.bela = Locatario.objects.create(razao_social='Bela Estética', email_cobranca='bela@x.test')
        self.sem = Locatario.objects.create(razao_social='Sem Email')
        self.contrato(Unidade.objects.get(nome='Sala 04'), self.bela, '800')
        self.contrato(Unidade.objects.get(nome='Sala 05'), self.sem, '700')
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.admin = Usuario.objects.create_user('adm', password='x', perfil='administrativo')
        self.equipe = Usuario.objects.create_user('eq', password='x', perfil='equipe')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        servicos.gerar_alugueis(MES)  # Grupo Vida (unificada), Bela e Sem Email
        self.url = reverse('cobranca_enviar_todas')
        self.client.force_login(self.sindico)

    def enviar(self, extra=None, consulta=''):
        dados = {'pular_enviadas': 'on', 'incluir_pix': 'on', 'anexar_fatura': 'on', 'anexar_boleto': 'on'}
        dados.update(extra or {})
        return self.client.post(self.url + consulta, dados)

    # --- botão e conferência ---
    def test_o_botao_aparece_na_lista_e_leva_os_filtros(self):
        pagina = self.client.get(reverse('cobranca_lista') + '?tipo=aluguel')
        self.assertContains(pagina, 'Enviar cobranças por e-mail')
        self.assertContains(pagina, reverse('cobranca_enviar_todas') + '?tipo=aluguel')

    def test_a_empresa_nao_ve_o_botao(self):
        self.client.force_login(self.responsavel)
        self.assertNotContains(self.client.get(reverse('cobranca_lista')), 'Enviar cobranças por e-mail')

    def test_conferencia_mostra_quem_recebe_e_quem_esta_sem_email(self):
        pagina = self.client.get(self.url)
        self.assertEqual([c.locatario.razao_social for c in pagina.context['a_enviar']], ['Bela Estética', 'Grupo Vida'])
        self.assertEqual([c.locatario.razao_social for c in pagina.context['sem_email']], ['Sem Email'])
        self.assertContains(pagina, 'a@vida.test, b@vida.test')
        self.assertContains(pagina, 'cadastrar e-mail')
        self.assertContains(pagina, 'Enviar 2 cobranças')

    def test_so_entram_as_cobrancas_em_aberto(self):
        Cobranca.objects.filter(locatario=self.bela).update(status='paga')
        Cobranca.objects.filter(locatario=self.empresa).update(status='cancelada')
        pagina = self.client.get(self.url)
        self.assertEqual(pagina.context['a_enviar'], [])

    # --- envio ---
    def test_envia_um_email_por_cobranca_para_todos_os_enderecos(self):
        resposta = self.enviar()
        self.assertRedirects(resposta, reverse('cobranca_lista') + '?', fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 2)
        por_destino = {tuple(m.to) for m in mail.outbox}
        self.assertEqual(por_destino, {('bela@x.test',), ('a@vida.test', 'b@vida.test')})
        self.assertEqual(EnvioCobranca.objects.filter(ok=True).count(), 2)
        self.assertFalse(EnvioCobranca.objects.filter(cobranca__locatario=self.sem).exists())

    def test_mostra_o_resumo_depois_de_enviar(self):
        resposta = self.client.post(self.url, {'pular_enviadas': 'on'}, follow=True)
        self.assertContains(resposta, '2 cobranças enviadas por e-mail.')

    def test_o_recado_vai_em_todos(self):
        self.enviar({'mensagem': 'Bom dia a todos!'})
        self.assertTrue(all('Bom dia a todos!' in m.body for m in mail.outbox))

    def test_usa_uma_conexao_so_para_o_lote(self):
        with mock.patch('financeiro.views.get_connection', wraps=views.get_connection) as conexao:
            self.enviar()
        self.assertEqual(conexao.call_count, 1)

    def test_pula_as_ja_enviadas_por_padrao(self):
        self.enviar()
        mail.outbox.clear()
        resposta = self.client.post(self.url, {'pular_enviadas': 'on'}, follow=True)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(resposta, 'Nenhuma cobrança para enviar')

    def test_sem_marcar_pular_envia_de_novo(self):
        self.enviar()
        mail.outbox.clear()
        self.client.post(self.url, {})
        self.assertEqual(len(mail.outbox), 2)

    def test_a_conferencia_lista_as_ja_enviadas(self):
        self.enviar()
        pagina = self.client.get(self.url)
        self.assertEqual(pagina.context['a_enviar'], [])
        self.assertEqual(len(pagina.context['enviadas']), 2)

    def test_uma_falha_de_envio_nao_derruba_o_resto(self):
        with mock.patch('django.core.mail.EmailMessage.send', side_effect=[OSError('sem rede'), 1]):
            resposta = self.client.post(self.url, {'pular_enviadas': 'on'}, follow=True)
        self.assertContains(resposta, '1 cobrança enviada por e-mail.')
        self.assertContains(resposta, '1 não saiu')
        self.assertEqual(EnvioCobranca.objects.filter(ok=False).count(), 1)
        self.assertEqual(EnvioCobranca.objects.filter(ok=True).count(), 1)

    # --- filtros e limites ---
    def test_respeita_os_filtros_da_lista(self):
        self.enviar(consulta=f'?empresa={self.bela.pk}')
        self.assertEqual([m.to for m in mail.outbox], [['bela@x.test']])

    def test_filtro_sem_resultado_nao_envia_nada(self):
        resposta = self.client.post(self.url + '?tipo=condominio', {'pular_enviadas': 'on'}, follow=True)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(resposta, 'Nenhuma cobrança para enviar')

    def test_limite_por_vez(self):
        with mock.patch.object(views.CobrancaEnviarTodas, 'LIMITE', 1):
            pagina = self.client.get(self.url)
            self.assertEqual(len(pagina.context['a_enviar']), 1)
            self.assertEqual(pagina.context['excedente'], 1)
            self.enviar()
        self.assertEqual(len(mail.outbox), 1)

    # --- configuração e permissão ---
    @override_settings(EMAIL_HOST='')
    def test_sem_configuracao_de_email_avisa_e_nao_envia(self):
        self.assertContains(self.client.get(self.url), 'ainda não foi configurado')
        resposta = self.client.post(self.url, {'pular_enviadas': 'on'}, follow=True)
        self.assertContains(resposta, 'ainda não foi configurado')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EnvioCobranca.objects.count(), 0)

    def test_administrativo_pode(self):
        self.client.force_login(self.admin)
        self.enviar()
        self.assertEqual(len(mail.outbox), 2)

    def test_empresa_e_equipe_nao_podem(self):
        for usuario in (self.responsavel, self.equipe):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, {}).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_pix_e_fatura_seguem_as_opcoes_marcadas(self):
        with override_settings(PIX_CHAVE='12345678000190', PIX_NOME='Play', PIX_CIDADE='Araguaina'):
            self.enviar({'anexar_fatura': '', 'incluir_pix': 'on'})
            self.assertTrue(all('pix-qrcode.png' in [a[0] for a in m.attachments] for m in mail.outbox))
            mail.outbox.clear()
            self.client.post(self.url, {})
            self.assertTrue(all(m.attachments == [] for m in mail.outbox))
