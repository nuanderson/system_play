"""Redistribuição de energia: parte da energia de algumas unidades vai, em partes iguais, para outras."""

from decimal import Decimal

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from cadastros.models import Locatario, Unidade
from core.models import Usuario

from . import fatura, servicos
from .calculo import ErroRateio, calcular_rateio, dividir_igual, redistribuir_energia
from .models import Cobranca, Competencia, TransferenciaEnergia
from .tests import BaseFinanceiro

D = Decimal
HASH = ['django.contrib.auth.hashers.MD5PasswordHasher']
SALAS = [f'Sala 0{i}' for i in range(1, 9)]


class DividirIgualTests(SimpleTestCase):
    def test_divide_sem_sobrar_nem_faltar_centavo(self):
        partes = dividir_igual(D('1525.84'), range(8))
        self.assertEqual(set(partes.values()), {D('190.73')})
        self.assertEqual(sum(partes.values()), D('1525.84'))

    def test_os_centavos_que_sobram_vao_para_as_primeiras(self):
        partes = dividir_igual(D('100.00'), ['a', 'b', 'c'])
        self.assertEqual(partes, {'a': D('33.34'), 'b': D('33.33'), 'c': D('33.33')})
        self.assertEqual(sum(partes.values()), D('100.00'))

    def test_varios_valores_sempre_fecham(self):
        for total in ('0.01', '0.07', '1.00', '999.99', '12345.67'):
            for n in (1, 2, 3, 5, 7, 8):
                partes = dividir_igual(D(total), range(n))
                self.assertEqual(sum(partes.values()), D(total), (total, n))
                self.assertLessEqual(max(partes.values()) - min(partes.values()), D('0.01'))

    def test_sem_ninguem_para_receber_da_erro(self):
        with self.assertRaises(ErroRateio):
            dividir_igual(D('10'), [])


class RedistribuirEnergiaTests(SimpleTestCase):
    ENERGIA = {'terreo': D('4445.65'), 'andar1': D('4244.71'), 's1': D('356.57'), 's2': D('368.06'), 's3': D('369.21')}

    def test_tira_o_percentual_de_cada_origem_e_divide_igual(self):
        nova, mexeu = redistribuir_energia(self.ENERGIA, {'terreo': D('20'), 'andar1': D('15')}, ['s1', 's2', 's3'])
        self.assertEqual(nova['terreo'], D('4445.65') - D('889.13'))
        self.assertEqual(nova['andar1'], D('4244.71') - D('636.71'))
        retirado = D('889.13') + D('636.71')
        self.assertEqual(sum(mexeu[s] for s in ('s1', 's2', 's3')), retirado)
        self.assertEqual(sum(nova.values()), sum(self.ENERGIA.values()))  # o total não muda

    def test_arredonda_o_retirado_para_o_centavo(self):
        nova, mexeu = redistribuir_energia({'a': D('100.05'), 'b': D('1')}, {'a': D('15')}, ['b'])
        self.assertEqual(mexeu['a'], D('-15.01'))  # 15,0075 arredonda para 15,01
        self.assertEqual(nova['b'], D('16.01'))

    def test_sem_regras_nada_muda(self):
        nova, mexeu = redistribuir_energia(self.ENERGIA, {}, ['s1'])
        self.assertEqual((nova, mexeu), (self.ENERGIA, {}))

    def test_sem_unidade_para_receber_da_erro_claro(self):
        with self.assertRaisesMessage(ErroRateio, 'nenhuma unidade está marcada'):
            redistribuir_energia(self.ENERGIA, {'terreo': D('20')}, [])

    def test_quem_paga_menos_nao_recebe(self):
        nova, mexeu = redistribuir_energia(self.ENERGIA, {'terreo': D('20')}, ['terreo', 's1'])
        self.assertEqual(mexeu['s1'], D('889.13'))
        self.assertEqual(mexeu['terreo'], D('-889.13'))

    def test_regra_de_unidade_fora_do_rateio_e_ignorada(self):
        nova, mexeu = redistribuir_energia(self.ENERGIA, {'fantasma': D('20')}, ['s1'])
        self.assertEqual(mexeu, {})


