from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from cadastros.models import Locatario, Unidade
from contratos.models import Contrato
from financeiro.calculo import calcular_rateio
from financeiro.models import Cobranca, Competencia, Despesa

from . import acesso
from .models import Usuario
from .views import MODULOS
from .usuarios import ALFABETO, gerar_senha_provisoria

RAPIDO = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])

S, A, E, C, L, M = (acesso.SINDICO, acesso.ADMINISTRATIVO, acesso.EQUIPE, acesso.CONSULTA, acesso.LOCATARIO, acesso.MEMBRO)

# Quem recebe 200 em cada tela. Quem não está na lista recebe 403.
MATRIZ = {
    'unidade_lista': {S, A},
    'unidade_criar': {S},
    'locatario_lista': {S, A},
    'locatario_criar': {S, A},
    'contrato_lista': {S, A, C, L},
    'contrato_criar': {S, A},
    'competencia_lista': {S, A},
    'competencia_nova': {S, A},
    'cobranca_lista': {S, A, C, L},
    'aluguel_gerar': {S, A},
    'relatorios': {S, A, C},
    'relatorio_despesas': {S, A, C},
    'relatorio_inadimplencia': {S, A, C},
    'chamado_lista': {S, E, L, M},
    'chamado_criar': {S, E, L, M},
    'correspondencia_lista': {S, A, E, L, M},
    'correspondencia_criar': {S, A, E},
    'mural': acesso.TODOS,
    'contato_criar': {S, A, E},
    'usuarios': {S},
    'usuario_criar': {S},
    'membros': {L},
    'membro_criar': {L},
    'trocar_senha': acesso.TODOS,
    'inicio': acesso.TODOS,
}


@RAPIDO
class Base(TestCase):
    def setUp(self):
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        unidade = {L: self.sala1, M: self.sala1}
        self.u = {
            perfil: Usuario.objects.create_user(perfil, password='senha-segura-1', perfil=perfil, unidade=unidade.get(perfil),
                                                first_name=perfil.capitalize(), email=f'{perfil}@exemplo.com')
            for perfil in acesso.TODOS
        }


class TabelaDePermissoesTests(SimpleTestCase):
    def test_todo_perfil_listado_existe(self):
        for nome, perfis in acesso.PERMISSOES.items():
            self.assertTrue(perfis <= acesso.TODOS, nome)
            self.assertTrue(perfis, f'{nome} não pode ficar sem ninguém')

    def test_perfis_da_tabela_batem_com_os_do_modelo(self):
        self.assertEqual(acesso.TODOS, set(Usuario.Perfil.values))

    def test_permissao_desconhecida_da_erro_em_vez_de_negar_em_silencio(self):
        with self.assertRaises(KeyError):
            acesso.tem_permissao('sindico', 'nao_existe')

    def test_modulos_da_home_so_usam_permissoes_que_existem(self):
        for _, itens in MODULOS:
            for modulo in itens:
                for nome in modulo['permissoes']:
                    self.assertIn(nome, acesso.PERMISSOES, modulo['titulo'])

    def test_regras_de_negocio_combinadas_com_o_sindico(self):
        P = acesso.PERMISSOES
        self.assertEqual(P['rateio_aprovar'], {S, A})                   # o administrativo também libera as cobranças
        self.assertIn(E, P['correspondencias_gerenciar'])               # a portaria cuida das correspondências
        self.assertEqual(P['chamados_gerenciar'], {S, E})
        self.assertNotIn(E, P['financeiro_preparar'])                   # mas a portaria não vê dinheiro
        self.assertNotIn(C, P['financeiro_preparar'])                   # consulta nunca escreve
        for nome, perfis in P.items():
            if nome.endswith(('_editar', '_gerenciar', '_preparar', '_aprovar')):
                self.assertNotIn(C, perfis, nome)

    def test_senha_provisoria_nao_tem_caracteres_confusos(self):
        senha = gerar_senha_provisoria()
        self.assertEqual(len(senha), 10)
        self.assertTrue(set(senha) <= set(ALFABETO))
        self.assertFalse(set('0O1lI') & set(ALFABETO))
        self.assertNotEqual(gerar_senha_provisoria(), gerar_senha_provisoria())


