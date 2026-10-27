"""Cobrança unificada por empresa, acréscimos e descontos do contrato, e boletos."""

import shutil
import tempfile
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from cadastros.models import Locatario, Unidade
from contratos.models import AjusteContrato, Contrato
from core.models import Usuario

from . import servicos
from .calculo import calcular_rateio
from .models import Cobranca, CobrancaItem, Competencia, Despesa

D = Decimal
MES = date(2026, 9, 1)


def ajuste(contrato, **extra):
    dados = dict(descricao='Desconto', natureza='desconto', forma='valor', valor=D('100'), aplica_em='aluguel')
    dados.update(extra)
    return AjusteContrato.objects.create(contrato=contrato, **dados)


class Base(TestCase):
    def setUp(self):
        self.empresa = Locatario.objects.create(razao_social='Grupo Vida')
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.sala3 = Unidade.objects.get(nome='Sala 03')
        self.c1 = self.contrato(self.sala1, self.empresa, '1000', dia=10)
        self.c2 = self.contrato(self.sala2, self.empresa, '1500', dia=5)

    def contrato(self, unidade, empresa, aluguel, dia=10, **extra):
        return Contrato.objects.create(
            unidade=unidade, locatario=empresa, status='vigente', data_inicio=date(2026, 1, 1),
            valor_aluguel=D(aluguel), dia_vencimento=dia, **extra)


class UnificacaoTests(Base):
    def test_empresa_com_duas_unidades_recebe_uma_cobranca_de_aluguel(self):
        resumo = servicos.gerar_alugueis(MES)
        self.assertEqual(resumo['criadas'], 1)
        c = Cobranca.objects.get()
        self.assertIsNone(c.unidade)
        self.assertEqual(c.valor, D('2500'))
        self.assertEqual(c.vencimento, date(2026, 9, 5))  # o vencimento mais cedo
        self.assertEqual(c.itens.count(), 2)
        self.assertEqual(sorted(i.unidade.nome for i in c.itens.all()), ['Sala 01', 'Sala 02'])
        self.assertEqual(c.origem, 'Sala 01, Sala 02')

    def test_gerar_de_novo_nao_duplica(self):
        servicos.gerar_alugueis(MES)
        resumo = servicos.gerar_alugueis(MES)
        self.assertEqual((resumo['criadas'], resumo['ja_existiam']), (0, 2))
        self.assertEqual(Cobranca.objects.count(), 1)

    def test_desligar_a_opcao_cobra_cada_unidade_separada(self):
        self.empresa.cobranca_unificada = False
        self.empresa.save()
        servicos.gerar_alugueis(MES)
        self.assertEqual(Cobranca.objects.count(), 2)
        self.assertEqual({c.unidade_id for c in Cobranca.objects.all()}, {self.sala1.pk, self.sala2.pk})

    def test_empresa_com_uma_unidade_continua_com_cobranca_por_unidade(self):
        outra = Locatario.objects.create(razao_social='Sozinha')
        self.contrato(self.sala3, outra, '800')
        servicos.gerar_alugueis(MES)
        sozinha = Cobranca.objects.get(locatario=outra)
        self.assertEqual(sozinha.unidade, self.sala3)
        self.assertEqual(sozinha.itens.count(), 1)

    def test_aluguel_e_condominio_ficam_em_cobrancas_separadas(self):
        servicos.gerar_alugueis(MES)
        Competencia.objects.all().delete()
        comp = Competencia.objects.create(referencia=MES)
        Despesa.objects.create(competencia=comp, descricao='Limpeza', valor=D('900'), tipo='demais',
                               vencimento=date(2026, 9, 6), chave='l')
        calcular_rateio(comp)
        servicos.aprovar_rateio(comp, Usuario.objects.create_user('s', password='x', perfil='sindico'))
        self.assertEqual(Cobranca.objects.filter(tipo='aluguel').count(), 1)
        condominio = Cobranca.objects.get(tipo='condominio')
        self.assertIsNone(condominio.unidade)
        self.assertEqual(condominio.itens.count(), 2)
        self.assertEqual(condominio.valor, sum(i.valor for i in condominio.itens.all()))

    def test_unidade_ja_cobrada_sozinha_nao_entra_de_novo_numa_unificada(self):
        Cobranca.objects.create(unidade=self.sala1, locatario=self.empresa, tipo='aluguel', referencia=MES,
                                valor=D('1000'), vencimento=date(2026, 9, 10))
        resumo = servicos.gerar_alugueis(MES)
        self.assertEqual(resumo['ja_existiam'], 1)
        self.assertEqual(Cobranca.objects.filter(tipo='aluguel').count(), 2)  # a antiga e a da sala 02 sozinha
        self.assertEqual(Cobranca.objects.filter(unidade=self.sala2).get().valor, D('1500'))

    def test_cobranca_cancelada_pode_ser_gerada_de_novo(self):
        servicos.gerar_alugueis(MES)
        Cobranca.objects.update(status='cancelada')
        servicos.gerar_alugueis(MES)
        self.assertEqual(Cobranca.objects.filter(status='aberta').count(), 1)