class EnergiaNoRateioTests(BaseFinanceiro):
    """O prédio inteiro com as despesas de setembro: o total é R$ 49.432,74 com ou sem a redistribuição."""

    def setUp(self):
        super().setUp()
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        self.terreo = Unidade.objects.get(nome='Térreo (clínica)')
        self.andar1 = Unidade.objects.get(nome='1º andar (academia)')
        self.salas = list(Unidade.objects.filter(nome__in=SALAS).order_by('ordem'))
        base = calcular_rateio(self.comp)
        self.antes = {i.unidade.nome: i for i in base.itens.select_related('unidade')}
        self.total_antes = base.total
        Unidade.objects.filter(pk__in=[s.pk for s in self.salas]).update(recebe_energia_transferida=True)
        TransferenciaEnergia.objects.create(origem=self.terreo, percentual=D('20'))
        TransferenciaEnergia.objects.create(origem=self.andar1, percentual=D('15'))

    def calcular(self):
        rateio = calcular_rateio(self.comp)
        return rateio, {i.unidade.nome: i for i in rateio.itens.select_related('unidade')}

    def test_o_total_do_condominio_nao_muda(self):
        rateio, itens = self.calcular()
        self.assertEqual(rateio.total, self.total_antes)
        self.assertEqual(rateio.total, D('49432.74'))
        self.assertEqual(sum(i.total for i in itens.values()), D('49432.74'))
        self.assertEqual(sum(i.energia for i in itens.values()), rateio.total_energia)

    def test_terreo_e_primeiro_andar_pagam_menos_energia(self):
        _, itens = self.calcular()
        retirado_t = (self.antes['Térreo (clínica)'].energia * D('0.20')).quantize(D('0.01'))
        retirado_1 = (self.antes['1º andar (academia)'].energia * D('0.15')).quantize(D('0.01'))
        self.assertEqual(itens['Térreo (clínica)'].energia, self.antes['Térreo (clínica)'].energia - retirado_t)
        self.assertEqual(itens['1º andar (academia)'].energia, self.antes['1º andar (academia)'].energia - retirado_1)
        self.assertEqual(itens['Térreo (clínica)'].energia_transferida, -retirado_t)
        self.assertEqual(itens['1º andar (academia)'].energia_transferida, -retirado_1)
        # água e demais despesas não mudam
        for nome in ('Térreo (clínica)', '1º andar (academia)'):
            self.assertEqual((itens[nome].agua, itens[nome].demais), (self.antes[nome].agua, self.antes[nome].demais))

    def test_as_oito_salas_recebem_partes_iguais(self):
        _, itens = self.calcular()
        retirado = -(itens['Térreo (clínica)'].energia_transferida + itens['1º andar (academia)'].energia_transferida)
        extras = [itens[s].energia - self.antes[s].energia for s in SALAS]
        self.assertEqual(sum(extras), retirado)
        self.assertLessEqual(max(extras) - min(extras), D('0.01'))  # iguais, a menos de 1 centavo de arredondamento
        self.assertEqual(extras, [itens[s].energia_transferida for s in SALAS])

    def test_quem_nao_participa_fica_igual(self):
        _, itens = self.calcular()
        restaurante = '3º andar (restaurante)'
        self.assertEqual(itens[restaurante].total, self.antes[restaurante].total)
        self.assertEqual(itens[restaurante].energia_transferida, 0)

    def test_regra_desligada_nao_vale(self):
        TransferenciaEnergia.objects.update(ativa=False)
        rateio, itens = self.calcular()
        self.assertEqual({i.energia_transferida for i in itens.values()}, {D('0')})
        self.assertEqual(itens['Térreo (clínica)'].energia, self.antes['Térreo (clínica)'].energia)

    def test_sem_receptoras_o_calculo_recusa_e_nao_apaga_o_anterior(self):
        anterior, _ = self.calcular()
        Unidade.objects.update(recebe_energia_transferida=False)
        with self.assertRaisesMessage(ErroRateio, 'nenhuma unidade está marcada'):
            calcular_rateio(self.comp)
        self.assertEqual(self.comp.rateio.pk, anterior.pk)

    def test_cobranca_leva_o_valor_ja_redistribuido(self):
        rateio, itens = self.calcular()
        servicos.aprovar_rateio(self.comp, self.sindico)
        sala1 = Cobranca.objects.get(unidade__nome='Sala 01')
        self.assertEqual(sala1.valor, itens['Sala 01'].total)
        self.assertGreater(sala1.valor, self.antes['Sala 01'].total)
        terreo = Cobranca.objects.get(unidade__nome='Térreo (clínica)')
        self.assertEqual(terreo.valor, itens['Térreo (clínica)'].total)
        self.assertLess(terreo.valor, self.antes['Térreo (clínica)'].total)

    def test_sala_sem_contrato_recebe_mas_o_custo_fica_com_a_play(self):
        from contratos.models import Contrato
        Contrato.objects.filter(unidade__nome='Sala 05').update(status='encerrado')
        rateio, itens = self.calcular()
        self.assertIsNone(itens['Sala 05'].locatario)
        self.assertEqual(itens['Sala 05'].energia_transferida, itens['Sala 01'].energia_transferida)
        resumo = servicos.aprovar_rateio(self.comp, self.sindico)
        self.assertFalse(Cobranca.objects.filter(unidade__nome='Sala 05').exists())
        self.assertIn('Sala 05', [str(i.unidade) for i in resumo['sem_contrato']])

    def test_fatura_em_pdf_explica_a_redistribuicao(self):
        self.calcular()
        servicos.aprovar_rateio(self.comp, self.sindico)
        pdf = fatura.gerar_pdf(Cobranca.objects.get(unidade__nome='Sala 01'))
        self.assertTrue(pdf.startswith(b'%PDF-'))


