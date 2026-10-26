from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cadastros.forms import LocatarioForm
from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from contratos.models import Contrato
from correspondencias.models import Correspondencia
from financeiro.models import Cobranca

from .models import Usuario
from .views import alertas, resumos
from .usuarios import pessoas_da_empresa

RAPIDO = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
L, M, S, A = Usuario.Perfil.LOCATARIO, Usuario.Perfil.MEMBRO, Usuario.Perfil.SINDICO, Usuario.Perfil.ADMINISTRATIVO


@RAPIDO
class Base(TestCase):
    """A Clínica Vida ocupa três unidades. A Outra Empresa ocupa uma."""

    def setUp(self):
        self.terreo = Unidade.objects.get(nome='Térreo (clínica)')
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.sala3 = Unidade.objects.get(nome='Sala 03')
        self.sala4 = Unidade.objects.get(nome='Sala 04')
        self.clinica = Locatario.objects.create(razao_social='Clínica Vida')
        self.outra = Locatario.objects.create(razao_social='Outra Empresa')
        for unidade in (self.terreo, self.sala1, self.sala2):
            Unidade.objects.filter(pk=unidade.pk).update(locatario=self.clinica)
        Unidade.objects.filter(pk=self.sala3.pk).update(locatario=self.outra)
        self.minhas = [self.terreo, self.sala1, self.sala2]

        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=S, first_name='Síndico', email='s@e.com')
        self.administrativo = Usuario.objects.create_user('adm', password='x', perfil=A, first_name='Adm', email='a@e.com')
        self.resp = Usuario.objects.create_user('resp', password='x', perfil=L, locatario=self.clinica, first_name='Rui', email='r@e.com')
        self.membro_todas = Usuario.objects.create_user('todas', password='x', perfil=M, locatario=self.clinica, first_name='Todas')
        self.membro_sala1 = Usuario.objects.create_user('so1', password='x', perfil=M, locatario=self.clinica, unidade=self.sala1, first_name='Só1')
        self.resp_outra = Usuario.objects.create_user('resp2', password='x', perfil=L, locatario=self.outra, first_name='Olga')
        self.antigo = Usuario.objects.create_user('antigo', password='x', perfil=L, unidade=self.sala4, first_name='Antigo')  # cadastro antigo: só unidade

    def contrato(self, unidade, locatario, **extra):
        dados = dict(unidade=unidade, locatario=locatario, status='vigente', data_inicio=date(2026, 1, 1), valor_aluguel=Decimal('1000'))
        dados.update(extra)
        return Contrato.objects.create(**dados)

    def chamado(self, unidade, titulo):
        return Chamado.objects.create(unidade=unidade, solicitante=self.sindico, titulo=titulo, descricao='d')

    def atrasada(self, unidade, locatario, valor='100.00', dias=5):
        vencimento = timezone.localdate() - timedelta(days=dias)
        return Cobranca.objects.create(unidade=unidade, locatario=locatario, tipo='aluguel', referencia=vencimento.replace(day=1),
                                       valor=Decimal(valor), vencimento=vencimento)


class EscopoDoUsuarioTests(Base):
    def test_unidades_de_cada_tipo_de_usuario(self):
        self.assertEqual(sorted(self.resp.unidades_ids()), sorted(u.pk for u in self.minhas))
        self.assertEqual(sorted(self.membro_todas.unidades_ids()), sorted(u.pk for u in self.minhas))
        self.assertEqual(self.membro_sala1.unidades_ids(), [self.sala1.pk])
        self.assertEqual(self.resp_outra.unidades_ids(), [self.sala3.pk])
        self.assertEqual(self.antigo.unidades_ids(), [self.sala4.pk])  # quem já existia continua igual
        self.assertEqual(self.sindico.unidades_ids(), [])

    def test_unidade_inativa_ou_nova_acompanha_a_empresa(self):
        Unidade.objects.filter(pk=self.sala2.pk).update(ativa=False)
        self.assertNotIn(self.sala2.pk, self.resp.unidades_ids())
        Unidade.objects.filter(pk=self.sala4.pk).update(locatario=self.clinica)
        self.assertIn(self.sala4.pk, self.resp.unidades_ids())  # unidade nova da empresa aparece sozinha

    def test_desocupar_a_unidade_tira_o_acesso(self):
        Unidade.objects.filter(pk=self.sala1.pk).update(locatario=None)
        self.assertNotIn(self.sala1.pk, self.resp.unidades_ids())

    def test_tem_escopo(self):
        self.assertTrue(self.resp.tem_escopo)
        self.assertTrue(self.antigo.tem_escopo)
        self.assertFalse(self.sindico.tem_escopo)


