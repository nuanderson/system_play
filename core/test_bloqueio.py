"""Trava de tentativas de login: 5 erros seguidos bloqueiam o usuário por 15 minutos."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import bloqueio
from .models import BloqueioLogin, Usuario

HASH = ['django.contrib.auth.hashers.MD5PasswordHasher']
GENERICA = 'Usuário ou senha incorretos.'


@override_settings(PASSWORD_HASHERS=HASH, LOGIN_MAX_FALHAS=5, LOGIN_BLOQUEIO_MINUTOS=15)
class BloqueioTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user('ana', password='certa', perfil='equipe')
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')

    def tentar(self, username='ana', senha='errada'):
        return self.client.post(reverse('login'), {'username': username, 'password': senha})

    def errar(self, vezes, username='ana'):
        for _ in range(vezes):
            self.tentar(username)

    def test_quatro_erros_ainda_deixam_entrar(self):
        self.errar(4)
        self.assertIsNone(bloqueio.bloqueado_ate('ana'))
        resposta = self.tentar(senha='certa')
        self.assertRedirects(resposta, reverse('inicio'), fetch_redirect_response=False)

    def test_o_quinto_erro_bloqueia_e_nem_a_senha_certa_entra(self):
        self.errar(5)
        self.assertIsNotNone(bloqueio.bloqueado_ate('ana'))
        resposta = self.tentar(senha='certa')
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Muitas tentativas erradas. Tente de novo em 15 minutos.')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_erro_normal_mostra_a_mensagem_generica(self):
        resposta = self.tentar()
        self.assertContains(resposta, GENERICA)
        self.assertNotContains(resposta, 'Muitas tentativas')

    def test_acertar_zera_a_contagem(self):
        self.errar(4)
        self.tentar(senha='certa')
        self.client.logout()
        self.errar(4)
        self.assertIsNone(bloqueio.bloqueado_ate('ana'))

    def test_usuario_inexistente_e_bloqueado_igual_sem_revelar_nada(self):
        self.errar(5, 'ninguem')
        resposta = self.tentar('ninguem', 'qualquer')
        self.assertContains(resposta, 'Muitas tentativas erradas')
        # quem existe e quem não existe recebem a mesma mensagem enquanto erram
        self.assertContains(self.tentar('outro-inexistente'), GENERICA)

    def test_maiusculas_nao_escapam_da_contagem(self):
        for nome in ('ana', 'ANA', 'Ana', 'aNa', ' ana '):
            self.tentar(nome)
        self.assertIsNotNone(bloqueio.bloqueado_ate('ana'))

    def test_tentar_durante_o_bloqueio_nao_prolonga(self):
        self.errar(5)
        antes = BloqueioLogin.objects.get(username='ana').bloqueado_ate
        self.errar(3)
        self.assertEqual(BloqueioLogin.objects.get(username='ana').bloqueado_ate, antes)

    def test_depois_do_tempo_volta_a_entrar(self):
        self.errar(5)
        BloqueioLogin.objects.filter(username='ana').update(bloqueado_ate=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(bloqueio.bloqueado_ate('ana'))
        resposta = self.tentar(senha='certa')
        self.assertRedirects(resposta, reverse('inicio'), fetch_redirect_response=False)

    def test_depois_do_bloqueio_a_contagem_recomeca(self):
        self.errar(5)
        BloqueioLogin.objects.filter(username='ana').update(bloqueado_ate=timezone.now() - timedelta(seconds=1))
        self.errar(4)
        self.assertIsNone(bloqueio.bloqueado_ate('ana'))

    def test_erros_antigos_nao_se_acumulam(self):
        self.errar(4)
        BloqueioLogin.objects.filter(username='ana').update(ultima_falha=timezone.now() - timedelta(minutes=16))
        self.errar(1)
        self.assertIsNone(bloqueio.bloqueado_ate('ana'))
        self.assertEqual(BloqueioLogin.objects.get(username='ana').falhas, 1)

    def test_bloquear_um_usuario_nao_afeta_os_outros(self):
        self.errar(5)
        resposta = self.tentar('sind', 'x')
        self.assertRedirects(resposta, reverse('inicio'), fetch_redirect_response=False)

    @override_settings(LOGIN_MAX_FALHAS=2, LOGIN_BLOQUEIO_MINUTOS=30)
    def test_os_numeros_vem_da_configuracao(self):
        self.errar(2)
        self.assertContains(self.tentar(senha='certa'), 'em 30 minutos')

    def test_o_admin_do_django_tambem_e_travado(self):
        Usuario.objects.create_superuser('chefe', 'c@x.com', 'certa')
        for _ in range(5):
            self.client.post('/admin/login/', {'username': 'chefe', 'password': 'errada', 'next': '/admin/'})
        resposta = self.client.post('/admin/login/', {'username': 'chefe', 'password': 'certa', 'next': '/admin/'})
        self.assertContains(resposta, 'Muitas tentativas erradas')
        self.assertNotIn('_auth_user_id', self.client.session)

    # --- síndico libera ---
    def test_sindico_ve_quem_esta_bloqueado_e_desbloqueia(self):
        self.errar(5)
        self.client.force_login(self.sindico)
        pagina = self.client.get(reverse('usuarios'))
        self.assertContains(pagina, 'bloqueado até')
        self.assertContains(pagina, 'Desbloquear')
        self.client.post(reverse('usuario_desbloquear', args=[self.usuario.pk]))
        self.assertIsNone(bloqueio.bloqueado_ate('ana'))
        self.client.logout()
        self.assertRedirects(self.tentar(senha='certa'), reverse('inicio'), fetch_redirect_response=False)

    def test_so_o_sindico_desbloqueia(self):
        self.errar(5)
        admin = Usuario.objects.create_user('adm', password='x', perfil='administrativo')
        self.client.force_login(admin)
        self.assertEqual(self.client.post(reverse('usuario_desbloquear', args=[self.usuario.pk])).status_code, 403)
        self.assertIsNotNone(bloqueio.bloqueado_ate('ana'))

    def test_lista_sem_bloqueios_nao_mostra_o_botao(self):
        self.client.force_login(self.sindico)
        self.assertNotContains(self.client.get(reverse('usuarios')), 'Desbloquear')
