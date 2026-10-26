import shutil
import tempfile
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from cadastros.models import Unidade
from core.models import Usuario

from .models import Anexo, Chamado, Comentario

MEDIA_TESTE = tempfile.mkdtemp()
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82')


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class ChamadosBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TESTE, ignore_errors=True)

    def setUp(self):
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO, first_name='Síndico')
        self.equipe = Usuario.objects.create_user('equipe', password='x', perfil=Usuario.Perfil.EQUIPE, first_name='Zelador')
        self.resp1 = Usuario.objects.create_user('r1', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala1)
        self.membro1 = Usuario.objects.create_user('m1', password='x', perfil=Usuario.Perfil.MEMBRO, unidade=self.sala1)
        self.resp2 = Usuario.objects.create_user('r2', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala2)
        self.sem_unidade = Usuario.objects.create_user('sem', password='x', perfil=Usuario.Perfil.MEMBRO)

    def novo(self, unidade=None, solicitante=None, **extra):
        dados = dict(unidade=unidade or self.sala1, solicitante=solicitante or self.resp1, titulo='Lâmpada queimada',
                     descricao='A lâmpada do corredor queimou.')
        dados.update(extra)
        return Chamado.objects.create(**dados)

    def dados_form(self, **extra):
        dados = dict(titulo='Ar-condicionado pingando', categoria='ar', prioridade=3, local='Sala de reunião',
                     descricao='Pinga água no piso.')
        dados.update(extra)
        return dados


class AberturaTests(ChamadosBase):
    def test_locatario_abre_chamado_na_propria_unidade(self):
        self.client.force_login(self.resp1)
        resposta = self.client.post(reverse('chamado_criar'), self.dados_form())
        chamado = Chamado.objects.get()
        self.assertRedirects(resposta, reverse('chamado_detalhe', args=[chamado.pk]))
        self.assertEqual((chamado.unidade, chamado.solicitante, chamado.status), (self.sala1, self.resp1, 'aberto'))
        self.assertTrue(chamado.comentarios.filter(tipo='sistema', texto='Chamado aberto.').exists())

    def test_locatario_nao_escolhe_outra_unidade(self):
        self.client.force_login(self.resp1)
        self.client.post(reverse('chamado_criar'), self.dados_form(unidade=self.sala2.pk))
        self.assertEqual(Chamado.objects.get().unidade, self.sala1)

    def test_equipe_abre_chamado_de_area_comum(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('chamado_criar'), self.dados_form(unidade='', local='Portão da garagem'))
        chamado = Chamado.objects.get()
        self.assertIsNone(chamado.unidade)
        self.assertEqual(chamado.onde, 'Área comum · Portão da garagem')

    def test_usuario_sem_unidade_nao_abre(self):
        self.client.force_login(self.sem_unidade)
        self.assertEqual(self.client.get(reverse('chamado_criar')).status_code, 403)

    def test_abre_com_fotos_e_rejeita_tipo_invalido(self):
        self.client.force_login(self.resp1)
        foto = SimpleUploadedFile('foto.png', PNG, content_type='image/png')
        self.client.post(reverse('chamado_criar'), self.dados_form(fotos=[foto]))
        self.assertEqual(Anexo.objects.count(), 1)
        ruim = SimpleUploadedFile('virus.exe', b'MZ')
        self.client.post(reverse('chamado_criar'), self.dados_form(titulo='Outro', fotos=[ruim]))
        self.assertEqual(Chamado.objects.count(), 1)