class VisibilidadeDeVariasUnidadesTests(Base):
    def setUp(self):
        super().setUp()
        for unidade in self.minhas:
            self.chamado(unidade, f'Chamado {unidade.nome}')
            Correspondencia.objects.create(unidade=unidade, tipo='carta', destinatario=f'Para {unidade.nome}', registrado_por=self.sindico)
            self.contrato(unidade, self.clinica, valor_aluguel=Decimal('500'))
            self.atrasada(unidade, self.clinica, '100.00')
        self.chamado(self.sala3, 'Chamado alheio')
        Correspondencia.objects.create(unidade=self.sala3, tipo='carta', destinatario='Para alheio', registrado_por=self.sindico)
        self.contrato(self.sala3, self.outra)
        self.atrasada(self.sala3, self.outra, '999.00')

    def test_responsavel_ve_as_tres_unidades_e_nada_da_outra_empresa(self):
        self.client.force_login(self.resp)
        chamados = self.client.get(reverse('chamado_lista'))
        for unidade in self.minhas:
            self.assertContains(chamados, f'Chamado {unidade.nome}')
        self.assertNotContains(chamados, 'Chamado alheio')
        correspondencias = self.client.get(reverse('correspondencia_lista'))
        for unidade in self.minhas:
            self.assertContains(correspondencias, f'Para {unidade.nome}')
        self.assertNotContains(correspondencias, 'Para alheio')
        self.assertEqual(len(self.client.get(reverse('contrato_lista')).context['contratos']), 3)
        cobrancas = self.client.get(reverse('cobranca_lista'))
        self.assertEqual(len(cobrancas.context['cobrancas']), 3)
        self.assertNotContains(cobrancas, '999,00')

    def test_nao_abre_o_que_e_de_outra_empresa(self):
        alheio = Chamado.objects.get(titulo='Chamado alheio')
        self.client.force_login(self.resp)
        self.assertEqual(self.client.get(reverse('chamado_detalhe', args=[alheio.pk])).status_code, 404)
        contrato_alheio = Contrato.objects.get(unidade=self.sala3)
        self.assertEqual(self.client.get(reverse('contrato_detalhe', args=[contrato_alheio.pk])).status_code, 404)
        correspondencia = Correspondencia.objects.get(destinatario='Para alheio')
        self.assertEqual(self.client.get(reverse('correspondencia_detalhe', args=[correspondencia.pk])).status_code, 404)

    def test_membro_da_empresa_ve_todas_e_o_restrito_ve_so_a_dele(self):
        self.client.force_login(self.membro_todas)
        self.assertContains(self.client.get(reverse('chamado_lista')), 'Chamado Térreo')
        self.client.force_login(self.membro_sala1)
        pagina = self.client.get(reverse('chamado_lista'))
        self.assertContains(pagina, 'Chamado Sala 01')
        self.assertNotContains(pagina, 'Chamado Sala 02')
        self.assertNotContains(pagina, 'Chamado Térreo')
        self.assertNotContains(self.client.get(reverse('correspondencia_lista')), 'Para Sala 02')

    def test_filtro_por_unidade_aparece_so_para_quem_tem_mais_de_uma(self):
        self.client.force_login(self.resp)
        self.assertContains(self.client.get(reverse('chamado_lista')), 'name="unidade"')
        pagina = self.client.get(reverse('chamado_lista') + f'?unidade={self.sala1.pk}')
        self.assertContains(pagina, 'Chamado Sala 01')
        self.assertNotContains(pagina, 'Chamado Sala 02')
        # filtrar por uma unidade que não é sua não revela nada
        self.assertNotContains(self.client.get(reverse('chamado_lista') + f'?unidade={self.sala3.pk}'), 'Chamado alheio')
        self.client.force_login(self.membro_sala1)
        self.assertNotContains(self.client.get(reverse('chamado_lista')), 'name="unidade"')

    def test_home_soma_as_unidades_da_empresa(self):
        dados = resumos(self.resp)
        self.assertEqual(dados['Contratos'], '3 vigentes')
        self.assertEqual(dados['Cobranças'], 'R$ 300,00 em aberto')
        self.assertEqual(dados['Chamados de manutenção'], '3 em aberto')
        self.assertEqual(dados['Correspondências'], '3 aguardando retirada')
        self.assertEqual([a['texto'] for a in alertas(self.resp)][0], '3 cobranças atrasadas · R$ 300,00')
        self.assertIn('3 correspondências esperando por você', [a['texto'] for a in alertas(self.resp)])
        self.assertEqual(resumos(self.membro_sala1)['Chamados de manutenção'], '1 em aberto')