class MatrizDeAcessoTests(Base):
    def test_cada_perfil_acessa_so_o_que_deve(self):
        for rota, permitidos in MATRIZ.items():
            for perfil, usuario in self.u.items():
                self.client.force_login(usuario)
                esperado = 200 if perfil in permitidos else 403
                resposta = self.client.get(reverse(rota))
                self.assertEqual(resposta.status_code, esperado, f'{rota} para {perfil}')

    def test_anonimo_vai_para_o_login_em_todas_as_telas(self):
        for rota in MATRIZ:
            resposta = self.client.get(reverse(rota))
            self.assertEqual(resposta.status_code, 302, rota)
            self.assertIn('/entrar/', resposta['Location'], rota)

    def test_membro_ou_responsavel_sem_unidade_nao_abre_chamado(self):
        sem_unidade = Usuario.objects.create_user('sem', password='x', perfil=M)
        self.client.force_login(sem_unidade)
        self.assertEqual(self.client.get(reverse('chamado_criar')).status_code, 403)
        sem_unidade.perfil = L
        sem_unidade.save()
        self.assertEqual(self.client.get(reverse('membros')).status_code, 403)

    def test_usuario_desativado_perde_o_acesso_na_hora(self):
        self.client.force_login(self.u[S])
        self.assertEqual(self.client.get(reverse('inicio')).status_code, 200)
        Usuario.objects.filter(pk=self.u[S].pk).update(is_active=False)
        self.assertEqual(self.client.get(reverse('inicio')).status_code, 302)


class ConsultaSoLeituraTests(Base):
    def setUp(self):
        super().setUp()
        locatario = Locatario.objects.create(razao_social='Empresa X')
        self.contrato = Contrato.objects.create(
            unidade=self.sala1, locatario=locatario, status='vigente', data_inicio=date(2026, 1, 1), valor_aluguel=Decimal('1000'))
        self.cobranca = Cobranca.objects.create(
            unidade=self.sala1, locatario=locatario, tipo='aluguel', referencia=date(2026, 9, 1),
            valor=Decimal('1000'), vencimento=date(2026, 9, 10))

    def test_consulta_ve_mas_nao_ve_botoes_de_acao(self):
        self.client.force_login(self.u[C])
        contratos = self.client.get(reverse('contrato_lista'))
        self.assertContains(contratos, 'Empresa X')
        self.assertNotContains(contratos, 'Novo contrato')
        cobrancas = self.client.get(reverse('cobranca_lista'))
        self.assertContains(cobrancas, 'Sala 01')
        self.assertNotContains(cobrancas, reverse('cobranca_pagar', args=[self.cobranca.pk]))
        detalhe = self.client.get(reverse('contrato_detalhe', args=[self.contrato.pk]))
        self.assertEqual(detalhe.status_code, 200)
        self.assertNotContains(detalhe, reverse('contrato_editar', args=[self.contrato.pk]))

    def test_consulta_nao_consegue_alterar_nada(self):
        self.client.force_login(self.u[C])
        self.assertEqual(self.client.post(reverse('cobranca_pagar', args=[self.cobranca.pk]),
                                          {'pago_em': '2026-09-10', 'valor_pago': '1000'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('cobranca_cancelar', args=[self.cobranca.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('contrato_editar', args=[self.contrato.pk]), {}).status_code, 403)
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.status, 'aberta')

    def test_consulta_exporta_relatorio_em_csv(self):
        self.client.force_login(self.u[C])
        self.assertEqual(self.client.get(reverse('relatorio_contratos') + '?formato=csv').status_code, 200)


class AdministrativoTests(Base):
    def setUp(self):
        super().setUp()
        for un, nome in ((Unidade.objects.get(nome='Térreo (clínica)'), 'Clínica'), (self.sala1, 'Empresa 1')):
            Contrato.objects.create(unidade=un, locatario=Locatario.objects.create(razao_social=nome), status='vigente',
                                    data_inicio=date(2026, 1, 1), valor_aluguel=Decimal('1000'))
        self.comp = Competencia.objects.create(referencia=date(2026, 9, 1))
        Despesa.objects.create(competencia=self.comp, descricao='x', valor=Decimal('1000'), tipo='demais',
                               vencimento=date(2026, 9, 5), chave='k1')

    def test_administrativo_importa_calcula_e_aprova_sem_o_sindico(self):
        self.client.force_login(self.u[A])
        self.client.post(reverse('rateio_calcular', args=[self.comp.pk]))
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.CALCULADA)
        self.client.post(reverse('rateio_aprovar', args=[self.comp.pk]))
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.APROVADA)
        self.assertEqual(self.comp.rateio.aprovado_por, self.u[A])  # fica registrado quem aprovou
        self.assertEqual(Cobranca.objects.count(), 2)

    def test_quem_nao_prepara_o_financeiro_nao_aprova(self):
        calcular_rateio(self.comp)
        for perfil in (E, C, L, M):
            self.client.force_login(self.u[perfil])
            self.assertEqual(self.client.post(reverse('rateio_aprovar', args=[self.comp.pk])).status_code, 403, perfil)
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.CALCULADA)
        self.assertFalse(Cobranca.objects.exists())

    def test_botao_de_aprovar_aparece_para_o_sindico_e_o_administrativo(self):
        calcular_rateio(self.comp)
        url = reverse('competencia_detalhe', args=[self.comp.pk])
        for perfil in (S, A):
            self.client.force_login(self.u[perfil])
            self.assertContains(self.client.get(url), 'Aprovar e gerar cobranças', msg_prefix=perfil)

    def test_administrativo_nao_mexe_em_areas_nem_em_usuarios(self):
        self.client.force_login(self.u[A])
        unidade = Unidade.objects.get(nome='Sala 01')
        self.assertEqual(self.client.post(reverse('unidade_editar', args=[unidade.pk]), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse('usuario_criar'), {}).status_code, 403)

    def test_administrativo_cadastra_locatario_e_contrato(self):
        self.client.force_login(self.u[A])
        resposta = self.client.post(reverse('locatario_criar'), {'razao_social': 'Nova Empresa', 'ativo': 'on'})
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(Locatario.objects.filter(razao_social='Nova Empresa').exists())


