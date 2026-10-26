import os
import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Unidade
from core.models import Usuario

from .models import DIAS_ALERTA, Correspondencia

MEDIA_TESTE = tempfile.mkdtemp()
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82')


def agora_formatado(deslocamento=timedelta(0)):
    return (timezone.localtime() + deslocamento).strftime('%Y-%m-%dT%H:%M')


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TESTE, ignore_errors=True)

    def setUp(self):
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.equipe = Usuario.objects.create_user('equipe', password='x', perfil=Usuario.Perfil.EQUIPE)
        self.resp1 = Usuario.objects.create_user('r1', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala1)
        self.membro1 = Usuario.objects.create_user('m1', password='x', perfil=Usuario.Perfil.MEMBRO, unidade=self.sala1)
        self.resp2 = Usuario.objects.create_user('r2', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala2)
        self.sem_unidade = Usuario.objects.create_user('sem', password='x', perfil=Usuario.Perfil.MEMBRO)

    def nova(self, unidade=None, **extra):
        dados = dict(unidade=unidade or self.sala1, tipo='encomenda', destinatario='Maria', registrado_por=self.equipe)
        dados.update(extra)
        return Correspondencia.objects.create(**dados)

    def dados_form(self, **extra):
        dados = dict(unidade=self.sala1.pk, tipo='encomenda', destinatario='Maria',
                     recebido_em=agora_formatado(-timedelta(minutes=5)), observacoes='')
        dados.update(extra)
        return dados


class RegistroTests(Base):
    def test_equipe_registra(self):
        self.client.force_login(self.equipe)
        resposta = self.client.post(reverse('correspondencia_criar'), self.dados_form())
        item = Correspondencia.objects.get()
        self.assertRedirects(resposta, reverse('correspondencia_detalhe', args=[item.pk]))
        self.assertEqual((item.unidade, item.status, item.registrado_por), (self.sala1, 'aguardando', self.equipe))
        self.assertEqual(item.numero, f'C{item.pk:04d}')

    def test_salvar_e_registrar_outra_volta_ao_formulario(self):
        self.client.force_login(self.equipe)
        resposta = self.client.post(reverse('correspondencia_criar'), self.dados_form(outra='1'))
        self.assertRedirects(resposta, reverse('correspondencia_criar'))

    def test_registra_com_foto_e_recusa_tipo_invalido(self):
        self.client.force_login(self.equipe)
        foto = SimpleUploadedFile('etiqueta.png', PNG, content_type='image/png')
        self.client.post(reverse('correspondencia_criar'), self.dados_form(foto=foto))
        self.assertTrue(Correspondencia.objects.get().tem_foto)
        ruim = SimpleUploadedFile('virus.exe', b'MZ')
        self.client.post(reverse('correspondencia_criar'), self.dados_form(foto=ruim))
        self.assertEqual(Correspondencia.objects.count(), 1)

    def test_data_futura_e_recusada(self):
        self.client.force_login(self.equipe)
        self.client.post(reverse('correspondencia_criar'), self.dados_form(recebido_em=agora_formatado(timedelta(days=2))))
        self.assertFalse(Correspondencia.objects.exists())

    def test_locatario_nao_registra(self):
        self.client.force_login(self.resp1)
        self.assertEqual(self.client.get(reverse('correspondencia_criar')).status_code, 403)
        self.assertEqual(self.client.post(reverse('correspondencia_criar'), self.dados_form()).status_code, 403)
        self.assertFalse(Correspondencia.objects.exists())


class VisibilidadeTests(Base):
    def setUp(self):
        super().setUp()
        self.minha = self.nova(self.sala1, destinatario='Para a sala 1')
        self.alheia = self.nova(self.sala2, destinatario='Para a sala 2')

    def test_locatario_ve_so_as_da_propria_unidade(self):
        self.client.force_login(self.resp1)
        resposta = self.client.get(reverse('correspondencia_lista'))
        self.assertContains(resposta, 'Para a sala 1')
        self.assertNotContains(resposta, 'Para a sala 2')
        self.assertEqual(self.client.get(reverse('correspondencia_detalhe', args=[self.alheia.pk])).status_code, 404)

    def test_membro_da_unidade_tambem_ve_e_sem_unidade_nao_ve_nada(self):
        self.client.force_login(self.membro1)
        self.assertEqual(self.client.get(reverse('correspondencia_detalhe', args=[self.minha.pk])).status_code, 200)
        self.client.force_login(self.sem_unidade)
        self.assertNotContains(self.client.get(reverse('correspondencia_lista')), 'Para a sala')

    def test_equipe_e_sindico_veem_todas(self):
        for usuario in (self.equipe, self.sindico):
            self.client.force_login(usuario)
            resposta = self.client.get(reverse('correspondencia_lista'))
            self.assertContains(resposta, 'Para a sala 1')
            self.assertContains(resposta, 'Para a sala 2')

    def test_lista_padrao_mostra_so_aguardando_e_busca_funciona(self):
        self.minha.status = 'retirada'
        self.minha.save()
        self.client.force_login(self.equipe)
        self.assertNotContains(self.client.get(reverse('correspondencia_lista')), 'Para a sala 1')
        self.assertContains(self.client.get(reverse('correspondencia_lista') + '?situacao=todas'), 'Para a sala 1')
        resposta = self.client.get(reverse('correspondencia_lista') + '?situacao=todas&q=sala 2')
        self.assertContains(resposta, 'Para a sala 2')
        self.assertNotContains(resposta, 'Para a sala 1')

    def test_locatario_nao_ve_formulario_de_baixa(self):
        self.client.force_login(self.resp1)
        resposta = self.client.get(reverse('correspondencia_detalhe', args=[self.minha.pk]))
        self.assertNotContains(resposta, 'Dar baixa')
        self.client.force_login(self.equipe)
        self.assertContains(self.client.get(reverse('correspondencia_detalhe', args=[self.minha.pk])), 'Dar baixa')


