"""Pessoas da empresa: contagem de quem trabalha lá, com ou sem acesso ao sistema."""

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Usuario
from relatorios import dados

from .models import Locatario, PessoaEmpresa, Unidade

HASH = ['django.contrib.auth.hashers.MD5PasswordHasher']


@override_settings(PASSWORD_HASHERS=HASH)
class PessoasTests(TestCase):
    def setUp(self):
        self.vida = Locatario.objects.create(razao_social='Clínica Vida')
        self.outra = Locatario.objects.create(razao_social='Outra Empresa')
        Unidade.objects.filter(nome='Sala 01').update(locatario=self.vida)
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.admin = Usuario.objects.create_user('adm', password='x', perfil='administrativo')
        self.equipe = Usuario.objects.create_user('eq', password='x', perfil='equipe')
        self.responsavel = Usuario.objects.create_user(
            'resp', password='x', perfil='locatario', locatario=self.vida, first_name='Ana', last_name='Souza')
        self.membro = Usuario.objects.create_user('mem', password='x', perfil='membro', locatario=self.vida)
        self.da_outra = PessoaEmpresa.objects.create(locatario=self.outra, nome='Beto', cargo='Gerente')

    def cadastrar(self, nome='Carla', **extra):
        return self.client.post(reverse('pessoa_criar', args=[self.vida.pk]), dict(nome=nome, **extra))

    # --- síndico e administrativo ---
    def test_sindico_cadastra_pessoa_sem_acesso(self):
        self.client.force_login(self.sindico)
        resposta = self.cadastrar('Carla', cargo='Recepcionista', telefone='63999990000')
        self.assertRedirects(resposta, reverse('pessoa_lista', args=[self.vida.pk]))
        carla = PessoaEmpresa.objects.get(nome='Carla')
        self.assertEqual((carla.locatario, carla.cargo, carla.usuario, carla.ativo), (self.vida, 'Recepcionista', None, True))
        self.assertContains(self.client.get(reverse('pessoa_lista', args=[self.vida.pk])), 'Recepcionista')

    def test_administrativo_tambem_cadastra(self):
        self.client.force_login(self.admin)
        self.cadastrar('Dora')
        self.assertTrue(PessoaEmpresa.objects.filter(nome='Dora', locatario=self.vida).exists())

    def test_equipe_nao_mexe(self):
        self.client.force_login(self.equipe)
        self.assertEqual(self.cadastrar('X').status_code, 403)
        self.assertEqual(self.client.get(reverse('pessoa_lista', args=[self.vida.pk])).status_code, 403)

    def test_nome_e_obrigatorio(self):
        self.client.force_login(self.sindico)
        self.cadastrar('')
        self.assertFalse(PessoaEmpresa.objects.filter(locatario=self.vida, usuario=None).exists())

    def test_contagem_na_lista_de_empresas(self):
        self.client.force_login(self.sindico)
        self.cadastrar('Carla')
        self.cadastrar('Dora')
        pagina = self.client.get(reverse('locatario_lista'))
        por_empresa = {e.razao_social: e.total_pessoas for e in pagina.context['locatarios']}
        self.assertEqual(por_empresa['Clínica Vida'], 2)
        self.assertEqual(por_empresa['Outra Empresa'], 1)
        self.assertEqual(pagina.context['total_pessoas'], 3)

    def test_editar_e_remover(self):
        self.client.force_login(self.sindico)
        self.cadastrar('Carla')
        carla = PessoaEmpresa.objects.get(nome='Carla')
        self.client.post(reverse('pessoa_editar', args=[carla.pk]), dict(nome='Carla Dias', cargo='Chefe'))
        carla.refresh_from_db()
        self.assertEqual((carla.nome, carla.cargo), ('Carla Dias', 'Chefe'))
        self.client.post(reverse('pessoa_remover', args=[carla.pk]))
        carla.refresh_from_db()
        self.assertFalse(carla.ativo)
        self.assertEqual(self.vida.pessoas.filter(ativo=True).count(), 0)

    # --- responsável da empresa ---
    def test_responsavel_cadastra_na_propria_empresa_e_volta_para_a_tela_dele(self):
        self.client.force_login(self.responsavel)
        resposta = self.cadastrar('Eva')
        self.assertRedirects(resposta, reverse('membros'))
        self.assertTrue(PessoaEmpresa.objects.filter(nome='Eva', locatario=self.vida).exists())

    def test_responsavel_nao_mexe_em_outra_empresa(self):
        self.client.force_login(self.responsavel)
        self.assertEqual(self.client.post(reverse('pessoa_criar', args=[self.outra.pk]), dict(nome='X')).status_code, 403)
        self.assertEqual(self.client.post(reverse('pessoa_remover', args=[self.da_outra.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('pessoa_editar', args=[self.da_outra.pk])).status_code, 403)
        self.da_outra.refresh_from_db()
        self.assertTrue(self.da_outra.ativo)

    def test_membro_nao_cadastra(self):
        self.client.force_login(self.membro)
        self.assertEqual(self.cadastrar('X').status_code, 403)

    def test_tela_do_responsavel_mostra_a_equipe_com_a_contagem(self):
        self.client.force_login(self.responsavel)
        self.cadastrar('Eva')
        pagina = self.client.get(reverse('membros'))
        self.assertContains(pagina, 'Equipe da empresa')
        self.assertContains(pagina, 'Eva')
        self.assertContains(pagina, 'sem acesso')

    def test_lista_so_mostra_pessoas_da_propria_empresa(self):
        self.client.force_login(self.responsavel)
        self.assertNotContains(self.client.get(reverse('membros')), 'Beto')

    # --- ligação com o acesso ---
    def test_quem_recebe_acesso_entra_na_lista_da_empresa(self):
        self.client.force_login(self.responsavel)
        self.client.post(reverse('membro_criar'), dict(first_name='Fabio', username='fabio', email='f@x.com'))
        fabio = PessoaEmpresa.objects.get(nome='Fabio')
        self.assertEqual((fabio.locatario, fabio.usuario.username), (self.vida, 'fabio'))
        self.assertTrue(fabio.tem_acesso)

    def test_sindico_cria_usuario_da_empresa_e_a_pessoa_nasce_junto(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('usuario_criar'), dict(
            first_name='Gil', username='gil', email='g@x.com', perfil='membro', locatario=self.vida.pk))
        self.assertTrue(PessoaEmpresa.objects.filter(nome='Gil', locatario=self.vida, usuario__username='gil').exists())

    def test_usuario_do_sindico_nao_vira_pessoa_de_empresa(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('usuario_criar'), dict(first_name='Hugo', username='hugo', email='h@x.com', perfil='equipe'))
        self.assertFalse(PessoaEmpresa.objects.filter(nome='Hugo').exists())

    def test_remover_pessoa_com_acesso_desativa_o_acesso(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('usuario_criar'), dict(
            first_name='Gil', username='gil', email='g@x.com', perfil='membro', locatario=self.vida.pk))
        gil = PessoaEmpresa.objects.get(nome='Gil')
        self.client.post(reverse('pessoa_remover', args=[gil.pk]))
        gil.usuario.refresh_from_db()
        self.assertFalse(gil.usuario.is_active)
        self.assertFalse(PessoaEmpresa.objects.get(pk=gil.pk).ativo)

    def test_ninguem_remove_a_si_mesmo(self):
        pessoa = PessoaEmpresa.objects.create(locatario=self.vida, nome='Ana Souza', usuario=self.responsavel)
        self.client.force_login(self.responsavel)
        self.client.post(reverse('pessoa_remover', args=[pessoa.pk]))
        pessoa.refresh_from_db()
        self.responsavel.refresh_from_db()
        self.assertTrue(pessoa.ativo and self.responsavel.is_active)

    # --- relatório ---
    def test_relatorio_conta_com_e_sem_acesso(self):
        PessoaEmpresa.objects.create(locatario=self.vida, nome='Ana Souza', usuario=self.responsavel)
        PessoaEmpresa.objects.create(locatario=self.vida, nome='Sem acesso')
        PessoaEmpresa.objects.create(locatario=self.vida, nome='Saiu', ativo=False)
        base = dados.pessoas_por_empresa()
        vida = next(l for l in base['linhas'] if l['empresa'] == self.vida)
        self.assertEqual((vida['total'], vida['com_acesso'], vida['sem_acesso']), (2, 1, 1))
        self.assertEqual((base['total'], base['com_acesso'], base['sem_acesso']), (3, 1, 2))
        self.assertEqual(base['linhas'][0]['empresa'], self.vida)  # maior primeiro

    def test_relatorio_na_tela_e_em_csv(self):
        self.client.force_login(self.sindico)
        self.cadastrar('Carla')
        self.assertContains(self.client.get(reverse('relatorio_pessoas')), 'Pessoas no prédio')
        csv = self.client.get(reverse('relatorio_pessoas') + '?formato=csv')
        self.assertEqual(csv.status_code, 200)
        self.assertIn('Clínica Vida', csv.content.decode('utf-8-sig'))

    def test_inicio_da_empresa_mostra_o_total(self):
        PessoaEmpresa.objects.create(locatario=self.vida, nome='Ana Souza', usuario=self.responsavel)
        self.client.force_login(self.responsavel)
        self.cadastrar('Eva')
        self.assertContains(self.client.get(reverse('inicio')), '2 pessoas · 1 com acesso')
