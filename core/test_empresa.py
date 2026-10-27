import importlib
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from contratos.forms import ContratoForm
from contratos.models import Contrato
from correspondencias.models import Correspondencia
from financeiro.models import Cobranca

from .models import Usuario

RAPIDO = override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
L, M, S, A, E = (Usuario.Perfil.LOCATARIO, Usuario.Perfil.MEMBRO, Usuario.Perfil.SINDICO,
                 Usuario.Perfil.ADMINISTRATIVO, Usuario.Perfil.EQUIPE)


def agora():
    return timezone.localtime().strftime('%Y-%m-%dT%H:%M')


@RAPIDO
class Base(TestCase):
    """A Clínica Vida ocupa três unidades; a Outra Empresa ocupa uma; a Sem Unidades não ocupa nenhuma."""

    def setUp(self):
        self.terreo = Unidade.objects.get(nome='Térreo (clínica)')
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.sala3 = Unidade.objects.get(nome='Sala 03')
        self.clinica = Locatario.objects.create(razao_social='Clínica Vida')
        self.outra = Locatario.objects.create(razao_social='Outra Empresa')
        self.vazia = Locatario.objects.create(razao_social='Sem Unidades')
        self.inativa = Locatario.objects.create(razao_social='Empresa Inativa', ativo=False)
        Unidade.objects.filter(pk__in=[self.terreo.pk, self.sala1.pk, self.sala2.pk]).update(locatario=self.clinica)
        Unidade.objects.filter(pk=self.sala3.pk).update(locatario=self.outra)

        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=S, first_name='Síndico')
        self.administrativo = Usuario.objects.create_user('adm', password='x', perfil=A, first_name='Adm')
        self.equipe = Usuario.objects.create_user('portaria', password='x', perfil=E, first_name='Portaria')
        self.resp = Usuario.objects.create_user('resp', password='x', perfil=L, locatario=self.clinica, first_name='Rui')
        self.membro_todas = Usuario.objects.create_user('todas', password='x', perfil=M, locatario=self.clinica)
        self.membro_sala1 = Usuario.objects.create_user('so1', password='x', perfil=M, locatario=self.clinica, unidade=self.sala1)
        self.resp_outra = Usuario.objects.create_user('resp2', password='x', perfil=L, locatario=self.outra)
        self.resp_vazia = Usuario.objects.create_user('resp3', password='x', perfil=L, locatario=self.vazia)
        self.antigo = Usuario.objects.create_user('antigo', password='x', perfil=L, unidade=self.sala3)  # cadastro antigo


