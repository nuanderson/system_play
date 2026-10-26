import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from core.models import Usuario

from .models import Contrato, DocumentoContrato

MEDIA_TESTE = tempfile.mkdtemp()


def novo_contrato(unidade, locatario, **extra):
    dados = dict(
        unidade=unidade, locatario=locatario, status=Contrato.Status.VIGENTE,
        data_inicio=timezone.localdate() - timedelta(days=30),
        data_fim=timezone.localdate() + timedelta(days=700),
        valor_aluguel=Decimal('5000.00'),
    )
    dados.update(extra)
    return Contrato.objects.create(**dados)


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class ContratoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TESTE, ignore_errors=True)

    def setUp(self):
        self.unidade = Unidade.objects.get(nome='Sala 01')
        self.outra = Unidade.objects.get(nome='Sala 02')
        self.empresa = Locatario.objects.create(razao_social='Empresa A')
        self.empresa_b = Locatario.objects.create(razao_social='Empresa B')
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.resp_a = Usuario.objects.create_user(
            'resp_a', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.unidade)
        self.resp_b = Usuario.objects.create_user(
            'resp_b', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.outra)
        self.membro = Usuario.objects.create_user('membro', password='x', perfil=Usuario.Perfil.MEMBRO)

    def test_contrato_vigente_atualiza_locatario_da_unidade(self):
        novo_contrato(self.unidade, self.empresa)
        self.unidade.refresh_from_db()
        self.assertEqual(self.unidade.locatario, self.empresa)

    def test_encerrar_contrato_libera_a_unidade(self):
        contrato = novo_contrato(self.unidade, self.empresa)
        contrato.status = Contrato.Status.ENCERRADO
        contrato.save()
        self.unidade.refresh_from_db()
        self.assertIsNone(self.unidade.locatario)

    def test_so_um_contrato_vigente_por_unidade(self):
        novo_contrato(self.unidade, self.empresa)
        with self.assertRaises(IntegrityError), transaction.atomic():
            novo_contrato(self.unidade, self.empresa_b)

    def test_validacao_avisa_contrato_vigente_duplicado(self):
        novo_contrato(self.unidade, self.empresa)
        duplicado = Contrato(
            unidade=self.unidade, locatario=self.empresa_b, status=Contrato.Status.VIGENTE,
            data_inicio=timezone.localdate(), valor_aluguel=Decimal('1'))
        with self.assertRaises(ValidationError):
            duplicado.full_clean()

    def test_fim_antes_do_inicio_e_invalido(self):
        contrato = Contrato(
            unidade=self.unidade, locatario=self.empresa, status=Contrato.Status.NEGOCIACAO,
            data_inicio=timezone.localdate(), data_fim=timezone.localdate() - timedelta(days=1),
            valor_aluguel=Decimal('1'))
        with self.assertRaises(ValidationError):
            contrato.full_clean()

    def test_alerta_de_vencimento(self):
        hoje = timezone.localdate()
        perto = novo_contrato(self.unidade, self.empresa, data_fim=hoje + timedelta(days=30))
        longe = novo_contrato(self.outra, self.empresa_b, data_fim=hoje + timedelta(days=400))
        self.assertEqual(perto.situacao_vencimento, 'a_vencer')
        self.assertEqual(longe.situacao_vencimento, '')
        self.assertEqual(list(Contrato.a_vencer()), [perto])
        perto.data_fim = hoje - timedelta(days=1)
        self.assertEqual(perto.situacao_vencimento, 'vencido')

    def test_responsavel_ve_so_o_contrato_da_propria_unidade(self):
        meu = novo_contrato(self.unidade, self.empresa)
        alheio = novo_contrato(self.outra, self.empresa_b)
        self.client.force_login(self.resp_a)
        resposta = self.client.get(reverse('contrato_lista'))
        self.assertContains(resposta, 'Empresa A')
        self.assertNotContains(resposta, 'Empresa B')
        self.assertEqual(self.client.get(reverse('contrato_detalhe', args=[meu.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('contrato_detalhe', args=[alheio.pk])).status_code, 404)

    def test_membro_nao_acessa_contratos(self):
        self.client.force_login(self.membro)
        self.assertEqual(self.client.get(reverse('contrato_lista')).status_code, 403)

    def test_responsavel_nao_cria_nem_edita(self):
        contrato = novo_contrato(self.unidade, self.empresa)
        self.client.force_login(self.resp_a)
        self.assertEqual(self.client.get(reverse('contrato_criar')).status_code, 403)
        self.assertEqual(self.client.get(reverse('contrato_editar', args=[contrato.pk])).status_code, 403)

    def test_sindico_cria_contrato_pela_tela(self):
        self.client.force_login(self.sindico)
        resposta = self.client.post(reverse('contrato_criar'), {
            'unidade': self.unidade.pk, 'locatario': self.empresa.pk, 'status': 'vigente',
            'data_inicio': '2026-01-01', 'data_fim': '2028-12-31', 'valor_aluguel': '4500.00',
            'dia_vencimento': 10, 'indice_reajuste': 'igpm', 'mes_reajuste': 1,
        })
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Contrato.objects.get().valor_aluguel, Decimal('4500.00'))

    def test_upload_download_e_exclusao_de_documento(self):
        contrato = novo_contrato(self.unidade, self.empresa)
        self.client.force_login(self.sindico)
        arquivo = SimpleUploadedFile('contrato.pdf', b'%PDF-1.4 teste', content_type='application/pdf')
        self.client.post(reverse('documento_enviar', args=[contrato.pk]), {'descricao': 'Contrato assinado', 'arquivo': arquivo})
        documento = DocumentoContrato.objects.get()
        self.assertEqual(documento.nome_original, 'contrato.pdf')
        resposta = self.client.get(reverse('documento_baixar', args=[documento.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(b''.join(resposta.streaming_content), b'%PDF-1.4 teste')
        self.client.post(reverse('documento_excluir', args=[documento.pk]))
        self.assertFalse(DocumentoContrato.objects.exists())

    def test_documento_so_para_quem_ve_o_contrato(self):
        contrato = novo_contrato(self.unidade, self.empresa)
        documento = DocumentoContrato.objects.create(
            contrato=contrato, descricao='x', nome_original='x.pdf',
            arquivo=SimpleUploadedFile('x.pdf', b'abc'))
        self.client.force_login(self.resp_b)
        self.assertEqual(self.client.get(reverse('documento_baixar', args=[documento.pk])).status_code, 403)
        self.client.force_login(self.resp_a)
        self.assertEqual(self.client.get(reverse('documento_baixar', args=[documento.pk])).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('documento_baixar', args=[documento.pk])).status_code, 302)

    def test_upload_rejeita_tipo_e_tamanho_invalidos(self):
        contrato = novo_contrato(self.unidade, self.empresa)
        self.client.force_login(self.sindico)
        perigoso = SimpleUploadedFile('malware.exe', b'MZ')
        self.client.post(reverse('documento_enviar', args=[contrato.pk]), {'descricao': 'x', 'arquivo': perigoso})
        grande = SimpleUploadedFile('grande.pdf', b'0' * (10 * 1024 * 1024 + 1))
        self.client.post(reverse('documento_enviar', args=[contrato.pk]), {'descricao': 'y', 'arquivo': grande})
        self.assertFalse(DocumentoContrato.objects.exists())

    def test_inicio_mostra_indicadores_de_contratos(self):
        novo_contrato(self.unidade, self.empresa, data_fim=timezone.localdate() + timedelta(days=30))
        self.client.force_login(self.sindico)
        resposta = self.client.get(reverse('inicio'))
        self.assertContains(resposta, 'contratos vigentes')
        self.assertContains(resposta, 'contratos vencem em até 90 dias')


class CriarSuperusuarioTests(TestCase):
    def test_superusuario_nasce_como_sindico(self):
        usuario = Usuario.objects.create_superuser('chefe', password='x')
        self.assertEqual(usuario.perfil, Usuario.Perfil.SINDICO)
        self.assertTrue(usuario.e_sindico)