class AbrirChamadoComVariasUnidadesTests(Base):
    def dados(self, **extra):
        dados = dict(titulo='Vazamento', categoria='hidraulica', prioridade=2, local='Banheiro', descricao='Pinga')
        dados.update(extra)
        return dados

    def test_formulario_pergunta_a_unidade_e_so_oferece_as_da_empresa(self):
        self.client.force_login(self.resp)
        form = self.client.get(reverse('chamado_criar')).context['form']
        self.assertIn('unidade', form.fields)
        self.assertEqual(sorted(form.fields['unidade'].queryset.values_list('pk', flat=True)), sorted(u.pk for u in self.minhas))
        self.assertTrue(form.fields['unidade'].required)

    def test_abre_na_unidade_escolhida(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('chamado_criar'), self.dados(unidade=self.sala2.pk))
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.unidade, chamado.solicitante), (self.sala2, self.resp))

    def test_recusa_sem_unidade_ou_com_unidade_de_outra_empresa(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('chamado_criar'), self.dados())
        self.client.post(reverse('chamado_criar'), self.dados(unidade=self.sala3.pk))
        self.assertFalse(Chamado.objects.exists())

    def test_com_uma_unidade_so_nao_pergunta_e_usa_a_unica(self):
        self.client.force_login(self.membro_sala1)
        self.assertNotIn('unidade', self.client.get(reverse('chamado_criar')).context['form'].fields)
        self.client.post(reverse('chamado_criar'), self.dados(unidade=self.sala3.pk))  # tentativa de forçar outra unidade
        self.assertEqual(Chamado.objects.get().unidade, self.sala1)

    def test_empresa_com_uma_unidade_tambem_usa_a_unica(self):
        self.client.force_login(self.resp_outra)
        self.client.post(reverse('chamado_criar'), self.dados())
        self.assertEqual(Chamado.objects.get().unidade, self.sala3)

    def test_empresa_sem_nenhuma_unidade_nao_abre_chamado(self):
        vazia = Locatario.objects.create(razao_social='Sem unidades')
        usuario = Usuario.objects.create_user('vazio', password='x', perfil=L, locatario=vazia)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(reverse('chamado_criar')).status_code, 403)