class EquipeEPortariaTests(Base):
    def test_equipe_cuida_de_chamados_mural_e_correspondencias(self):
        self.client.force_login(self.u[E])
        for rota in ('chamado_lista', 'contato_criar', 'correspondencia_lista', 'correspondencia_criar'):
            self.assertEqual(self.client.get(reverse(rota)).status_code, 200, rota)

    def test_portaria_registra_e_da_baixa_em_correspondencia(self):
        from django.utils import timezone
        from correspondencias.models import Correspondencia
        self.client.force_login(self.u[E])
        empresa = Locatario.objects.create(razao_social='Empresa Teste')
        Unidade.objects.filter(pk=self.sala1.pk).update(locatario=empresa)
        agora = timezone.localtime().strftime('%Y-%m-%dT%H:%M')
        self.client.post(reverse('correspondencia_criar'), dict(
            locatario=empresa.pk, tipo='encomenda', destinatario='Maria', recebido_em=agora, observacoes=''))
        item = Correspondencia.objects.get()
        self.assertEqual(item.registrado_por, self.u[E])
        self.client.post(reverse('correspondencia_baixa', args=[item.pk]), dict(
            retirada_por='João', retirada_em=agora, baixa_obs=''))
        item.refresh_from_db()
        self.assertEqual((item.status, item.baixa_por), ('retirada', self.u[E]))

    def test_portaria_continua_sem_acesso_a_dinheiro_e_contratos(self):
        self.client.force_login(self.u[E])
        for rota in ('cobranca_lista', 'competencia_lista', 'contrato_lista', 'relatorios', 'unidade_lista', 'usuarios'):
            self.assertEqual(self.client.get(reverse(rota)).status_code, 403, rota)
        resposta = self.client.get(reverse('inicio'))
        self.assertContains(resposta, 'Correspondências</h3>')
        self.assertNotContains(resposta, 'Cobranças</h3>')


