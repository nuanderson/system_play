from datetime import date, datetime

from django.shortcuts import render
from django.views import View

from core.permissions import SindicoRequiredMixin
from financeiro.models import Competencia

from . import dados
from .exportacao import data, data_hora, numero, resposta_csv, texto

RELATORIOS = [
    dict(nome='relatorio_despesas', titulo='Despesas do mês', icone='🧾', cor='azul',
         texto='O que o prédio gastou, por categoria, e a evolução dos últimos 12 meses.'),
    dict(nome='relatorio_rateio', titulo='Rateio por unidade', icone='🧮', cor='ciano',
         texto='Quanto cada unidade paga no mês: energia, água e demais despesas.'),
    dict(nome='relatorio_inadimplencia', titulo='Inadimplência e cobranças', icone='💳', cor='marinho',
         texto='Quem está em atraso, por faixa de dias, e o resumo mensal de cobranças.'),
    dict(nome='relatorio_chamados', titulo='Chamados de manutenção', icone='🛠️', cor='azul',
         texto='Volume, tempo de resolução e custo dos chamados num período.'),
    dict(nome='relatorio_contratos', titulo='Contratos', icone='📄', cor='ciano',
         texto='Contratos vigentes, vencimentos, receita mensal de aluguel e unidades vagas.'),
    dict(nome='relatorio_correspondencias', titulo='Correspondências', icone='📬', cor='marinho',
         texto='Recebimentos e retiradas num período, e o que está parado na portaria.'),
]


def interpretar_data(texto_data, padrao):
    try:
        return datetime.strptime(texto_data or '', '%Y-%m-%d').date()
    except ValueError:
        return padrao


def periodo(request, dias=90):
    inicio_padrao, fim_padrao = dados.periodo_padrao(dias)
    de = interpretar_data(request.GET.get('de'), inicio_padrao)
    ate = interpretar_data(request.GET.get('ate'), fim_padrao)
    return (ate, de) if de > ate else (de, ate)


def competencia_escolhida(request):
    todas = list(Competencia.objects.order_by('-referencia'))
    escolhida = None
    pk = request.GET.get('competencia', '')
    if pk.isdigit():
        escolhida = next((c for c in todas if c.pk == int(pk)), None)
    return (escolhida or (todas[0] if todas else None)), todas


class Relatorio(SindicoRequiredMixin, View):
    """Cada relatório monta o contexto e, se pedido com ?formato=csv, as linhas do CSV."""
    template_name = None

    def contexto(self, request):
        raise NotImplementedError

    def csv(self, contexto):
        raise NotImplementedError

    def get(self, request):
        contexto = self.contexto(request)
        if request.GET.get('formato') == 'csv':
            return self.csv(contexto)
        contexto['relatorio'] = next(r for r in RELATORIOS if r['nome'] == request.resolver_match.url_name)
        return render(request, self.template_name, contexto)


class Indice(SindicoRequiredMixin, View):
    def get(self, request):
        return render(request, 'relatorios/indice.html', {'relatorios': RELATORIOS})


class Despesas(Relatorio):
    template_name = 'relatorios/despesas.html'

    def contexto(self, request):
        competencia, todas = competencia_escolhida(request)
        return dict(
            competencias=todas, escolhida=competencia, evolucao=dados.evolucao_despesas(),
            relatorio_dados=dados.despesas(competencia) if competencia else None,
        )

    def csv(self, contexto):
        base = contexto['relatorio_dados']
        if not base:
            return resposta_csv('despesas', ['Sem dados'], [])
        linhas = [
            [data(d.vencimento), texto(d.fornecedor), texto(d.descricao), texto(d.categoria), d.get_tipo_display(),
             numero(d.valor), numero(d.multa_juros), 'Sim' if d.entra_no_rateio else 'Não', texto(d.motivo_exclusao)]
            for d in base['linhas']
        ]
        referencia = base['competencia'].referencia
        return resposta_csv(
            f'despesas-{referencia:%Y-%m}',
            ['Vencimento', 'Fornecedor', 'Descrição', 'Categoria', 'Tipo', 'Valor', 'Multa e juros', 'Entra no rateio', 'Motivo da exclusão'],
            linhas)