class CadastroDoLocatarioComVariasUnidadesTests(Base):
    def dados(self, **extra):
        dados = dict(razao_social='Nova Empresa', cnpj='', contato='', email_cobranca='', telefone='', ativo='on')
        dados.update(extra)
        return dados

    def test_formulario_so_oferece_unidades_livres(self):
        self.client.force_login(self.administrativo)
        form = self.client.get(reverse('locatario_criar')).context['form']
        oferecidas = set(form.fields['unidades'].queryset.values_list('nome', flat=True))
        self.assertIn('Sala 04', oferecidas)
        self.assertNotIn('Sala 01', oferecidas)  # já é da Clínica
        self.assertNotIn('Sala 03', oferecidas)  # já é da Outra Empresa

    def test_cadastra_locatario_com_varias_unidades(self):
        sala5, sala6 = Unidade.objects.get(nome='Sala 05'), Unidade.objects.get(nome='Sala 06')
        for usuario in (self.sindico, self.administrativo):
            self.client.force_login(usuario)
            resposta = self.client.post(reverse('locatario_criar'), self.dados(
                razao_social=f'Empresa de {usuario.username}', unidades=[sala5.pk, sala6.pk] if usuario == self.sindico else []))
            self.assertEqual(resposta.status_code, 302)
        novo = Locatario.objects.get(razao_social='Empresa de sindico')
        self.assertEqual({u.nome for u in novo.unidades.all()}, {'Sala 05', 'Sala 06'})
        self.assertEqual(Locatario.objects.get(razao_social='Empresa de adm').unidades.count(), 0)  # unidades são opcionais

    def test_recusa_unidade_que_ja_e_de_outro_locatario(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('locatario_criar'), self.dados(unidades=[self.sala3.pk]))
        self.assertFalse(Locatario.objects.filter(razao_social='Nova Empresa').exists())
        self.sala3.refresh_from_db()
        self.assertEqual(self.sala3.locatario, self.outra)

    def test_edita_acrescentando_e_tirando_unidades(self):
        self.client.force_login(self.sindico)
        url = reverse('locatario_editar', args=[self.clinica.pk])
        form = self.client.get(url).context['form']
        self.assertEqual(set(form.initial['unidades']), {u.pk for u in self.minhas})  # já vem marcado
        self.client.post(url, self.dados(razao_social='Clínica Vida', unidades=[self.terreo.pk, self.sala1.pk, self.sala4.pk]))
        self.assertEqual({u.nome for u in self.clinica.unidades.all()}, {'Térreo (clínica)', 'Sala 01', 'Sala 04'})
        self.sala2.refresh_from_db()
        self.assertIsNone(self.sala2.locatario)  # ficou livre

    def test_nao_tira_unidade_com_contrato_vigente(self):
        self.contrato(self.sala1, self.clinica)
        self.client.force_login(self.sindico)
        resposta = self.client.post(reverse('locatario_editar', args=[self.clinica.pk]),
                                    self.dados(razao_social='Clínica Vida', unidades=[self.terreo.pk, self.sala2.pk]))
        self.assertContains(resposta, 'Há contrato vigente em: Sala 01')
        self.sala1.refresh_from_db()
        self.assertEqual(self.sala1.locatario, self.clinica)

    def test_lista_de_locatarios_mostra_todas_as_unidades(self):
        self.client.force_login(self.sindico)
        pagina = self.client.get(reverse('locatario_lista'))
        self.assertContains(pagina, 'Térreo (clínica)')
        self.assertContains(pagina, 'Sala 01')
        self.assertContains(pagina, 'Sala 02')

    def test_desmarcar_tudo_libera_as_unidades(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('locatario_editar', args=[self.clinica.pk]), self.dados(razao_social='Clínica Vida'))
        self.assertEqual(self.clinica.unidades.count(), 0)

    def test_so_quem_pode_editar_locatarios_usa_o_formulario(self):
        self.client.force_login(self.resp)
        self.assertEqual(self.client.get(reverse('locatario_editar', args=[self.clinica.pk])).status_code, 403)


