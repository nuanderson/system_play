import io
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from cadastros.models import Locatario, Unidade
from contratos.models import Contrato
from core.models import Usuario

from . import servicos
from .calculo import ErroRateio, calcular_rateio, dividir, ratear_componente
from .importacao import ErroImportacao, importar, ler_planilha
from .models import Cobranca, Competencia, Despesa, data_no_mes, somar_meses

D = Decimal
CABECALHO = [
    'Identificador do fornecedor', 'Nome do fornecedor', 'Data de vencimento', 'Descrição',
    'Valor original da parcela (R$)', 'Juros realizado (R$)', 'Multa realizado (R$)',
    'Categoria 1', 'Centro de Custo 1',
]


def planilha(linhas):
    livro = openpyxl.Workbook()
    folha = livro.active
    folha.append(CABECALHO)
    for fornecedor, venc, descricao, valor, multa, categoria in linhas:
        folha.append(['', fornecedor, venc, descricao, valor, 0, multa, categoria, 'Edifício Uruz'])
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


class DivisaoTests(SimpleTestCase):
    def test_divisao_fecha_no_centavo(self):
        partes = dividir(D('100.00'), {'a': D(1), 'b': D(1), 'c': D(1)})
        self.assertEqual(sum(partes.values()), D('100.00'))
        self.assertEqual(sorted(partes.values()), [D('33.33'), D('33.33'), D('33.34')])

    def test_porcentagens_fixas_acima_de_100_dao_erro(self):
        unidades = {1: dict(area=D(10), pct_energia=D(70), pct_agua=None),
                    2: dict(area=D(10), pct_energia=D(40), pct_agua=None)}
        with self.assertRaises(ErroRateio):
            ratear_componente(D('1000'), unidades, 'pct_energia', 'energia')

    def test_data_no_mes_e_somar_meses(self):
        self.assertEqual(data_no_mes(2027, 2, 31), date(2027, 2, 28))
        self.assertEqual(somar_meses(date(2026, 12, 1), 1), date(2027, 1, 1))


class BaseFinanceiro(TestCase):
    """Reproduz o prédio: restaurante com 60% da energia e 40% da água, os demais por área."""

    def setUp(self):
        self.sindico = Usuario.objects.create_user('sindico', password='x', perfil=Usuario.Perfil.SINDICO)
        self.comp = Competencia.objects.create(referencia=date(2026, 9, 1))

    def contratar(self, nome_unidade, empresa, **extra):
        locatario = Locatario.objects.create(razao_social=empresa)
        return Contrato.objects.create(
            unidade=Unidade.objects.get(nome=nome_unidade), locatario=locatario,
            status=Contrato.Status.VIGENTE, data_inicio=date(2026, 1, 1), valor_aluguel=D('1000'), **extra)

    def contratar_predio_inteiro(self):
        self.contratar('Térreo (clínica)', 'Clínica')
        self.contratar('1º andar (academia)', 'Academia')
        self.contratar('3º andar (restaurante)', 'Restaurante', pct_energia=D('60'), pct_agua=D('40'))
        for i in range(1, 9):
            self.contratar(f'Sala 0{i}', f'Empresa {i}')

    def despesas_de_setembro(self):
        for desc, valor, tipo in [('Energia', '29023.34', 'energia'), ('Água', '1788.10', 'agua'),
                                  ('Manutenção e demais', '18621.30', 'demais')]:
            Despesa.objects.create(
                competencia=self.comp, descricao=desc, valor=D(valor), tipo=tipo,
                vencimento=date(2026, 9, 15), chave=desc)

    def total_do(self, rateio, *nomes):
        return sum(i.total for i in rateio.itens.all() if i.unidade.nome in nomes)