class CorrespondenciaPorEmpresaTests(Base):
    def dados(self, **extra):
        dados = dict(locatario=self.clinica.pk, destinatario='', tipo='encomenda', recebido_em=agora(), observacoes='')
        dados.update(extra)
        return dados

    def test_o_formulario_nao_pede_unidade_e_lista_so_empresas_ativas(self):
        self.client.force_login(self.equipe)
        form = self.client.get(reverse('correspondencia_criar')).context['form']
        self.assertNotIn('unidade', form.fields)
        self.assertEqual(form.fields['locatario'].label, 'Destinatário (empresa)')
        nomes = set(form.fields['locatario'].queryset.values_list('razao_social', flat=True))
        self.assertEqual(nomes, {'Clínica Vida', 'Outra Empresa', 'Sem Unidades'})  # a inativa não aparece
        self.assertTrue(form.fields['locatario'].required)

    def test_pagina_do_formulario_mostra_as_empresas_e_nao_fala_de_unidade(self):
        self.client.force_login(self.equipe)
        pagina = self.client.get(reverse('correspondencia_criar'))
        self.assertContains(pagina, 'Clínica Vida')
        self.assertContains(pagina, 'Outra Empresa')
        self.assertNotContains(pagina, 'Unidade</label>')

    def test_registra_para_empresa_com_varias_unidades_sem_chutar_a_unidade(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('correspondencia_criar'), self.dados())
        item = Correspondencia.objects.get()
        self.assertEqual((item.locatario, item.unidade, item.registrado_por), (self.clinica, None, self.equipe))
        self.assertEqual(item.destino, 'Clínica Vida')

    def test_registra_para_empresa_de_uma_unidade_e_descobre_a_unidade(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('correspondencia_criar'), self.dados(locatario=self.outra.pk, destinatario='João'))
        item = Correspondencia.objects.get()
        self.assertEqual((item.locatario, item.unidade, item.destinatario), (self.outra, self.sala3, 'João'))

    def test_empresa_e_obrigatoria_e_a_inativa_e_recusada(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('correspondencia_criar'), self.dados(locatario=''))
        self.client.post(reverse('correspondencia_criar'), self.dados(locatario=self.inativa.pk))
        self.assertFalse(Correspondencia.objects.exists())

    def test_o_nome_no_envelope_e_opcional(self):
        self.client.force_login(self.administrativo)
        resposta = self.client.post(reverse('correspondencia_criar'), self.dados())
        self.assertEqual(resposta.status_code, 302)

    def test_editar_mantem_e_troca_a_empresa(self):
        item = Correspondencia.objects.create(locatario=self.clinica, tipo='carta', registrado_por=self.equipe)
        self.client.force_login(self.equipe)
        form = self.client.get(reverse('correspondencia_editar', args=[item.pk])).context['form']
        self.assertEqual(form.initial['locatario'], self.clinica.pk)
        self.client.post(reverse('correspondencia_editar', args=[item.pk]), self.dados(locatario=self.outra.pk))
        item.refresh_from_db()
        self.assertEqual((item.locatario, item.unidade), (self.outra, self.sala3))

    def test_lista_mostra_a_empresa_e_busca_pelo_nome_dela(self):
        Correspondencia.objects.create(locatario=self.clinica, tipo='carta', registrado_por=self.equipe)
        Correspondencia.objects.create(locatario=self.outra, tipo='carta', registrado_por=self.equipe)
        self.client.force_login(self.equipe)
        pagina = self.client.get(reverse('correspondencia_lista'))
        self.assertContains(pagina, '<th>Empresa</th>')
        self.assertContains(pagina, 'Clínica Vida')
        busca = self.client.get(reverse('correspondencia_lista') + '?q=outra')
        self.assertEqual([c.locatario for c in busca.context['itens']], [self.outra])

    def test_filtro_por_empresa_so_para_quem_cuida_das_correspondencias(self):
        Correspondencia.objects.create(locatario=self.clinica, tipo='carta', registrado_por=self.equipe)
        Correspondencia.objects.create(locatario=self.outra, tipo='carta', registrado_por=self.equipe)
        self.client.force_login(self.equipe)
        pagina = self.client.get(reverse('correspondencia_lista'))
        self.assertContains(pagina, 'name="empresa"')
        filtrada = self.client.get(reverse('correspondencia_lista') + f'?empresa={self.outra.pk}')
        self.assertEqual([c.locatario for c in filtrada.context['itens']], [self.outra])
        self.client.force_login(self.resp)
        self.assertNotContains(self.client.get(reverse('correspondencia_lista')), 'name="empresa"')

    def test_quem_e_da_empresa_ve_a_correspondencia_mesmo_sem_unidade(self):
        item = Correspondencia.objects.create(locatario=self.clinica, tipo='carta', destinatario='Dra. Ana', registrado_por=self.equipe)
        for usuario in (self.resp, self.membro_todas):
            self.client.force_login(usuario)
            self.assertContains(self.client.get(reverse('correspondencia_lista')), 'Clínica Vida', msg_prefix=usuario.username)
            self.assertEqual(self.client.get(reverse('correspondencia_detalhe', args=[item.pk])).status_code, 200)

    def test_quem_e_de_outra_empresa_ou_limitado_a_uma_unidade_nao_ve(self):
        item = Correspondencia.objects.create(locatario=self.clinica, tipo='carta', registrado_por=self.equipe)
        for usuario in (self.resp_outra, self.membro_sala1, self.antigo):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse('correspondencia_detalhe', args=[item.pk])).status_code, 404, usuario.username)

    def test_empresa_de_uma_unidade_aparece_tambem_para_quem_esta_limitado_a_essa_unidade(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('correspondencia_criar'), self.dados(locatario=self.outra.pk))
        item = Correspondencia.objects.get()
        self.client.force_login(self.antigo)  # cadastro antigo, só com a unidade
        self.assertEqual(self.client.get(reverse('correspondencia_detalhe', args=[item.pk])).status_code, 200)

    def test_pagina_inicial_conta_a_correspondencia_da_empresa(self):
        Correspondencia.objects.create(locatario=self.clinica, tipo='carta', registrado_por=self.equipe)
        self.client.force_login(self.resp)
        pagina = self.client.get(reverse('inicio'))
        self.assertContains(pagina, '1 correspondência esperando por você')
        self.assertContains(pagina, '1 aguardando retirada')

    def test_relatorio_agrupa_por_empresa(self):
        Correspondencia.objects.create(locatario=self.clinica, tipo='carta', registrado_por=self.equipe)
        Correspondencia.objects.create(locatario=self.clinica, tipo='encomenda', registrado_por=self.equipe)
        self.client.force_login(self.sindico)
        pagina = self.client.get(reverse('relatorio_correspondencias'))
        self.assertContains(pagina, 'Por empresa')
        self.assertContains(pagina, 'Clínica Vida')
        csv = self.client.get(reverse('relatorio_correspondencias') + '?formato=csv').content.decode('utf-8')
        self.assertIn('Empresa;Tipo;Aos cuidados de', csv)
        self.assertIn('Clínica Vida', csv)

    def test_correspondencias_antigas_ganham_a_empresa_pela_unidade(self):
        antiga = Correspondencia.objects.create(unidade=self.sala1, tipo='carta', registrado_por=self.equipe)
        solta = Correspondencia.objects.create(unidade=self.sala4_livre(), tipo='carta', registrado_por=self.equipe)
        migracao = importlib.import_module('correspondencias.migrations.0003_correspondencia_locatario_and_more')
        migracao.preencher_empresa(apps, None)
        antiga.refresh_from_db()
        solta.refresh_from_db()
        self.assertEqual(antiga.locatario, self.clinica)
        self.assertIsNone(solta.locatario)  # unidade livre: não há empresa para ligar

    def sala4_livre(self):
        return Unidade.objects.get(nome='Sala 04')