class HomePorPerfilTests(Base):
    def test_cartoes_de_cada_perfil(self):
        esperado = {
            S: {'Unidades', 'Empresas', 'Usuários e acessos', 'Contratos', 'Condomínio e rateio', 'Cobranças',
                'Chamados de manutenção', 'Correspondências', 'Mural de contatos', 'Relatórios'},
            A: {'Unidades', 'Empresas', 'Contratos', 'Condomínio e rateio', 'Cobranças', 'Correspondências',
                'Mural de contatos', 'Relatórios'},
            E: {'Chamados de manutenção', 'Correspondências', 'Mural de contatos'},
            C: {'Contratos', 'Cobranças', 'Mural de contatos', 'Relatórios'},
            L: {'Pessoas da empresa', 'Contratos', 'Cobranças', 'Chamados de manutenção', 'Correspondências', 'Mural de contatos'},
            M: {'Chamados de manutenção', 'Correspondências', 'Mural de contatos'},
        }
        todos = {m['titulo'] for _, itens in MODULOS for m in itens}
        for perfil, titulos in esperado.items():
            self.client.force_login(self.u[perfil])
            resposta = self.client.get(reverse('inicio'))
            for titulo in todos:
                marca = f'{titulo}</h3>'
                if titulo in titulos:
                    self.assertContains(resposta, marca, msg_prefix=f'{perfil} deveria ver {titulo}')
                else:
                    self.assertNotContains(resposta, marca, msg_prefix=f'{perfil} não deveria ver {titulo}')