class ContratosDeVariasUnidadesTests(Base):
    def test_um_contrato_por_unidade_para_o_mesmo_locatario(self):
        for unidade in self.minhas:
            self.contrato(unidade, self.clinica)
        self.assertEqual(Contrato.vigentes().filter(locatario=self.clinica).count(), 3)

    def test_contrato_nao_toma_a_unidade_de_outro_locatario(self):
        contrato = Contrato(unidade=self.sala1, locatario=self.outra, status='vigente', data_inicio=date(2026, 1, 1),
                            valor_aluguel=Decimal('1'))
        with self.assertRaises(ValidationError) as erro:
            contrato.full_clean()
        self.assertIn('Clínica Vida', str(erro.exception))

    def test_contrato_do_proprio_locatario_na_unidade_dele_e_valido(self):
        contrato = Contrato(unidade=self.sala1, locatario=self.clinica, status='vigente', data_inicio=date(2026, 1, 1),
                            valor_aluguel=Decimal('1'))
        contrato.full_clean()

    def test_encerrar_um_contrato_solta_so_aquela_unidade(self):
        contratos = [self.contrato(unidade, self.clinica) for unidade in self.minhas]
        contratos[0].status = 'encerrado'
        contratos[0].save()
        self.terreo.refresh_from_db()
        self.sala1.refresh_from_db()
        self.assertIsNone(self.terreo.locatario)
        self.assertEqual(self.sala1.locatario, self.clinica)
        self.assertEqual(len(self.resp.unidades_ids()), 2)  # o acesso encolhe junto


class UsuariosComEmpresaTests(Base):
    def dados(self, **extra):
        dados = dict(first_name='Paula', last_name='Souza', username='paula', email='paula@exemplo.com',
                     telefone='', perfil='locatario', locatario='', unidade='')
        dados.update(extra)
        return dados

    def criar(self, **extra):
        self.client.force_login(self.sindico)
        return self.client.post(reverse('usuario_criar'), self.dados(**extra))

    def test_responsavel_pela_empresa_sem_unidade(self):
        self.criar(locatario=self.clinica.pk)
        usuario = Usuario.objects.get(username='paula')
        self.assertEqual((usuario.locatario, usuario.unidade), (self.clinica, None))
        self.assertEqual(len(usuario.unidades_ids()), 3)

    def test_membro_limitado_a_uma_unidade_da_empresa(self):
        self.criar(perfil='membro', locatario=self.clinica.pk, unidade=self.sala2.pk)
        usuario = Usuario.objects.get(username='paula')
        self.assertEqual(usuario.unidades_ids(), [self.sala2.pk])

    def test_exige_empresa_ou_unidade(self):
        self.criar()
        self.assertFalse(Usuario.objects.filter(username='paula').exists())

    def test_nao_deixa_unidade_de_outra_empresa(self):
        self.criar(locatario=self.clinica.pk, unidade=self.sala3.pk)
        self.assertFalse(Usuario.objects.filter(username='paula').exists())

    def test_perfil_de_equipe_descarta_empresa_e_unidade(self):
        self.criar(perfil='equipe', locatario=self.clinica.pk, unidade=self.sala1.pk)
        usuario = Usuario.objects.get(username='paula')
        self.assertEqual((usuario.locatario, usuario.unidade), (None, None))

    def test_so_a_unidade_continua_valendo_para_quem_ja_existe(self):
        self.criar(unidade=self.sala4.pk)
        self.assertEqual(Usuario.objects.get(username='paula').unidades_ids(), [self.sala4.pk])

    def test_lista_de_usuarios_mostra_a_empresa(self):
        self.client.force_login(self.sindico)
        pagina = self.client.get(reverse('usuarios'))
        self.assertContains(pagina, 'Clínica Vida')
        self.assertContains(pagina, 'só Sala 01')
        self.assertContains(pagina, 'todas as unidades')