class AjustesTests(Base):
    def test_desconto_fixo_e_acrescimo_percentual_entram_no_aluguel(self):
        ajuste(self.c1, descricao='Desconto de fidelidade', valor=D('100'))
        ajuste(self.c1, descricao='Taxa de uso', natureza='acrescimo', forma='percentual', valor=D('5'))
        self.empresa.cobranca_unificada = False
        self.empresa.save()
        servicos.gerar_alugueis(MES)
        c = Cobranca.objects.get(unidade=self.sala1)
        self.assertEqual(c.valor, D('950'))  # 1000 - 100 + 5% de 1000
        self.assertEqual([(i.descricao, i.valor) for i in c.itens.all()],
                         [('Aluguel', D('1000')), ('Desconto de fidelidade', D('-100')), ('Taxa de uso', D('50.00'))])

    def test_ajuste_so_vale_no_periodo(self):
        ajuste(self.c1, inicio=date(2026, 10, 1), fim=date(2026, 12, 31))
        servicos.gerar_alugueis(MES)
        self.assertEqual(Cobranca.objects.get().valor, D('2500'))
        servicos.gerar_alugueis(date(2026, 10, 1))
        self.assertEqual(Cobranca.objects.get(referencia=date(2026, 10, 1)).valor, D('2400'))
        servicos.gerar_alugueis(date(2027, 1, 1))
        self.assertEqual(Cobranca.objects.get(referencia=date(2027, 1, 1)).valor, D('2500'))

    def test_ajuste_do_condominio_nao_mexe_no_aluguel_e_vice_versa(self):
        ajuste(self.c1, aplica_em='condominio', valor=D('50'))
        servicos.gerar_alugueis(MES)
        self.assertEqual(Cobranca.objects.get().valor, D('2500'))

    def test_desconto_de_percentual_no_condominio(self):
        ajuste(self.c1, aplica_em='condominio', forma='percentual', valor=D('10'))
        comp = Competencia.objects.create(referencia=MES)
        Despesa.objects.create(competencia=comp, descricao='Limpeza', valor=D('1000'), tipo='demais',
                               vencimento=date(2026, 9, 6), chave='l')
        calcular_rateio(comp)
        base = {i.unidade_id: i.total for i in comp.rateio.itens.all()}
        servicos.aprovar_rateio(comp, Usuario.objects.create_user('s', password='x', perfil='sindico'))
        c = Cobranca.objects.get(tipo='condominio')
        desconto = (base[self.sala1.pk] * D('0.10')).quantize(D('0.01'))
        self.assertEqual(c.valor, base[self.sala1.pk] + base[self.sala2.pk] - desconto)

    def test_desconto_que_zera_o_aluguel_nao_gera_cobranca(self):
        ajuste(self.c1, valor=D('1000'))
        self.empresa.cobranca_unificada = False
        self.empresa.save()
        servicos.gerar_alugueis(MES)
        self.assertFalse(Cobranca.objects.filter(unidade=self.sala1).exists())
        self.assertTrue(Cobranca.objects.filter(unidade=self.sala2).exists())

    def test_percentual_acima_de_100_e_recusado(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            ajuste(self.c1, forma='percentual', valor=D('150')).full_clean()


class AjustePelaTelaTests(Base):
    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.admin = Usuario.objects.create_user('adm', password='x', perfil='consulta')

    def test_sindico_adiciona_e_remove(self):
        self.client.force_login(self.sindico)
        resposta = self.client.post(reverse('ajuste_criar', args=[self.c1.pk]), dict(
            descricao='Desconto', natureza='desconto', forma='valor', valor='200', aplica_em='aluguel'))
        self.assertRedirects(resposta, reverse('contrato_detalhe', args=[self.c1.pk]))
        item = self.c1.ajustes.get()
        self.assertContains(self.client.get(reverse('contrato_detalhe', args=[self.c1.pk])), 'Desconto')
        self.client.post(reverse('ajuste_excluir', args=[item.pk]))
        self.assertEqual(self.c1.ajustes.count(), 0)

    def test_quem_nao_edita_contrato_e_barrado(self):
        self.client.force_login(self.admin)
        resposta = self.client.post(reverse('ajuste_criar', args=[self.c1.pk]), dict(
            descricao='x', natureza='desconto', forma='valor', valor='1', aplica_em='aluguel'))
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(self.c1.ajustes.count(), 0)

    def test_empresa_ve_o_desconto_do_proprio_contrato(self):
        ajuste(self.c1, descricao='Desconto combinado')
        responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        self.client.force_login(responsavel)
        self.assertContains(self.client.get(reverse('contrato_detalhe', args=[self.c1.pk])), 'Desconto combinado')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class BoletoTests(Base):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings
        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.admin = Usuario.objects.create_user('adm', password='x', perfil='administrativo')
        self.equipe = Usuario.objects.create_user('eq', password='x', perfil='equipe')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        self.estranho = Usuario.objects.create_user('estr', password='x', perfil='locatario',
                                                    locatario=Locatario.objects.create(razao_social='Outra'))
        servicos.gerar_alugueis(MES)
        self.cobranca = Cobranca.objects.get()

    def enviar(self, nome='boleto.pdf', conteudo=b'%PDF-1.4 teste'):
        return self.client.post(reverse('boleto_enviar', args=[self.cobranca.pk]),
                                {'arquivo': SimpleUploadedFile(nome, conteudo)})

    def test_sindico_salva_e_a_empresa_baixa(self):
        self.client.force_login(self.sindico)
        self.assertRedirects(self.enviar(), reverse('cobranca_detalhe', args=[self.cobranca.pk]))
        self.cobranca.refresh_from_db()
        self.assertTrue(self.cobranca.boleto)
        self.assertEqual(self.cobranca.boleto_nome, 'boleto.pdf')
        self.client.force_login(self.responsavel)
        resposta = self.client.get(reverse('boleto_baixar', args=[self.cobranca.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(b''.join(resposta.streaming_content), b'%PDF-1.4 teste')
        self.assertIn('attachment', resposta['Content-Disposition'])

    def test_outra_empresa_nao_ve_nem_baixa(self):
        self.client.force_login(self.sindico)
        self.enviar()
        self.client.force_login(self.estranho)
        self.assertEqual(self.client.get(reverse('boleto_baixar', args=[self.cobranca.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk])).status_code, 404)

    def test_empresa_nao_envia_boleto(self):
        self.client.force_login(self.responsavel)
        self.assertEqual(self.enviar().status_code, 403)
        self.cobranca.refresh_from_db()
        self.assertFalse(self.cobranca.boleto)

    def test_equipe_nao_ve_cobranca(self):
        self.client.force_login(self.equipe)
        self.assertEqual(self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('boleto_baixar', args=[self.cobranca.pk])).status_code, 403)

    def test_tipo_de_arquivo_e_validado(self):
        self.client.force_login(self.sindico)
        self.enviar('programa.exe', b'MZ')
        self.cobranca.refresh_from_db()
        self.assertFalse(self.cobranca.boleto)

    def test_trocar_e_remover(self):
        self.client.force_login(self.admin)
        self.enviar('um.pdf')
        self.cobranca.refresh_from_db()
        primeiro = self.cobranca.boleto.name
        self.enviar('dois.pdf', b'%PDF-2')
        self.cobranca.refresh_from_db()
        self.assertEqual(self.cobranca.boleto_nome, 'dois.pdf')
        self.assertNotEqual(self.cobranca.boleto.name, primeiro)
        self.client.post(reverse('boleto_remover', args=[self.cobranca.pk]))
        self.cobranca.refresh_from_db()
        self.assertFalse(self.cobranca.boleto)

    def test_empresa_ve_a_cobranca_unificada_com_os_itens(self):
        self.client.force_login(self.responsavel)
        pagina = self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk]))
        self.assertContains(pagina, 'Sala 01')
        self.assertContains(pagina, 'Sala 02')
        lista = self.client.get(reverse('cobranca_lista'))
        self.assertEqual(list(lista.context['cobrancas']), [self.cobranca])

    def test_responsavel_restrito_a_uma_unidade_nao_ve_a_cobranca_somada(self):
        restrito = Usuario.objects.create_user('restrito', password='x', perfil='locatario',
                                               locatario=self.empresa, unidade=self.sala1)
        self.client.force_login(restrito)
        self.assertEqual(list(self.client.get(reverse('cobranca_lista')).context['cobrancas']), [])
        self.assertEqual(self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk])).status_code, 404)

    def test_membro_nao_ve_cobrancas(self):
        membro = Usuario.objects.create_user('membro', password='x', perfil='membro', locatario=self.empresa)
        self.client.force_login(membro)
        self.assertEqual(self.client.get(reverse('cobranca_lista')).status_code, 403)


class MigracaoItensTests(Base):
    def test_cobranca_sem_itens_nao_quebra_a_origem(self):
        c = Cobranca.objects.create(unidade=self.sala1, locatario=self.empresa, tipo='aluguel', referencia=MES,
                                    valor=D('1000'), vencimento=date(2026, 9, 10))
        self.assertEqual(c.origem, 'Sala 01')
        self.assertEqual(CobrancaItem.objects.count(), 0)