class ChamadoPorEmpresaTests(Base):
    def dados(self, **extra):
        dados = dict(titulo='Vazamento', categoria='hidraulica', prioridade=2, local='Banheiro', descricao='Pinga')
        dados.update(extra)
        return dados

    def test_responsavel_nao_ve_campo_de_empresa_nem_de_unidade(self):
        self.client.force_login(self.resp)
        form = self.client.get(reverse('chamado_criar')).context['form']
        self.assertNotIn('unidade', form.fields)
        self.assertNotIn('locatario', form.fields)

    def test_chamado_de_empresa_com_varias_unidades_fica_na_empresa(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('chamado_criar'), self.dados())
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.locatario, chamado.unidade, chamado.solicitante), (self.clinica, None, self.resp))
        self.assertEqual(chamado.onde, 'Clínica Vida · Banheiro')

    def test_nao_da_para_forcar_outra_empresa_ou_unidade(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('chamado_criar'), self.dados(locatario=self.outra.pk, unidade=self.sala3.pk))
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.locatario, chamado.unidade), (self.clinica, None))

    def test_pessoa_limitada_a_uma_unidade_abre_na_unidade_dela(self):
        self.client.force_login(self.membro_sala1)
        self.client.post(reverse('chamado_criar'), self.dados(unidade=self.sala3.pk))
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.locatario, chamado.unidade), (self.clinica, self.sala1))

    def test_empresa_de_uma_unidade_so_descobre_a_unidade(self):
        self.client.force_login(self.resp_outra)
        self.client.post(reverse('chamado_criar'), self.dados())
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.locatario, chamado.unidade), (self.outra, self.sala3))

    def test_empresa_sem_unidade_abre_chamado_so_com_a_empresa(self):
        self.client.force_login(self.resp_vazia)
        self.assertEqual(self.client.get(reverse('chamado_criar')).status_code, 200)
        self.client.post(reverse('chamado_criar'), self.dados())
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.locatario, chamado.unidade), (self.vazia, None))

    def test_cadastro_antigo_so_com_unidade_continua_abrindo(self):
        self.client.force_login(self.antigo)
        self.client.post(reverse('chamado_criar'), self.dados())
        chamado = Chamado.objects.get()
        self.assertEqual((chamado.locatario, chamado.unidade), (self.outra, self.sala3))

    def test_equipe_escolhe_a_empresa_e_nao_a_unidade(self):
        self.client.force_login(self.equipe)
        form = self.client.get(reverse('chamado_criar')).context['form']
        self.assertNotIn('unidade', form.fields)
        self.assertEqual(form.fields['locatario'].label, 'Empresa')
        self.assertEqual(form.fields['locatario'].empty_label, 'Área comum do prédio')
        self.assertEqual({e.razao_social for e in form.fields['locatario'].queryset}, {'Clínica Vida', 'Outra Empresa', 'Sem Unidades'})

    def test_equipe_abre_para_a_empresa_e_a_unidade_vem_so_quando_e_unica(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('chamado_criar'), self.dados(titulo='A', locatario=self.clinica.pk))
        self.client.post(reverse('chamado_criar'), self.dados(titulo='B', locatario=self.outra.pk))
        self.client.post(reverse('chamado_criar'), self.dados(titulo='C', locatario=''))
        a, b, c = (Chamado.objects.get(titulo=t) for t in 'ABC')
        self.assertEqual((a.locatario, a.unidade), (self.clinica, None))
        self.assertEqual((b.locatario, b.unidade), (self.outra, self.sala3))
        self.assertEqual((c.locatario, c.unidade, c.onde), (None, None, 'Área comum · Banheiro'))

    def test_chamado_da_empresa_aparece_para_todas_as_pessoas_dela(self):
        self.client.force_login(self.resp)
        self.client.post(reverse('chamado_criar'), self.dados(titulo='Torneira'))
        for usuario in (self.resp, self.membro_todas):
            self.client.force_login(usuario)
            self.assertContains(self.client.get(reverse('chamado_lista')), 'Torneira', msg_prefix=usuario.username)
        for usuario in (self.resp_outra, self.membro_sala1):
            self.client.force_login(usuario)
            self.assertNotContains(self.client.get(reverse('chamado_lista')), 'Torneira', msg_prefix=usuario.username)

    def test_lista_filtra_por_empresa_so_para_a_equipe(self):
        Chamado.objects.create(locatario=self.clinica, solicitante=self.sindico, titulo='Da clínica', descricao='d')
        Chamado.objects.create(locatario=self.outra, solicitante=self.sindico, titulo='Da outra', descricao='d')
        self.client.force_login(self.equipe)
        self.assertContains(self.client.get(reverse('chamado_lista')), 'name="empresa"')
        filtrada = self.client.get(reverse('chamado_lista') + f'?empresa={self.clinica.pk}')
        self.assertContains(filtrada, 'Da clínica')
        self.assertNotContains(filtrada, 'Da outra')
        self.client.force_login(self.resp)
        pagina = self.client.get(reverse('chamado_lista'))
        self.assertNotContains(pagina, 'name="empresa"')
        self.assertNotContains(pagina, 'Da outra')
        # filtrar por uma empresa que não é a sua não revela nada
        self.assertNotContains(self.client.get(reverse('chamado_lista') + f'?empresa={self.outra.pk}'), 'Da outra')

    def test_chamados_antigos_ganham_a_empresa_pela_unidade(self):
        antigo = Chamado.objects.create(unidade=self.sala1, solicitante=self.sindico, titulo='Antigo', descricao='d')
        migracao = importlib.import_module('chamados.migrations.0002_chamado_locatario_alter_chamado_unidade_and_more')
        migracao.preencher_empresa(apps, None)
        antigo.refresh_from_db()
        self.assertEqual(antigo.locatario, self.clinica)

    def test_relatorio_de_chamados_agrupa_por_empresa(self):
        Chamado.objects.create(locatario=self.clinica, solicitante=self.sindico, titulo='x', descricao='d')
        Chamado.objects.create(solicitante=self.sindico, titulo='y', descricao='d')
        self.client.force_login(self.sindico)
        pagina = self.client.get(reverse('relatorio_chamados'))
        self.assertContains(pagina, 'Por empresa')
        self.assertContains(pagina, 'Clínica Vida')
        self.assertContains(pagina, 'Área comum')


