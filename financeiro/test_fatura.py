"""Fatura do condomínio em PDF."""

from datetime import date
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.urls import reverse

from cadastros.models import Locatario
from contratos.models import AjusteContrato
from core.models import Usuario

from . import fatura, servicos
from .calculo import calcular_rateio
from .models import Cobranca, Competencia, Despesa, EnvioCobranca
from .test_unificacao import Base

D = Decimal
MES = date(2026, 9, 1)




class FaturaTests(Base):
    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario', locatario=self.empresa)
        self.estranho = Usuario.objects.create_user('estr', password='x', perfil='locatario',
                                                    locatario=Locatario.objects.create(razao_social='Outra'))
        self.comp = Competencia.objects.create(referencia=MES)
        for desc, valor, tipo, cat in [('Conta de luz', '3000', 'energia', 'Energia Elétrica'),
                                       ('Conta de água', '400', 'agua', 'Água'),
                                       ('Limpeza', '900', 'demais', 'Limpeza'),
                                       ('Faxina extra', '100', 'demais', 'Limpeza'),
                                       ('Multa', '50', 'demais', '')]:
            Despesa.objects.create(competencia=self.comp, descricao=desc, valor=D(valor), tipo=tipo, categoria=cat,
                                   vencimento=date(2026, 9, 5), chave=desc)
        Despesa.objects.create(competencia=self.comp, descricao='Fora', valor=D('999'), tipo='demais',
                               entra_no_rateio=False, motivo_exclusao='x', vencimento=date(2026, 9, 5), chave='fora')
        calcular_rateio(self.comp)
        AjusteContrato.objects.create(contrato=self.c1, descricao='Desconto', aplica_em='condominio', valor=D('10'))
        servicos.aprovar_rateio(self.comp, self.sindico)
        self.cobranca = Cobranca.objects.get(tipo='condominio')

    def test_dados_somam_so_o_que_entra_no_rateio_por_categoria(self):
        d = fatura.dados(self.cobranca)
        categorias = dict(d['categorias'])
        self.assertEqual(categorias['Limpeza'], D('1000'))
        self.assertEqual(categorias['Energia Elétrica'], D('3000'))
        self.assertEqual(categorias['Água'], D('400'))
        self.assertEqual(categorias['Demais (sem categoria)'], D('50'))
        self.assertEqual(d['total_gastos'], D('4450'))  # a despesa fora do rateio não entra
        self.assertEqual(d['categorias'][0][0], 'Energia Elétrica')  # maior primeiro
        self.assertEqual(len(d['rateio_itens']), 2)
        self.assertEqual([a.descricao for a in d['ajustes']], ['Desconto'])
        self.assertEqual(d['meus'], sum(r.total for r in d['rateio_itens']))

    def test_pdf_e_gerado(self):
        pdf = fatura.gerar_pdf(self.cobranca)
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertGreater(len(pdf), 1500)

    def test_empresa_baixa_a_propria_fatura(self):
        self.client.force_login(self.responsavel)
        resposta = self.client.get(reverse('fatura_baixar', args=[self.cobranca.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertIn('attachment', resposta['Content-Disposition'])
        self.assertIn('fatura-2026-09-grupo-vida', resposta['Content-Disposition'])
        self.assertTrue(resposta.content.startswith(b'%PDF-'))

    def test_outra_empresa_nao_baixa(self):
        self.client.force_login(self.estranho)
        self.assertEqual(self.client.get(reverse('fatura_baixar', args=[self.cobranca.pk])).status_code, 404)

    def test_aluguel_nao_tem_fatura(self):
        servicos.gerar_alugueis(MES)
        aluguel = Cobranca.objects.get(tipo='aluguel')
        self.assertFalse(aluguel.fatura_disponivel)
        self.client.force_login(self.sindico)
        self.assertEqual(self.client.get(reverse('fatura_baixar', args=[aluguel.pk])).status_code, 404)

    def test_botao_aparece_so_no_condominio(self):
        self.client.force_login(self.responsavel)
        self.assertContains(self.client.get(reverse('cobranca_detalhe', args=[self.cobranca.pk])), 'Fatura do condomínio')

    def test_fatura_sem_despesas_nao_quebra(self):
        self.comp.despesas.all().delete()
        self.assertTrue(fatura.gerar_pdf(self.cobranca).startswith(b'%PDF-'))

    @override_settings(EMAIL_HOST='smtp.teste', DEFAULT_FROM_EMAIL='Play <c@play.test>')
    def test_email_leva_a_fatura_em_anexo(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('cobranca_enviar', args=[self.cobranca.pk]),
                         {'para': 'a@b.test', 'anexar_fatura': 'on'})
        anexos = mail.outbox[0].attachments
        self.assertEqual([a[0] for a in anexos], ['fatura-condominio-2026-09.pdf'])
        self.assertEqual(anexos[0][2], 'application/pdf')
        self.assertTrue(EnvioCobranca.objects.get().com_fatura)

    @override_settings(EMAIL_HOST='smtp.teste', DEFAULT_FROM_EMAIL='Play <c@play.test>')
    def test_email_sem_marcar_a_fatura_vai_sem_ela(self):
        self.client.force_login(self.sindico)
        self.client.post(reverse('cobranca_enviar', args=[self.cobranca.pk]), {'para': 'a@b.test'})
        self.assertEqual(mail.outbox[0].attachments, [])