class GestaoDeUsuariosTests(Base):
    def dados(self, **extra):
        dados = dict(first_name='Paula', last_name='Souza', username='Paula.Souza', email='paula@exemplo.com',
                     telefone='', perfil='equipe', unidade='')
        dados.update(extra)
        return dados

    def criar(self, **extra):
        self.client.force_login(self.u[S])
        return self.client.post(reverse('usuario_criar'), self.dados(**extra))

    def test_cria_usuario_e_mostra_a_senha_provisoria_uma_so_vez(self):
        resposta = self.criar()
        # fetch_redirect_response=False: seguir o redirecionamento gastaria a única exibição da senha
        self.assertRedirects(resposta, reverse('senha_provisoria'), fetch_redirect_response=False)
        pagina = self.client.get(reverse('senha_provisoria'))
        self.assertContains(pagina, 'paula.souza')
        senha = pagina.context['senha']
        self.assertEqual(len(senha), 10)
        self.assertRedirects(self.client.get(reverse('senha_provisoria')), reverse('inicio'))  # não aparece de novo
        novo = Usuario.objects.get(username='paula.souza')
        self.assertTrue(novo.trocar_senha)
        self.assertEqual((novo.perfil, novo.unidade), ('equipe', None))
        self.assertTrue(self.client.login(username='paula.souza', password=senha))

    def test_senha_provisoria_nao_fica_gravada_no_banco_em_texto(self):
        self.criar()
        senha = self.client.get(reverse('senha_provisoria')).context['senha']
        novo = Usuario.objects.get(username='paula.souza')
        self.assertNotIn(senha, novo.password)

    def test_responsavel_e_membro_exigem_unidade(self):
        self.criar(perfil='locatario', unidade='')
        self.assertFalse(Usuario.objects.filter(username='paula.souza').exists())
        resposta = self.criar(perfil='membro', unidade=self.sala2.pk)
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Usuario.objects.get(username='paula.souza').unidade, self.sala2)

    def test_perfil_sem_unidade_descarta_a_unidade_informada(self):
        self.criar(perfil='administrativo', unidade=self.sala2.pk)
        self.assertIsNone(Usuario.objects.get(username='paula.souza').unidade)

    def test_usuario_repetido_e_email_obrigatorio(self):
        self.criar()
        self.criar(first_name='Outra')
        self.assertEqual(Usuario.objects.filter(username='paula.souza').count(), 1)
        self.criar(username='sememail', email='')
        self.assertFalse(Usuario.objects.filter(username='sememail').exists())

    def test_usuario_novo_so_entra_trocando_a_senha(self):
        self.criar()
        senha = self.client.get(reverse('senha_provisoria')).context['senha']
        self.client.logout()
        self.client.login(username='paula.souza', password=senha)
        self.assertRedirects(self.client.get(reverse('inicio')), reverse('trocar_senha'))
        self.assertRedirects(self.client.get(reverse('chamado_lista')), reverse('trocar_senha'))
        self.assertEqual(self.client.get(reverse('trocar_senha')).status_code, 200)

    def test_nao_muda_o_proprio_perfil(self):
        self.client.force_login(self.u[S])
        dados = self.dados(first_name='Síndico', username=self.u[S].username, email='s@exemplo.com', perfil='consulta')
        self.client.post(reverse('usuario_editar', args=[self.u[S].pk]), dados)
        self.u[S].refresh_from_db()
        self.assertEqual(self.u[S].perfil, 'sindico')

    def test_nao_tira_o_perfil_do_ultimo_sindico(self):
        outro = Usuario.objects.create_user('outro', password='x', perfil='sindico', first_name='Outro', email='o@e.com')
        Usuario.objects.filter(pk=self.u[S].pk).update(is_active=False)  # só sobra "outro"
        self.client.force_login(outro)
        dados = self.dados(first_name='Outro', username='outro', email='o@e.com', perfil='equipe')
        self.client.post(reverse('usuario_editar', args=[outro.pk]), dados)
        outro.refresh_from_db()
        self.assertEqual(outro.perfil, 'sindico')

    def test_desativa_e_reativa_e_o_acesso_cai(self):
        self.client.force_login(self.u[S])
        alvo = self.u[E]
        self.client.post(reverse('usuario_alternar', args=[alvo.pk]))
        alvo.refresh_from_db()
        self.assertFalse(alvo.is_active)
        self.assertFalse(self.client.login(username='equipe', password='senha-segura-1'))
        self.client.force_login(self.u[S])
        self.client.post(reverse('usuario_alternar', args=[alvo.pk]))
        alvo.refresh_from_db()
        self.assertTrue(alvo.is_active)

    def test_nao_desativa_a_si_mesmo_nem_o_ultimo_sindico(self):
        self.client.force_login(self.u[S])
        self.client.post(reverse('usuario_alternar', args=[self.u[S].pk]))
        self.u[S].refresh_from_db()
        self.assertTrue(self.u[S].is_active)
        outro = Usuario.objects.create_user('outro', password='x', perfil='sindico')
        Usuario.objects.exclude(pk__in=[outro.pk]).update(is_active=False)
        self.client.force_login(outro)
        self.client.post(reverse('usuario_alternar', args=[outro.pk]))
        outro.refresh_from_db()
        self.assertTrue(outro.is_active)

    def test_nova_senha_invalida_a_antiga(self):
        self.client.force_login(self.u[S])
        alvo = self.u[L]
        self.client.post(reverse('usuario_senha', args=[alvo.pk]))
        senha = self.client.get(reverse('senha_provisoria')).context['senha']
        self.assertFalse(self.client.login(username='locatario', password='senha-segura-1'))
        self.assertTrue(self.client.login(username='locatario', password=senha))
        alvo.refresh_from_db()
        self.assertTrue(alvo.trocar_senha)

    def test_nao_gera_senha_provisoria_para_si_mesmo(self):
        self.client.force_login(self.u[S])
        resposta = self.client.post(reverse('usuario_senha', args=[self.u[S].pk]))
        self.assertRedirects(resposta, reverse('usuarios'))
        self.assertTrue(self.client.login(username='sindico', password='senha-segura-1'))

    def test_so_o_sindico_gerencia_usuarios(self):
        alvo = self.u[M]
        for perfil in (A, E, C, L, M):
            self.client.force_login(self.u[perfil])
            self.assertEqual(self.client.post(reverse('usuario_criar'), self.dados()).status_code, 403, perfil)
            self.assertEqual(self.client.post(reverse('usuario_alternar', args=[alvo.pk])).status_code, 403, perfil)
            self.assertEqual(self.client.post(reverse('usuario_senha', args=[alvo.pk])).status_code, 403, perfil)
            self.assertEqual(self.client.post(reverse('usuario_editar', args=[alvo.pk]), self.dados()).status_code, 403, perfil)
        alvo.refresh_from_db()
        self.assertTrue(alvo.is_active)

    def test_lista_filtra_por_perfil_e_situacao(self):
        Usuario.objects.filter(pk=self.u[E].pk).update(is_active=False)
        self.client.force_login(self.u[S])
        ativos = self.client.get(reverse('usuarios'))
        self.assertNotContains(ativos, '>equipe<')
        inativos = self.client.get(reverse('usuarios') + '?situacao=inativos')
        self.assertContains(inativos, '>equipe<')
        so_membros = self.client.get(reverse('usuarios') + '?perfil=membro')
        self.assertContains(so_membros, '>membro<')
        self.assertNotContains(so_membros, '>consulta<')