class PessoasDaEmpresaTests(Base):
    def dados(self, **extra):
        dados = dict(first_name='João', last_name='Silva', username='Joao.Silva', email='joao@exemplo.com', telefone='')
        dados.update(extra)
        return dados

    def test_formulario_do_responsavel_de_varias_unidades_oferece_limitar(self):
        self.client.force_login(self.resp)
        form = self.client.get(reverse('membro_criar')).context['form']
        self.assertEqual(sorted(form.fields['unidade'].queryset.values_list('pk', flat=True)), sorted(u.pk for u in self.minhas))
        self.assertFalse(form.fields['unidade'].required)

    def test_cria_membro_para_toda_a_empresa(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_criar'), self.dados())
        novo = Usuario.objects.get(username='joao.silva')
        self.assertEqual((novo.perfil, novo.locatario, novo.unidade), ('membro', self.clinica, None))
        self.assertEqual(len(novo.unidades_ids()), 3)

    def test_cria_membro_limitado_a_uma_unidade(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_criar'), self.dados(unidade=self.sala2.pk))
        self.assertEqual(Usuario.objects.get(username='joao.silva').unidades_ids(), [self.sala2.pk])

    def test_nao_limita_a_unidade_de_outra_empresa(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_criar'), self.dados(unidade=self.sala3.pk))
        self.assertFalse(Usuario.objects.filter(username='joao.silva').exists())

    def test_nao_escolhe_perfil_nem_empresa(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_criar'), self.dados(perfil='sindico', locatario=self.outra.pk, is_superuser='on'))
        novo = Usuario.objects.get(username='joao.silva')
        self.assertEqual((novo.perfil, novo.locatario, novo.is_superuser), ('membro', self.clinica, False))

    def test_empresa_de_uma_unidade_nao_pergunta_unidade(self):
        self.client.force_login(self.resp_outra)
        self.assertNotIn('unidade', self.client.get(reverse('membro_criar')).context['form'].fields)
        self.client.post(reverse('membro_criar'), self.dados())
        self.assertEqual(Usuario.objects.get(username='joao.silva').unidades_ids(), [self.sala3.pk])

    def test_cadastro_antigo_so_com_unidade_continua_funcionando(self):
        self.client.force_login(self.antigo)
        self.client.post(reverse('membro_criar'), self.dados())
        novo = Usuario.objects.get(username='joao.silva')
        self.assertEqual((novo.unidade, novo.locatario), (self.sala4, None))

    def test_lista_so_tem_as_pessoas_da_propria_empresa(self):
        self.client.force_login(self.resp)
        pagina = self.client.get(reverse('membros'))
        self.assertContains(pagina, 'todas')
        self.assertContains(pagina, 'so1')
        self.assertContains(pagina, 'só Sala 01')
        self.assertNotContains(pagina, 'resp2')
        self.assertNotContains(pagina, 'antigo')

    def test_nao_mexe_em_pessoa_de_outra_empresa(self):
        estranho = Usuario.objects.create_user('estranho', password='x', perfil=M, locatario=self.outra)
        self.client.force_login(self.resp)
        self.assertEqual(self.client.post(reverse('membro_alternar', args=[estranho.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('membro_senha', args=[estranho.pk])).status_code, 404)

    def test_responsavel_limitado_a_uma_unidade_so_mexe_nas_pessoas_dela(self):
        restrito = Usuario.objects.create_user('restrito', password='x', perfil=L, locatario=self.clinica, unidade=self.sala1)
        self.client.force_login(restrito)
        self.assertEqual(self.client.post(reverse('membro_alternar', args=[self.membro_todas.pk])).status_code, 404)
        self.client.post(reverse('membro_alternar', args=[self.membro_sala1.pk]))
        self.membro_sala1.refresh_from_db()
        self.assertFalse(self.membro_sala1.is_active)
        self.client.post(reverse('membro_criar'), self.dados())
        self.assertEqual(Usuario.objects.get(username='joao.silva').unidades_ids(), [self.sala1.pk])  # nunca mais que o responsável

    def test_pessoas_da_empresa_do_cadastro_antigo(self):
        colega = Usuario.objects.create_user('colega', password='x', perfil=M, unidade=self.sala4)
        self.assertEqual(set(pessoas_da_empresa(self.antigo)), {self.antigo, colega})

    def test_contagem_na_home(self):
        self.assertEqual(resumos(self.resp)['Pessoas da empresa'], '3 pessoas com acesso')