class CobrancaPorEmpresaTests(Base):
    def setUp(self):
        super().setUp()
        for unidade, empresa in ((self.sala1, self.clinica), (self.sala3, self.outra)):
            Cobranca.objects.create(unidade=unidade, locatario=empresa, tipo='aluguel', referencia=date(2026, 9, 1),
                                    valor=Decimal('100'), vencimento=date(2026, 9, 10))

    def test_lista_mostra_empresa_antes_da_unidade_e_filtra_por_empresa(self):
        self.client.force_login(self.administrativo)
        pagina = self.client.get(reverse('cobranca_lista'))
        self.assertContains(pagina, '<th>Empresa</th><th>Unidade</th>')
        self.assertContains(pagina, 'name="empresa"')
        filtrada = self.client.get(reverse('cobranca_lista') + f'?empresa={self.outra.pk}')
        self.assertEqual([c.locatario for c in filtrada.context['cobrancas']], [self.outra])

    def test_responsavel_nao_tem_filtro_e_so_ve_as_da_empresa(self):
        self.client.force_login(self.resp)
        pagina = self.client.get(reverse('cobranca_lista'))
        self.assertNotContains(pagina, 'name="empresa"')
        self.assertEqual([c.locatario for c in pagina.context['cobrancas']], [self.clinica])