class Rateio(Relatorio):
    template_name = 'relatorios/rateio.html'

    def contexto(self, request):
        competencia, todas = competencia_escolhida(request)
        return dict(competencias=todas, escolhida=competencia,
                    relatorio_dados=dados.rateio(competencia) if competencia else None)

    def csv(self, contexto):
        base = contexto['relatorio_dados']
        if not base:
            return resposta_csv('rateio', ['Sem rateio calculado'], [])
        linhas = [
            [texto(l['item'].unidade), texto(l['item'].locatario or 'Sem contrato (Play)'), numero(l['item'].area),
             numero(l['item'].energia), numero(l['item'].agua), numero(l['item'].demais), numero(l['item'].total),
             numero(l['percentual'])]
            for l in base['linhas']
        ]
        linhas.append(['Total', '', '', numero(base['rateio'].total_energia), numero(base['rateio'].total_agua),
                       numero(base['rateio'].total_demais), numero(base['total']), '100,00'])
        return resposta_csv(
            f"rateio-{base['competencia'].referencia:%Y-%m}",
            ['Unidade', 'Locatário', 'Área (m²)', 'Energia', 'Água', 'Demais', 'Total', '% do total'], linhas)


class Inadimplencia(Relatorio):
    template_name = 'relatorios/inadimplencia.html'

    def contexto(self, request):
        return dict(relatorio_dados=dados.inadimplencia(), resumo=dados.resumo_mensal_cobrancas())

    def csv(self, contexto):
        linhas = [
            [texto(c.locatario), texto(c.unidade), c.get_tipo_display(), f'{c.referencia:%m/%Y}', data(c.vencimento),
             numero(c.valor), c.dias_atraso]
            for c in contexto['relatorio_dados']['atrasadas']
        ]
        return resposta_csv('inadimplencia', ['Locatário', 'Unidade', 'Tipo', 'Referência', 'Vencimento', 'Valor', 'Dias de atraso'], linhas)


class Chamados(Relatorio):
    template_name = 'relatorios/chamados.html'

    def contexto(self, request):
        de, ate = periodo(request)
        return dict(de=de, ate=ate, relatorio_dados=dados.chamados(de, ate))

    def csv(self, contexto):
        linhas = [
            [c.numero, data_hora(c.aberto_em), texto(c.titulo), c.get_categoria_display(), c.get_prioridade_display(),
             c.get_status_display(), texto(c.onde), texto(c.responsavel or ''), texto(c.fornecedor),
             numero(c.custo), data_hora(c.resolvido_em)]
            for c in contexto['relatorio_dados']['lista']
        ]
        return resposta_csv(
            f"chamados-{contexto['de']:%Y%m%d}-{contexto['ate']:%Y%m%d}",
            ['Nº', 'Aberto em', 'Título', 'Categoria', 'Prioridade', 'Situação', 'Onde', 'Responsável', 'Fornecedor', 'Custo', 'Resolvido em'],
            linhas)


class Contratos(Relatorio):
    template_name = 'relatorios/contratos.html'

    def contexto(self, request):
        return dict(relatorio_dados=dados.contratos())

    def csv(self, contexto):
        linhas = [
            [texto(c.unidade), texto(c.locatario), data(c.data_inicio), data(c.data_fim), c.dias_para_vencer if c.dias_para_vencer is not None else '',
             numero(c.valor_aluguel), c.dia_vencimento, c.get_indice_reajuste_display(), c.mes_reajuste or '',
             numero(c.pct_energia) if c.pct_energia is not None else '', numero(c.pct_agua) if c.pct_agua is not None else '']
            for c in contexto['relatorio_dados']['vigentes']
        ]
        return resposta_csv('contratos-vigentes', [
            'Unidade', 'Locatário', 'Início', 'Fim', 'Dias para vencer', 'Aluguel', 'Dia do vencimento', 'Reajuste', 'Mês do reajuste',
            '% energia', '% água'], linhas)


class Correspondencias(Relatorio):
    template_name = 'relatorios/correspondencias.html'

    def contexto(self, request):
        de, ate = periodo(request, dias=30)
        return dict(de=de, ate=ate, relatorio_dados=dados.correspondencias(de, ate))

    def csv(self, contexto):
        linhas = [
            [c.numero, data_hora(c.recebido_em), texto(c.unidade), c.get_tipo_display(), texto(c.destinatario),
             c.get_status_display(), data_hora(c.retirada_em), texto(c.retirada_por), texto(c.baixa_obs)]
            for c in contexto['relatorio_dados']['lista']
        ]
        return resposta_csv(
            f"correspondencias-{contexto['de']:%Y%m%d}-{contexto['ate']:%Y%m%d}",
            ['Nº', 'Recebida em', 'Unidade', 'Tipo', 'Destinatário', 'Situação', 'Retirada ou devolvida em', 'Retirada por', 'Observação da baixa'],
            linhas)