class VisibilidadeTests(ChamadosBase):
    def setUp(self):
        super().setUp()
        self.meu = self.novo(self.sala1, self.resp1, titulo='Chamado da sala 1')
        self.alheio = self.novo(self.sala2, self.resp2, titulo='Chamado da sala 2')

    def test_locatario_ve_so_os_da_propria_unidade(self):
        self.client.force_login(self.resp1)
        resposta = self.client.get(reverse('chamado_lista'))
        self.assertContains(resposta, 'Chamado da sala 1')
        self.assertNotContains(resposta, 'Chamado da sala 2')
        self.assertEqual(self.client.get(reverse('chamado_detalhe', args=[self.alheio.pk])).status_code, 404)

    def test_membro_da_mesma_unidade_tambem_ve(self):
        self.client.force_login(self.membro1)
        self.assertEqual(self.client.get(reverse('chamado_detalhe', args=[self.meu.pk])).status_code, 200)

    def test_equipe_e_sindico_veem_todos(self):
        for usuario in (self.equipe, self.sindico):
            self.client.force_login(usuario)
            resposta = self.client.get(reverse('chamado_lista'))
            self.assertContains(resposta, 'Chamado da sala 1')
            self.assertContains(resposta, 'Chamado da sala 2')

    def test_lista_padrao_mostra_so_abertos(self):
        self.meu.status = 'resolvido'
        self.meu.save()
        self.client.force_login(self.sindico)
        self.assertNotContains(self.client.get(reverse('chamado_lista')), 'Chamado da sala 1')
        self.assertContains(self.client.get(reverse('chamado_lista') + '?situacao=todos'), 'Chamado da sala 1')

    def test_notas_internas_e_custo_ficam_longe_do_locatario(self):
        Comentario.objects.create(chamado=self.meu, autor=self.equipe, texto='Segredo da equipe', interno=True)
        Comentario.objects.create(chamado=self.meu, autor=self.equipe, texto='Vamos resolver amanhã')
        self.meu.fornecedor = 'Eletricista Zé'
        self.meu.custo = Decimal('150.00')
        self.meu.save()
        url = reverse('chamado_detalhe', args=[self.meu.pk])
        self.client.force_login(self.resp1)
        resposta = self.client.get(url)
        self.assertContains(resposta, 'Vamos resolver amanhã')
        self.assertNotContains(resposta, 'Segredo da equipe')
        self.assertNotContains(resposta, 'Eletricista Zé')
        self.assertNotContains(resposta, 'Gerenciar')
        self.client.force_login(self.equipe)
        resposta = self.client.get(url)
        self.assertContains(resposta, 'Segredo da equipe')
        self.assertContains(resposta, 'Gerenciar')


class GestaoTests(ChamadosBase):
    def setUp(self):
        super().setUp()
        self.chamado = self.novo()

    def gerenciar(self, usuario, **dados):
        base = dict(status=self.chamado.status, prioridade=self.chamado.prioridade, responsavel='', fornecedor='', custo='')
        base.update(dados)
        self.client.force_login(usuario)
        return self.client.post(reverse('chamado_gerenciar', args=[self.chamado.pk]), base)

    def test_equipe_resolve_e_o_sistema_registra(self):
        self.gerenciar(self.equipe, status='andamento', responsavel=self.equipe.pk)
        self.chamado.refresh_from_db()
        self.assertEqual((self.chamado.status, self.chamado.responsavel), ('andamento', self.equipe))
        self.gerenciar(self.equipe, status='resolvido', responsavel=self.equipe.pk)
        self.chamado.refresh_from_db()
        self.assertIsNotNone(self.chamado.resolvido_em)
        textos = list(self.chamado.comentarios.filter(tipo='sistema').values_list('texto', flat=True))
        self.assertIn('Situação: Aberto → Em andamento.', textos)
        self.assertIn('Situação: Em andamento → Resolvido.', textos)

    def test_reabrir_limpa_a_data_de_resolucao(self):
        self.gerenciar(self.equipe, status='resolvido')
        self.gerenciar(self.equipe, status='andamento')
        self.chamado.refresh_from_db()
        self.assertIsNone(self.chamado.resolvido_em)

    def test_fornecedor_e_custo_viram_registro_interno(self):
        self.gerenciar(self.equipe, fornecedor='Eletricista Zé', custo='150.00')
        registro = self.chamado.comentarios.get(texto__contains='Eletricista Zé')
        self.assertTrue(registro.interno)
        self.chamado.refresh_from_db()
        self.assertEqual(self.chamado.custo, Decimal('150.00'))

    def test_locatario_nao_gerencia(self):
        resposta = self.gerenciar(self.resp1, status='resolvido')
        self.assertEqual(resposta.status_code, 403)
        self.chamado.refresh_from_db()
        self.assertEqual(self.chamado.status, 'aberto')

    def test_responsavel_so_pode_ser_equipe_ou_sindico(self):
        self.gerenciar(self.sindico, responsavel=self.resp1.pk)
        self.chamado.refresh_from_db()
        self.assertIsNone(self.chamado.responsavel)


