from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from cadastros.models import Unidade
from core.models import Usuario

from .models import Contato


class TelefoneTests(SimpleTestCase):
    def contato(self, telefone, **extra):
        return Contato(categoria='eletrica', nome='João', telefone=telefone, **extra)

    def test_formata_celular_e_fixo(self):
        self.assertEqual(self.contato('63999990000').telefone_exibido, '(63) 99999-0000')
        self.assertEqual(self.contato('(63) 3411-2233').telefone_exibido, '(63) 3411-2233')
        self.assertEqual(self.contato('190').telefone_exibido, '190')

    def test_links(self):
        c = self.contato('(63) 99999-0000', whatsapp=True)
        self.assertEqual(c.link_telefone, 'tel:+5563999990000')
        self.assertEqual(c.link_whatsapp, 'https://wa.me/5563999990000')
        self.assertEqual(self.contato('(63) 99999-0000').link_whatsapp, '')  # sem WhatsApp marcado
        self.assertEqual(self.contato('190', whatsapp=True).link_whatsapp, '')  # número curto não é WhatsApp
        self.assertEqual(self.contato('190').link_telefone, 'tel:190')

    def test_cor_e_estavel_por_categoria(self):
        cores = {Contato(categoria=valor).cor for valor in Contato.Categoria.values}
        self.assertTrue(cores <= {0, 1, 2, 3, 4})
        self.assertEqual(Contato(categoria='eletrica').cor, Contato(categoria='eletrica').cor)


class MuralBase(TestCase):
    def setUp(self):
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.equipe = Usuario.objects.create_user('equipe', password='x', perfil=Usuario.Perfil.EQUIPE)
        self.resp = Usuario.objects.create_user(
            'resp', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=Unidade.objects.get(nome='Sala 01'))
        self.sem_unidade = Usuario.objects.create_user('sem', password='x', perfil=Usuario.Perfil.MEMBRO)
        self.eletricista = Contato.objects.create(categoria='eletrica', nome='Zé Eletricista', telefone='(63) 99999-0000',
                                                  whatsapp=True, observacoes='Atende 24 horas')
        self.encanador = Contato.objects.create(categoria='hidraulica', nome='Ana Encanadora', telefone='6334112233')

    def dados(self, **extra):
        dados = dict(categoria='ar', nome='Frio Bom Ar', telefone='(63) 98888-7777', observacoes='')
        dados.update(extra)
        return dados


class VisualizacaoTests(MuralBase):
    def test_todos_os_usuarios_logados_veem_o_mural(self):
        for usuario in (self.sindico, self.equipe, self.resp, self.sem_unidade):
            self.client.force_login(usuario)
            resposta = self.client.get(reverse('mural'))
            self.assertContains(resposta, 'Zé Eletricista')
            self.assertContains(resposta, '(63) 99999-0000')
            self.assertContains(resposta, 'Atende 24 horas')

    def test_anonimo_vai_para_o_login(self):
        resposta = self.client.get(reverse('mural'))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn('/entrar/', resposta['Location'])

    def test_locatario_nao_ve_botoes_de_edicao(self):
        self.client.force_login(self.resp)
        resposta = self.client.get(reverse('mural'))
        self.assertNotContains(resposta, 'Novo contato')
        self.assertNotContains(resposta, reverse('contato_editar', args=[self.eletricista.pk]))
        self.client.force_login(self.equipe)
        resposta = self.client.get(reverse('mural'))
        self.assertContains(resposta, 'Novo contato')
        self.assertContains(resposta, reverse('contato_editar', args=[self.eletricista.pk]))

    def test_link_de_telefone_e_whatsapp_na_tela(self):
        self.client.force_login(self.resp)
        resposta = self.client.get(reverse('mural'))
        self.assertContains(resposta, 'tel:+5563999990000')
        self.assertContains(resposta, 'https://wa.me/5563999990000')

    def test_filtro_por_categoria_e_busca(self):
        self.client.force_login(self.resp)
        resposta = self.client.get(reverse('mural') + '?categoria=hidraulica')
        self.assertContains(resposta, 'Ana Encanadora')
        self.assertNotContains(resposta, 'Zé Eletricista')
        resposta = self.client.get(reverse('mural') + '?q=24 horas')
        self.assertContains(resposta, 'Zé Eletricista')
        self.assertNotContains(resposta, 'Ana Encanadora')
        resposta = self.client.get(reverse('mural') + '?q=3411')
        self.assertContains(resposta, 'Ana Encanadora')
        self.assertContains(self.client.get(reverse('mural') + '?categoria=inexistente'), 'Ana Encanadora')

    def test_ordem_segue_as_categorias_e_depois_o_nome(self):
        Contato.objects.create(categoria='eletrica', nome='Abel Elétrica', telefone='63911112222')
        self.client.force_login(self.resp)
        nomes = [c.nome for c in self.client.get(reverse('mural')).context['contatos']]
        self.assertEqual(nomes, ['Abel Elétrica', 'Zé Eletricista', 'Ana Encanadora'])

    def test_mural_vazio_mostra_mensagem(self):
        Contato.objects.all().delete()
        self.client.force_login(self.equipe)
        self.assertContains(self.client.get(reverse('mural')), 'O mural ainda está vazio')

    def test_nome_com_html_e_escapado(self):
        Contato.objects.create(categoria='outros', nome='<script>alert(1)</script>', telefone='63911112222')
        self.client.force_login(self.equipe)
        resposta = self.client.get(reverse('mural'))
        self.assertNotContains(resposta, '<script>alert(1)</script>')
        self.assertContains(resposta, '&lt;script&gt;')