class BaixaTests(Base):
    def setUp(self):
        super().setUp()
        self.item = self.nova(recebido_em=timezone.now() - timedelta(days=2))

    def baixar(self, usuario, **extra):
        dados = dict(retirada_por='João da Silva', retirada_em=agora_formatado(-timedelta(minutes=1)), baixa_obs='')
        dados.update(extra)
        self.client.force_login(usuario)
        return self.client.post(reverse('correspondencia_baixa', args=[self.item.pk]), dados)

    def test_equipe_da_baixa(self):
        self.baixar(self.equipe)
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.retirada_por, self.item.baixa_por), ('retirada', 'João da Silva', self.equipe))
        self.assertIsNotNone(self.item.retirada_em)

    def test_baixa_exige_quem_retirou(self):
        self.baixar(self.equipe, retirada_por='')
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'aguardando')

    def test_retirada_nao_pode_ser_antes_do_recebimento_nem_no_futuro(self):
        self.baixar(self.equipe, retirada_em=agora_formatado(-timedelta(days=5)))
        self.baixar(self.equipe, retirada_em=agora_formatado(timedelta(days=1)))
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'aguardando')

    def test_locatario_nao_da_baixa(self):
        self.assertEqual(self.baixar(self.resp1).status_code, 403)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'aguardando')

    def test_nao_da_baixa_duas_vezes(self):
        self.baixar(self.equipe)
        self.assertEqual(self.baixar(self.equipe, retirada_por='Outro').status_code, 404)
        self.item.refresh_from_db()
        self.assertEqual(self.item.retirada_por, 'João da Silva')

    def test_devolucao_exige_motivo(self):
        self.client.force_login(self.equipe)
        url = reverse('correspondencia_devolver', args=[self.item.pk])
        self.client.post(url, {'baixa_obs': ''})
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'aguardando')
        self.client.post(url, {'baixa_obs': 'Destinatário mudou de endereço'})
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.baixa_obs), ('devolvida', 'Destinatário mudou de endereço'))

    def test_so_edita_enquanto_aguarda(self):
        self.client.force_login(self.equipe)
        url = reverse('correspondencia_editar', args=[self.item.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.baixar(self.equipe)
        self.assertEqual(self.client.get(url).status_code, 404)


class ParadaEFotoTests(Base):
    def test_alerta_de_correspondencia_parada(self):
        recente = self.nova()
        antiga = self.nova(recebido_em=timezone.now() - timedelta(days=DIAS_ALERTA + 1))
        self.assertFalse(recente.parada)
        self.assertTrue(antiga.parada)
        self.assertEqual(list(Correspondencia.paradas()), [antiga])
        self.assertEqual(Correspondencia.pendentes().count(), 2)
        antiga.status = 'retirada'
        antiga.retirada_em = timezone.now()
        antiga.save()
        self.assertFalse(antiga.parada)

    def test_foto_so_para_quem_ve_a_correspondencia(self):
        item = self.nova(foto=SimpleUploadedFile('f.png', PNG))
        url = reverse('correspondencia_foto', args=[item.pk])
        self.client.force_login(self.resp2)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.resp1)
        resposta = self.client.get(url)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(b''.join(resposta.streaming_content), PNG)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_apagar_remove_o_arquivo(self):
        item = self.nova(foto=SimpleUploadedFile('f.png', PNG))
        caminho = item.foto.path
        self.assertTrue(os.path.exists(caminho))
        item.delete()
        self.assertFalse(os.path.exists(caminho))


class InicioTests(Base):
    def test_indicadores_da_equipe_e_do_sindico(self):
        self.nova()
        self.nova(recebido_em=timezone.now() - timedelta(days=10))
        for usuario in (self.sindico, self.equipe):
            self.client.force_login(usuario)
            resposta = self.client.get(reverse('inicio'))
            self.assertContains(resposta, '2 aguardando retirada')
            self.assertContains(resposta, f'1 correspondência parada há {DIAS_ALERTA} dias ou mais')

    def test_locatario_ve_o_que_espera_por_ele(self):
        self.nova(self.sala1)
        self.nova(self.sala2)
        self.client.force_login(self.resp1)
        resposta = self.client.get(reverse('inicio'))
        self.assertContains(resposta, '1 correspondência esperando por você')
        self.assertContains(resposta, '1 aguardando retirada')  # só a da própria unidade