class MembrosDaUnidadeTests(Base):
    def setUp(self):
        super().setUp()
        self.resp = self.u[L]
        self.outro_resp = Usuario.objects.create_user('resp2', password='x', perfil=L, unidade=self.sala2, first_name='Outro')
        self.membro_alheio = Usuario.objects.create_user('alheio', password='x', perfil=M, unidade=self.sala2, first_name='Alheio')

    def dados(self, **extra):
        dados = dict(first_name='João', last_name='Silva', username='Joao.Silva', email='joao@exemplo.com', telefone='')
        dados.update(extra)
        return dados

    def test_responsavel_cadastra_membro_da_propria_unidade(self):
        self.client.force_login(self.resp)
        resposta = self.client.post(reverse('membro_criar'), self.dados())
        self.assertRedirects(resposta, reverse('senha_provisoria'), fetch_redirect_response=False)
        novo = Usuario.objects.get(username='joao.silva')
        self.assertEqual((novo.perfil, novo.unidade, novo.trocar_senha), ('membro', self.sala1, True))
        self.assertContains(self.client.get(reverse('senha_provisoria')), 'joao.silva')

    def test_nao_da_para_escolher_perfil_nem_unidade(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_criar'), self.dados(perfil='sindico', unidade=self.sala2.pk, is_staff='on', is_superuser='on'))
        novo = Usuario.objects.get(username='joao.silva')
        self.assertEqual((novo.perfil, novo.unidade, novo.is_staff, novo.is_superuser), ('membro', self.sala1, False, False))

    def test_membro_novo_ve_so_o_da_propria_unidade(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_criar'), self.dados())
        senha = self.client.get(reverse('senha_provisoria')).context['senha']
        self.client.logout()
        self.client.login(username='joao.silva', password=senha)
        self.client.post(reverse('trocar_senha'), {'old_password': senha, 'new_password1': 'Outra-senha-forte-9', 'new_password2': 'Outra-senha-forte-9'})
        self.assertEqual(self.client.get(reverse('chamado_lista')).status_code, 200)
        self.assertEqual(self.client.get(reverse('membros')).status_code, 403)
        self.assertEqual(self.client.get(reverse('usuarios')).status_code, 403)

    def test_responsavel_so_enxerga_e_mexe_nos_membros_da_propria_unidade(self):
        proprio = Usuario.objects.create_user('meu', password='x', perfil=M, unidade=self.sala1, first_name='Meu')
        self.client.force_login(self.resp)
        pagina = self.client.get(reverse('membros'))
        self.assertContains(pagina, 'meu')
        self.assertNotContains(pagina, 'alheio')
        for rota in ('membro_alternar', 'membro_senha'):
            self.assertEqual(self.client.post(reverse(rota, args=[self.membro_alheio.pk])).status_code, 404, rota)
            self.assertEqual(self.client.post(reverse(rota, args=[self.outro_resp.pk])).status_code, 404, rota)
            self.assertEqual(self.client.post(reverse(rota, args=[self.u[E].pk])).status_code, 404, rota)
        self.client.post(reverse('membro_alternar', args=[proprio.pk]))
        proprio.refresh_from_db()
        self.assertFalse(proprio.is_active)
        self.membro_alheio.refresh_from_db()
        self.assertTrue(self.membro_alheio.is_active)

    def test_responsavel_nao_desativa_a_si_mesmo_nem_outro_responsavel(self):
        self.client.force_login(self.resp)
        self.assertEqual(self.client.post(reverse('membro_alternar', args=[self.resp.pk])).status_code, 404)
        self.resp.refresh_from_db()
        self.assertTrue(self.resp.is_active)

    def test_desativar_membro_derruba_o_login(self):
        membro = self.u[M]
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_alternar', args=[membro.pk]))
        self.assertFalse(self.client.login(username='membro', password='senha-segura-1'))

    def test_nova_senha_do_membro(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('membro_senha', args=[self.u[M].pk]))
        senha = self.client.get(reverse('senha_provisoria')).context['senha']
        self.assertTrue(self.client.login(username='membro', password=senha))

    def test_lista_mostra_o_responsavel_e_os_membros_ativos_e_inativos(self):
        Usuario.objects.filter(pk=self.u[M].pk).update(is_active=False)
        self.client.force_login(self.resp)
        pagina = self.client.get(reverse('membros'))
        self.assertContains(pagina, 'responsável')
        self.assertContains(pagina, 'desativado')
        self.assertContains(pagina, 'Reativar')


class TrocaDeSenhaTests(Base):
    def provisoria(self, perfil=M):
        usuario = self.u[perfil]
        usuario.set_password('Provisoria9')
        usuario.trocar_senha = True
        usuario.save()
        self.client.force_login(usuario)
        return usuario

    def test_enquanto_a_senha_for_provisoria_so_a_troca_e_o_sair_funcionam(self):
        self.provisoria()
        for rota in ('inicio', 'chamado_lista', 'mural', 'correspondencia_lista'):
            self.assertRedirects(self.client.get(reverse(rota)), reverse('trocar_senha'), msg_prefix=rota)
        self.assertEqual(self.client.get(reverse('trocar_senha')).status_code, 200)
        self.assertEqual(self.client.post(reverse('logout')).status_code, 302)

    def test_troca_correta_libera_o_sistema(self):
        usuario = self.provisoria()
        resposta = self.client.post(reverse('trocar_senha'), {
            'old_password': 'Provisoria9', 'new_password1': 'Minha-senha-forte-7', 'new_password2': 'Minha-senha-forte-7'})
        self.assertRedirects(resposta, reverse('inicio'))
        usuario.refresh_from_db()
        self.assertFalse(usuario.trocar_senha)
        self.assertTrue(usuario.check_password('Minha-senha-forte-7'))
        self.assertEqual(self.client.get(reverse('chamado_lista')).status_code, 200)  # a sessão continua válida

    def test_recusa_senha_fraca_ou_senha_atual_errada(self):
        usuario = self.provisoria()
        casos = [
            {'old_password': 'Provisoria9', 'new_password1': '12345678', 'new_password2': '12345678'},
            {'old_password': 'Provisoria9', 'new_password1': 'curta', 'new_password2': 'curta'},
            {'old_password': 'Provisoria9', 'new_password1': 'Minha-senha-forte-7', 'new_password2': 'Diferente-forte-7'},
            {'old_password': 'errada', 'new_password1': 'Minha-senha-forte-7', 'new_password2': 'Minha-senha-forte-7'},
        ]
        for dados in casos:
            self.client.post(reverse('trocar_senha'), dados)
            usuario.refresh_from_db()
            self.assertTrue(usuario.trocar_senha, dados)
            self.assertTrue(usuario.check_password('Provisoria9'), dados)

    def test_usuario_comum_troca_a_senha_quando_quiser_e_ve_o_link(self):
        self.client.force_login(self.u[S])
        self.assertContains(self.client.get(reverse('inicio')), reverse('trocar_senha'))
        pagina = self.client.get(reverse('trocar_senha'))
        self.assertContains(pagina, 'Alterar minha senha')
        self.assertContains(pagina, 'Cancelar')

    def test_a_sessao_expira_em_12_horas(self):
        from django.conf import settings
        self.assertEqual(settings.SESSION_COOKIE_AGE, 12 * 60 * 60)