class EdicaoTests(MuralBase):
    def test_equipe_e_sindico_cadastram(self):
        for usuario in (self.equipe, self.sindico):
            self.client.force_login(usuario)
            resposta = self.client.post(reverse('contato_criar'), self.dados(nome=f'Contato de {usuario.username}'))
            self.assertRedirects(resposta, reverse('mural'))
        self.assertEqual(Contato.objects.filter(categoria='ar').count(), 2)

    def test_salvar_e_adicionar_outro_mantem_a_categoria(self):
        self.client.force_login(self.equipe)
        resposta = self.client.post(reverse('contato_criar'), self.dados(outra='1'))
        self.assertRedirects(resposta, reverse('contato_criar') + '?categoria=ar')
        self.assertContains(self.client.get(reverse('contato_criar') + '?categoria=ar'), 'value="ar" selected')

    def test_locatario_nao_cadastra_edita_nem_remove(self):
        self.client.force_login(self.resp)
        self.assertEqual(self.client.get(reverse('contato_criar')).status_code, 403)
        self.assertEqual(self.client.post(reverse('contato_criar'), self.dados()).status_code, 403)
        self.assertEqual(self.client.post(reverse('contato_editar', args=[self.eletricista.pk]), self.dados()).status_code, 403)
        self.assertEqual(self.client.post(reverse('contato_excluir', args=[self.eletricista.pk])).status_code, 403)
        self.assertEqual(Contato.objects.count(), 2)

    def test_editar_e_remover(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('contato_editar', args=[self.eletricista.pk]), self.dados(categoria='eletrica', nome='Zé Renomeado'))
        self.eletricista.refresh_from_db()
        self.assertEqual(self.eletricista.nome, 'Zé Renomeado')
        self.client.post(reverse('contato_excluir', args=[self.eletricista.pk]))
        self.assertFalse(Contato.objects.filter(pk=self.eletricista.pk).exists())

    def test_remover_exige_post(self):
        self.client.force_login(self.equipe)
        self.assertEqual(self.client.get(reverse('contato_excluir', args=[self.eletricista.pk])).status_code, 405)
        self.assertTrue(Contato.objects.filter(pk=self.eletricista.pk).exists())

    def test_telefone_invalido_e_recusado(self):
        self.client.force_login(self.equipe)
        for telefone in ('abc', '12', '1234567890123456', '(63) 9999-00; DROP'):
            self.client.post(reverse('contato_criar'), self.dados(telefone=telefone))
        self.assertEqual(Contato.objects.count(), 2)

    def test_nome_em_branco_e_recusado(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('contato_criar'), self.dados(nome='   '))
        self.assertEqual(Contato.objects.count(), 2)


class InicioTests(MuralBase):
    def test_cartao_do_mural_na_home_de_todos(self):
        for usuario in (self.sindico, self.equipe, self.resp):
            self.client.force_login(usuario)
            resposta = self.client.get(reverse('inicio'))
            self.assertContains(resposta, 'Mural de contatos')
            self.assertContains(resposta, '2 contatos')