class RateioDeSetembroTests(BaseFinanceiro):
    def test_reproduz_o_rateio_de_setembro(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        rateio = calcular_rateio(self.comp)
        self.assertEqual(rateio.total, D('49432.74'))
        self.assertEqual(sum(i.total for i in rateio.itens.all()), D('49432.74'))
        # Valores conferidos com Decimal fora do sistema; a tolerância cobre só o arredondamento por unidade.
        tolerancia = D('0.03')
        restaurante = self.total_do(rateio, '3º andar (restaurante)')
        self.assertAlmostEqual(restaurante, D('22072.45'), delta=tolerancia)
        self.assertAlmostEqual(self.total_do(rateio, 'Térreo (clínica)'), D('10041.48'), delta=tolerancia)
        self.assertAlmostEqual(self.total_do(rateio, '1º andar (academia)'), D('9587.59'), delta=tolerancia)
        salas = self.total_do(rateio, *[f'Sala 0{i}' for i in range(1, 9)])
        self.assertAlmostEqual(salas, D('7731.22'), delta=tolerancia)

    def test_restaurante_paga_60_da_energia_e_40_da_agua(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        rateio = calcular_rateio(self.comp)
        item = rateio.itens.get(unidade__nome='3º andar (restaurante)')
        self.assertEqual(item.energia, D('17414.00'))
        self.assertEqual(item.agua, D('715.24'))

    def test_salas_dividem_pela_area_privativa(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        rateio = calcular_rateio(self.comp)
        sala_05 = rateio.itens.get(unidade__nome='Sala 05').total
        sala_01 = rateio.itens.get(unidade__nome='Sala 01').total
        # 23,13 m² contra 15,46 m²
        self.assertAlmostEqual(sala_05 / sala_01, D('23.13') / D('15.46'), delta=D('0.01'))

    def test_unidade_sem_contrato_fica_com_a_play_e_nao_gera_cobranca(self):
        self.contratar_predio_inteiro()
        Contrato.objects.filter(unidade__nome='Sala 08').update(status=Contrato.Status.ENCERRADO)
        self.despesas_de_setembro()
        rateio = calcular_rateio(self.comp)
        item = rateio.itens.get(unidade__nome='Sala 08')
        self.assertIsNone(item.locatario)
        self.assertEqual(sum(i.total for i in rateio.itens.all()), D('49432.74'))
        servicos.aprovar_rateio(self.comp, self.sindico)
        self.assertFalse(Cobranca.objects.filter(unidade__nome='Sala 08').exists())
        self.assertEqual(Cobranca.objects.count(), 10)

    def test_despesa_fora_do_rateio_nao_entra(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        Despesa.objects.create(
            competencia=self.comp, descricao='Despesa da Play', valor=D('1000'), tipo='demais',
            vencimento=date(2026, 9, 20), chave='play', entra_no_rateio=False, motivo_exclusao='Da Play')
        self.assertEqual(calcular_rateio(self.comp).total, D('49432.74'))

    def test_sem_despesas_da_erro(self):
        with self.assertRaises(ErroRateio):
            calcular_rateio(self.comp)


class AprovacaoTests(BaseFinanceiro):
    def test_aprovar_gera_cobrancas_e_trava_a_competencia(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        calcular_rateio(self.comp)
        resumo = servicos.aprovar_rateio(self.comp, self.sindico)
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.APROVADA)
        self.assertEqual(resumo['criadas'], 11)
        total = sum(c.valor for c in Cobranca.objects.filter(tipo=Cobranca.Tipo.CONDOMINIO))
        self.assertEqual(total, D('49432.74'))
        cobranca = Cobranca.objects.get(unidade__nome='Térreo (clínica)')
        self.assertEqual(cobranca.vencimento, date(2026, 10, 10))
        self.assertEqual(cobranca.referencia, date(2026, 9, 1))

    def test_nao_aprova_sem_calcular(self):
        with self.assertRaises(ErroRateio):
            servicos.aprovar_rateio(self.comp, self.sindico)

    def test_aprovada_nao_recalcula(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        calcular_rateio(self.comp)
        servicos.aprovar_rateio(self.comp, self.sindico)
        with self.assertRaises(ErroRateio):
            calcular_rateio(self.comp)

    def test_gerar_alugueis_nao_duplica(self):
        self.contratar('Sala 01', 'Empresa X')
        primeira = servicos.gerar_alugueis(date(2026, 10, 1))
        segunda = servicos.gerar_alugueis(date(2026, 10, 1))
        self.assertEqual((primeira['criadas'], segunda['criadas'], segunda['ja_existiam']), (1, 0, 1))
        cobranca = Cobranca.objects.get()
        self.assertEqual((cobranca.valor, cobranca.vencimento), (D('1000.00'), date(2026, 10, 10)))

    def test_aluguel_nao_gera_antes_do_inicio_do_contrato(self):
        self.contratar('Sala 01', 'Empresa X')
        resumo = servicos.gerar_alugueis(date(2025, 12, 1))
        self.assertEqual(resumo['criadas'], 0)

    def test_cobranca_atrasada(self):
        unidade = Unidade.objects.get(nome='Sala 01')
        locatario = Locatario.objects.create(razao_social='Z')
        ontem = timezone.localdate() - timedelta(days=3)
        cobranca = Cobranca.objects.create(
            unidade=unidade, locatario=locatario, tipo='aluguel', referencia=ontem.replace(day=1),
            valor=D('10'), vencimento=ontem)
        self.assertTrue(cobranca.atrasada)
        self.assertEqual(cobranca.dias_atraso, 3)
        self.assertEqual(Cobranca.atrasadas().count(), 1)


class ImportacaoTests(BaseFinanceiro):
    def linhas(self):
        return planilha([
            ('Energisa', date(2026, 9, 15), 'Energia Prédio', 1000.5, 0, 'Energia Elétrica'),
            ('BRK', '18/09/2026', 'Conta de Água', 200, 0, 'Água e Saneamento'),
            ('Forte Limp', date(2026, 9, 23), 'Limpeza', 300, 12.5, 'Manutenção Predial'),
            ('Forte Limp', date(2026, 9, 23), 'Limpeza', 300, 0, 'Manutenção Predial'),
            ('Outro mês', date(2026, 10, 2), 'Fora', 50, 0, 'Manutenção Predial'),
        ])

    def test_le_importa_e_classifica(self):
        resumo = importar(self.comp, ler_planilha(self.linhas()))
        self.assertEqual(resumo, dict(novas=4, repetidas=0, fora_do_mes=1))
        self.assertEqual(Despesa.objects.get(descricao='Energia Prédio').tipo, 'energia')
        self.assertEqual(Despesa.objects.get(descricao='Conta de Água').tipo, 'agua')
        self.assertEqual(Despesa.objects.filter(descricao='Limpeza').count(), 2)
        self.assertEqual(Despesa.objects.get(descricao='Limpeza', multa_juros__gt=0).multa_juros, D('12.50'))

    def test_reimportar_nao_duplica(self):
        importar(self.comp, ler_planilha(self.linhas()))
        resumo = importar(self.comp, ler_planilha(self.linhas()))
        self.assertEqual((resumo['novas'], resumo['repetidas']), (0, 4))
        self.assertEqual(Despesa.objects.count(), 4)

    def test_arquivo_invalido(self):
        with self.assertRaises(ErroImportacao):
            ler_planilha(b'isto nao e uma planilha')

    def test_planilha_sem_colunas_obrigatorias(self):
        livro = openpyxl.Workbook()
        livro.active.append(['Coluna qualquer'])
        saida = io.BytesIO()
        livro.save(saida)
        with self.assertRaises(ErroImportacao):
            ler_planilha(saida.getvalue())


class FluxoPelasTelasTests(BaseFinanceiro):
    def test_importar_calcular_e_aprovar_pela_interface(self):
        self.contratar_predio_inteiro()
        self.client.force_login(self.sindico)
        arquivo = SimpleUploadedFile('relatorio.xlsx', planilha([
            ('Energisa', date(2026, 9, 15), 'Energia', 29023.34, 0, 'Energia Elétrica'),
            ('BRK', date(2026, 9, 18), 'Água', 1788.10, 0, 'Água e Saneamento'),
            ('Diversos', date(2026, 9, 20), 'Manutenção', 18621.30, 0, 'Manutenção Predial'),
        ]))
        resposta = self.client.post(reverse('competencia_nova'), {'mes': '2026-09', 'arquivo': arquivo})
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Despesa.objects.count(), 3)
        url = reverse('competencia_detalhe', args=[self.comp.pk])
        self.assertContains(self.client.get(url), '49.432,74')
        self.client.post(reverse('rateio_calcular', args=[self.comp.pk]))
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.CALCULADA)
        self.assertContains(self.client.get(url), 'Aprovar e gerar cobranças')
        self.client.post(reverse('rateio_aprovar', args=[self.comp.pk]))
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.APROVADA)
        self.assertEqual(Cobranca.objects.count(), 11)
        resposta = self.client.post(reverse('despesas_salvar', args=[self.comp.pk]), {})
        self.assertEqual(resposta.status_code, 403)

    def test_excluir_despesa_exige_motivo_e_invalida_o_calculo(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        calcular_rateio(self.comp)
        self.client.force_login(self.sindico)
        despesas = list(self.comp.despesas.all())

        def dados(excluir_primeira, motivo):
            base = {'form-TOTAL_FORMS': len(despesas), 'form-INITIAL_FORMS': len(despesas),
                    'form-MIN_NUM_FORMS': 0, 'form-MAX_NUM_FORMS': 1000}
            for i, d in enumerate(despesas):
                base[f'form-{i}-id'] = d.pk
                base[f'form-{i}-tipo'] = d.tipo
                if not (excluir_primeira and i == 0):
                    base[f'form-{i}-entra_no_rateio'] = 'on'
                base[f'form-{i}-motivo_exclusao'] = motivo if (excluir_primeira and i == 0) else ''
            return base

        url = reverse('despesas_salvar', args=[self.comp.pk])
        self.client.post(url, dados(True, ''))
        self.assertTrue(Despesa.objects.get(pk=despesas[0].pk).entra_no_rateio)
        self.client.post(url, dados(True, 'Despesa da Play'))
        self.assertFalse(Despesa.objects.get(pk=despesas[0].pk).entra_no_rateio)
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.ABERTA)

    def test_registrar_pagamento_e_cancelar(self):
        unidade = Unidade.objects.get(nome='Sala 01')
        locatario = Locatario.objects.create(razao_social='Z')
        a = Cobranca.objects.create(unidade=unidade, locatario=locatario, tipo='aluguel',
                                    referencia=date(2026, 10, 1), valor=D('100'), vencimento=date(2026, 10, 10))
        b = Cobranca.objects.create(unidade=unidade, locatario=locatario, tipo='condominio',
                                    referencia=date(2026, 10, 1), valor=D('50'), vencimento=date(2026, 10, 10))
        self.client.force_login(self.sindico)
        self.client.post(reverse('cobranca_pagar', args=[a.pk]), {'pago_em': '2026-10-09', 'valor_pago': '100.00'})
        self.client.post(reverse('cobranca_cancelar', args=[b.pk]))
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual((a.status, a.pago_em), ('paga', date(2026, 10, 9)))
        self.assertEqual(b.status, 'cancelada')


class PermissoesFinanceiroTests(BaseFinanceiro):
    def setUp(self):
        super().setUp()
        self.sala1 = Unidade.objects.get(nome='Sala 01')
        self.sala2 = Unidade.objects.get(nome='Sala 02')
        self.resp1 = Usuario.objects.create_user('r1', password='x', perfil=Usuario.Perfil.LOCATARIO, unidade=self.sala1)
        self.membro = Usuario.objects.create_user('m', password='x', perfil=Usuario.Perfil.MEMBRO, unidade=self.sala1)
        empresa = Locatario.objects.create(razao_social='Empresa Visível')
        outra = Locatario.objects.create(razao_social='Empresa Alheia')
        for unidade, loc in ((self.sala1, empresa), (self.sala2, outra)):
            Cobranca.objects.create(unidade=unidade, locatario=loc, tipo='aluguel', referencia=date(2026, 10, 1),
                                    valor=D('100'), vencimento=date(2026, 10, 10))

    def test_responsavel_ve_so_as_proprias_cobrancas(self):
        self.client.force_login(self.resp1)
        resposta = self.client.get(reverse('cobranca_lista'))
        self.assertContains(resposta, 'Empresa Visível')
        self.assertNotContains(resposta, 'Empresa Alheia')

    def test_responsavel_nao_acessa_rateio_nem_acoes(self):
        self.client.force_login(self.resp1)
        pk = Cobranca.objects.first().pk
        self.assertEqual(self.client.get(reverse('competencia_lista')).status_code, 403)
        self.assertEqual(self.client.get(reverse('competencia_detalhe', args=[self.comp.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('cobranca_pagar', args=[pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('cobranca_cancelar', args=[pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('aluguel_gerar')).status_code, 403)

    def test_membro_nao_acessa_cobrancas(self):
        self.client.force_login(self.membro)
        self.assertEqual(self.client.get(reverse('cobranca_lista')).status_code, 403)

    def test_sindico_acessa_tudo(self):
        self.client.force_login(self.sindico)
        for nome in ('competencia_lista', 'competencia_nova', 'cobranca_lista', 'aluguel_gerar'):
            self.assertEqual(self.client.get(reverse(nome)).status_code, 200, nome)

    def test_inicio_do_sindico_mostra_indicadores_financeiros(self):
        self.client.force_login(self.sindico)
        resposta = self.client.get(reverse('inicio'))
        self.assertContains(resposta, 'cobranças em aberto')
        self.assertContains(resposta, 'R$ 200,00')