class InteracaoTests(ChamadosBase):
    def setUp(self):
        super().setUp()
        self.chamado = self.novo()

    def test_locatario_comenta_mas_nao_cria_nota_interna(self):
        self.client.force_login(self.resp1)
        self.client.post(reverse('chamado_comentar', args=[self.chamado.pk]), {'texto': 'Ainda sem luz', 'interno': 'on'})
        comentario = self.chamado.comentarios.get(texto='Ainda sem luz')
        self.assertFalse(comentario.interno)

    def test_equipe_cria_nota_interna(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('chamado_comentar', args=[self.chamado.pk]), {'texto': 'Pedir peça', 'interno': 'on'})
        self.assertTrue(self.chamado.comentarios.get(texto='Pedir peça').interno)

    def test_outro_locatario_nao_comenta(self):
        self.client.force_login(self.resp2)
        resposta = self.client.post(reverse('chamado_comentar', args=[self.chamado.pk]), {'texto': 'oi'})
        self.assertEqual(resposta.status_code, 404)

    def test_solicitante_cancela_enquanto_aberto(self):
        self.client.force_login(self.resp1)
        self.client.post(reverse('chamado_cancelar', args=[self.chamado.pk]))
        self.chamado.refresh_from_db()
        self.assertEqual(self.chamado.status, 'cancelado')

    def test_nao_cancela_depois_de_iniciado_nem_chamado_de_outro(self):
        self.chamado.status = 'andamento'
        self.chamado.save()
        self.client.force_login(self.resp1)
        self.assertEqual(self.client.post(reverse('chamado_cancelar', args=[self.chamado.pk])).status_code, 403)
        self.chamado.status = 'aberto'
        self.chamado.save()
        self.client.force_login(self.membro1)  # mesma unidade, mas não foi quem abriu
        self.assertEqual(self.client.post(reverse('chamado_cancelar', args=[self.chamado.pk])).status_code, 403)

    def test_cancelado_nao_aceita_comentario(self):
        self.chamado.status = 'cancelado'
        self.chamado.save()
        self.client.force_login(self.resp1)
        self.client.post(reverse('chamado_comentar', args=[self.chamado.pk]), {'texto': 'oi'})
        self.assertFalse(self.chamado.comentarios.filter(texto='oi').exists())


class AnexosTests(ChamadosBase):
    def setUp(self):
        super().setUp()
        self.chamado = self.novo()

    def anexar(self, usuario, nome='foto.png', conteudo=PNG):
        self.client.force_login(usuario)
        return self.client.post(reverse('chamado_anexar', args=[self.chamado.pk]),
                                {'arquivo': SimpleUploadedFile(nome, conteudo)})

    def test_anexar_e_baixar(self):
        self.anexar(self.resp1)
        anexo = Anexo.objects.get()
        self.assertTrue(anexo.eh_imagem)
        resposta = self.client.get(reverse('anexo_baixar', args=[anexo.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn('attachment', resposta.get('Content-Disposition', ''))
        self.assertEqual(b''.join(resposta.streaming_content), PNG)

    def test_pdf_e_baixado_como_arquivo(self):
        self.anexar(self.resp1, 'laudo.pdf', b'%PDF-1.4')
        resposta = self.client.get(reverse('anexo_baixar', args=[Anexo.objects.get().pk]))
        self.assertIn('attachment', resposta['Content-Disposition'])

    def test_tipo_invalido_e_arquivo_grande_sao_recusados(self):
        self.anexar(self.resp1, 'malware.exe', b'MZ')
        self.anexar(self.resp1, 'grande.png', b'0' * (10 * 1024 * 1024 + 1))
        self.assertFalse(Anexo.objects.exists())

    def test_so_quem_ve_o_chamado_baixa_o_arquivo(self):
        self.anexar(self.resp1)
        anexo = Anexo.objects.get()
        url = reverse('anexo_baixar', args=[anexo.pk])
        self.client.force_login(self.resp2)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.equipe)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_so_a_equipe_remove_anexo_e_o_arquivo_some(self):
        self.anexar(self.resp1)
        anexo = Anexo.objects.get()
        caminho = anexo.arquivo.path
        self.assertEqual(self.client.post(reverse('anexo_excluir', args=[anexo.pk])).status_code, 403)
        self.client.force_login(self.equipe)
        self.client.post(reverse('anexo_excluir', args=[anexo.pk]))
        self.assertFalse(Anexo.objects.exists())
        import os
        self.assertFalse(os.path.exists(caminho))


class InicioTests(ChamadosBase):
    def test_indicadores_de_chamados_na_home(self):
        self.novo(prioridade=4)
        self.novo(prioridade=2, titulo='Outro')
        for usuario in (self.sindico, self.equipe):
            self.client.force_login(usuario)
            resposta = self.client.get(reverse('inicio'))
            self.assertContains(resposta, 'chamados em aberto')
            self.assertContains(resposta, 'chamados urgentes')

    def test_locatario_ve_o_cartao_de_chamados(self):
        self.client.force_login(self.resp1)
        self.assertContains(self.client.get(reverse('inicio')), 'Chamados de manutenção')