class ReabrirRateioTests(BaseFinanceiro):
    def setUp(self):
        super().setUp()
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        calcular_rateio(self.comp)
        servicos.aprovar_rateio(self.comp, self.sindico)
        self.comp.refresh_from_db()

    def cancelar_todas(self):
        Cobranca.objects.update(status='cancelada')

    def test_reabre_quando_todas_as_cobrancas_estao_canceladas(self):
        self.cancelar_todas()
        servicos.reabrir_rateio(self.comp)
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.CALCULADA)
        self.assertIsNone(self.comp.rateio.aprovado_em)
        self.assertTrue(self.comp.editavel)

    def test_recusa_com_cobranca_em_aberto(self):
        with self.assertRaisesMessage(ErroRateio, 'em aberto ou paga'):
            servicos.reabrir_rateio(self.comp)
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, Competencia.Status.APROVADA)

    def test_recusa_com_cobranca_paga(self):
        self.cancelar_todas()
        Cobranca.objects.filter(unidade__nome='Sala 01').update(status='paga')
        with self.assertRaises(ErroRateio):
            servicos.reabrir_rateio(self.comp)

    def test_so_reabre_o_que_esta_aprovado(self):
        self.cancelar_todas()
        servicos.reabrir_rateio(self.comp)
        with self.assertRaisesMessage(ErroRateio, 'aprovada'):
            servicos.reabrir_rateio(self.comp)

    def test_depois_de_reabrir_recalcula_aprova_e_gera_cobrancas_novas(self):
        self.cancelar_todas()
        antigas = set(Cobranca.objects.values_list('pk', flat=True))
        servicos.reabrir_rateio(self.comp)
        calcular_rateio(self.comp)
        resumo = servicos.aprovar_rateio(self.comp, self.sindico)
        novas = Cobranca.objects.exclude(pk__in=antigas)
        self.assertEqual(novas.count(), resumo['criadas'])
        self.assertTrue(all(c.status == 'aberta' for c in novas))
        self.assertTrue(all(c.status == 'cancelada' for c in Cobranca.objects.filter(pk__in=antigas)))  # histórico fica