class ContratoPorEmpresaTests(Base):
    def test_formulario_comeca_pela_empresa_e_mostra_de_quem_e_cada_unidade(self):
        form = ContratoForm()
        self.assertEqual(list(form.fields)[:2], ['locatario', 'unidade'])
        self.assertEqual(form.fields['locatario'].label, 'Empresa')
        rotulo = form.fields['unidade'].label_from_instance
        self.assertEqual(rotulo(Unidade.objects.get(pk=self.sala1.pk)), 'Sala 01 · Clínica Vida')
        self.assertEqual(rotulo(Unidade.objects.get(nome='Sala 05')), 'Sala 05 · livre')

    def test_so_empresas_ativas_na_lista_do_contrato_novo(self):
        nomes = {e.razao_social for e in ContratoForm().fields['locatario'].queryset}
        self.assertNotIn('Empresa Inativa', nomes)
        self.assertIn('Clínica Vida', nomes)

    def test_empresa_inativa_continua_aparecendo_no_contrato_que_ja_e_dela(self):
        contrato = Contrato.objects.create(unidade=self.sala1, locatario=self.inativa, status='encerrado',
                                           data_inicio=date(2025, 1, 1), valor_aluguel=Decimal('1'))
        nomes = {e.razao_social for e in ContratoForm(instance=contrato).fields['locatario'].queryset}
        self.assertIn('Empresa Inativa', nomes)

    def test_telas_de_contrato_falam_em_empresa(self):
        Contrato.objects.create(unidade=self.sala1, locatario=self.clinica, status='vigente', data_inicio=date(2026, 1, 1),
                                valor_aluguel=Decimal('1000'))
        self.client.force_login(self.administrativo)
        pagina = self.client.get(reverse('contrato_lista'))
        self.assertContains(pagina, '<th>Empresa</th>')
        self.assertNotContains(pagina, 'Locatário')


class NomeEmpresaTests(Base):
    def test_nenhuma_tela_principal_fala_locatario(self):
        self.client.force_login(self.sindico)
        rotas = ['inicio', 'locatario_lista', 'locatario_criar', 'unidade_lista', 'contrato_lista', 'cobranca_lista',
                 'competencia_lista', 'chamado_lista', 'correspondencia_lista', 'relatorios', 'relatorio_contratos',
                 'relatorio_inadimplencia', 'relatorio_rateio', 'usuarios', 'usuario_criar', 'mural']
        for rota in rotas:
            pagina = self.client.get(reverse(rota)).content.decode('utf-8')
            self.assertNotIn('Locatário', pagina, rota)
            self.assertNotIn('locatários', pagina, rota)

    def test_endereco_e_mensagens_da_empresa(self):
        self.assertEqual(reverse('locatario_lista'), '/cadastros/empresas/')
        self.client.force_login(self.sindico)
        resposta = self.client.post(reverse('locatario_criar'), {'razao_social': 'Nova', 'ativo': 'on'}, follow=True)
        self.assertContains(resposta, 'Empresa cadastrada.')
        pagina = self.client.get(reverse('locatario_lista'))
        self.assertContains(pagina, '<h1 class="mb-0">Empresas</h1>')
        self.assertContains(pagina, 'Nova empresa')

    def test_nomes_dos_perfis_e_do_modelo(self):
        self.assertEqual(Usuario.Perfil.LOCATARIO.label, 'Responsável da empresa')
        self.assertEqual(Usuario.Perfil.MEMBRO.label, 'Membro da empresa')
        self.assertEqual(Locatario._meta.verbose_name, 'empresa')
        self.assertEqual(Locatario._meta.verbose_name_plural, 'empresas')

    def test_nota_interna_fala_em_empresa(self):
        chamado = Chamado.objects.create(locatario=self.clinica, solicitante=self.sindico, titulo='x', descricao='d')
        self.client.force_login(self.equipe)
        self.assertContains(self.client.get(reverse('chamado_detalhe', args=[chamado.pk])), 'a empresa não vê')