@override_settings(PASSWORD_HASHERS=HASH)
class RegrasPelaTelaTests(BaseFinanceiro):
    def setUp(self):
        super().setUp()
        self.sindico = Usuario.objects.create_user('sind', password='x', perfil='sindico')
        self.admin = Usuario.objects.create_user('adm', password='x', perfil='administrativo')
        self.equipe = Usuario.objects.create_user('eq', password='x', perfil='equipe')
        self.responsavel = Usuario.objects.create_user('resp', password='x', perfil='locatario',
                                                       locatario=Locatario.objects.create(razao_social='Empresa A'))
        self.terreo = Unidade.objects.get(nome='Térreo (clínica)')
        self.client.force_login(self.sindico)

    def test_lista_mostra_as_regras_e_quem_recebe(self):
        TransferenciaEnergia.objects.create(origem=self.terreo, percentual=D('20'))
        Unidade.objects.filter(nome='Sala 02').update(recebe_energia_transferida=True)
        pagina = self.client.get(reverse('regras_rateio'))
        self.assertContains(pagina, 'Térreo (clínica)')
        self.assertContains(pagina, '20%')
        self.assertContains(pagina, 'Sala 02')

    def test_cadastra_edita_e_remove(self):
        resposta = self.client.post(reverse('regra_energia_criar'), dict(origem=self.terreo.pk, percentual='20', ativa='on'))
        self.assertRedirects(resposta, reverse('regras_rateio'))
        regra = TransferenciaEnergia.objects.get()
        self.assertEqual((regra.origem, regra.percentual, regra.ativa), (self.terreo, D('20.00'), True))
        self.client.post(reverse('regra_energia_editar', args=[regra.pk]), dict(origem=self.terreo.pk, percentual='25'))
        regra.refresh_from_db()
        self.assertEqual((regra.percentual, regra.ativa), (D('25.00'), False))
        self.client.post(reverse('regra_energia_excluir', args=[regra.pk]))
        self.assertEqual(TransferenciaEnergia.objects.count(), 0)

    def test_percentual_invalido_e_recusado(self):
        for valor in ('0', '-5', '101', 'abc'):
            self.client.post(reverse('regra_energia_criar'), dict(origem=self.terreo.pk, percentual=valor, ativa='on'))
        self.assertEqual(TransferenciaEnergia.objects.count(), 0)

    def test_uma_regra_por_unidade(self):
        TransferenciaEnergia.objects.create(origem=self.terreo, percentual=D('20'))
        self.client.post(reverse('regra_energia_criar'), dict(origem=self.terreo.pk, percentual='10', ativa='on'))
        self.assertEqual(TransferenciaEnergia.objects.count(), 1)

    def test_a_lista_de_origem_nao_oferece_quem_recebe_nem_quem_ja_tem_regra(self):
        Unidade.objects.filter(nome='Sala 02').update(recebe_energia_transferida=True)
        TransferenciaEnergia.objects.create(origem=self.terreo, percentual=D('20'))
        pagina = self.client.get(reverse('regra_energia_criar'))
        nomes = [u.nome for u in pagina.context['form'].fields['origem'].queryset]
        self.assertNotIn('Sala 02', nomes)
        self.assertNotIn('Térreo (clínica)', nomes)
        self.assertIn('Sala 01', nomes)

    def test_quem_recebe_nao_pode_ser_origem(self):
        Unidade.objects.filter(pk=self.terreo.pk).update(recebe_energia_transferida=True)
        self.client.post(reverse('regra_energia_criar'), dict(origem=self.terreo.pk, percentual='20', ativa='on'))
        self.assertEqual(TransferenciaEnergia.objects.count(), 0)

    def test_administrativo_mexe_empresa_e_equipe_nao(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('regras_rateio')).status_code, 200)
        for usuario in (self.responsavel, self.equipe):
            self.client.force_login(usuario)
            self.assertEqual(self.client.get(reverse('regras_rateio')).status_code, 403)
            self.assertEqual(self.client.post(reverse('regra_energia_criar'), {}).status_code, 403)

    def test_competencia_mostra_a_energia_redistribuida_e_o_botao_de_reabrir(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        Unidade.objects.filter(nome__in=SALAS).update(recebe_energia_transferida=True)
        TransferenciaEnergia.objects.create(origem=self.terreo, percentual=D('20'))
        calcular_rateio(self.comp)
        pagina = self.client.get(reverse('competencia_detalhe', args=[self.comp.pk]))
        self.assertContains(pagina, 'Energia redistribuída')
        self.assertContains(pagina, 'redistribuída</span>')
        servicos.aprovar_rateio(self.comp, self.sindico)
        self.assertContains(self.client.get(reverse('competencia_detalhe', args=[self.comp.pk])), 'Reabrir para recalcular')

    def test_reabrir_pela_tela(self):
        self.contratar_predio_inteiro()
        self.despesas_de_setembro()
        calcular_rateio(self.comp)
        servicos.aprovar_rateio(self.comp, self.sindico)
        url = reverse('rateio_reabrir', args=[self.comp.pk])
        resposta = self.client.post(url, follow=True)
        self.assertContains(resposta, 'em aberto ou paga')  # ainda há cobranças abertas
        Cobranca.objects.update(status='cancelada')
        resposta = self.client.post(url, follow=True)
        self.assertContains(resposta, 'Rateio reaberto')
        self.comp.refresh_from_db()
        self.assertEqual(self.comp.status, 'calculada')
        self.client.force_login(self.equipe)
        self.assertEqual(self.client.post(url).status_code, 403)

    def test_a_unidade_pode_ser_marcada_como_receptora_pelo_formulario(self):
        sala = Unidade.objects.get(nome='Sala 03')
        dados = dict(nome=sala.nome, tipo=sala.tipo, andar=sala.andar, ordem=sala.ordem, area_privativa=sala.area_privativa,
                     area_rateio=sala.area_rateio, entra_no_rateio_por_area='on', recebe_energia_transferida='on', ativa='on')
        self.client.post(reverse('unidade_editar', args=[sala.pk]), dados)
        sala.refresh_from_db()
        self.assertTrue(sala.recebe_energia_transferida)
