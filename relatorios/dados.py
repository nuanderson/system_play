"""Cálculo dos relatórios. Funções sem tela, para poder testar os números."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from cadastros.models import Unidade
from chamados.models import Chamado
from contratos.models import DIAS_ALERTA_VENCIMENTO, Contrato
from correspondencias.models import DIAS_ALERTA, Correspondencia
from financeiro.models import Cobranca, Competencia, Despesa

ZERO = Decimal('0.00')
FAIXAS_ATRASO = [(30, '1 a 30 dias'), (60, '31 a 60 dias'), (90, '61 a 90 dias'), (None, 'Mais de 90 dias')]


def somar(valores):
    return sum(valores, ZERO)


def percentual(parte, total):
    return (Decimal(parte) * 100 / Decimal(total)) if total else ZERO


def media(valores, casas=1):
    valores = list(valores)
    return round(sum(valores) / len(valores), casas) if valores else None


def periodo_padrao(dias=90):
    hoje = timezone.localdate()
    return hoje - timedelta(days=dias), hoje


# ---- Despesas ---------------------------------------------------------------

def despesas(competencia):
    linhas = list(competencia.despesas.all())
    incluidas = [d for d in linhas if d.entra_no_rateio]
    excluidas = [d for d in linhas if not d.entra_no_rateio]
    total = somar(d.valor for d in incluidas)
    por_tipo = {tipo: somar(d.valor for d in incluidas if d.tipo == tipo) for tipo in Despesa.Tipo.values}
    soma_categoria = defaultdict(lambda: ZERO)
    for d in incluidas:
        soma_categoria[d.categoria or 'Sem categoria'] += d.valor
    categorias = sorted(
        ((nome, valor, percentual(valor, total)) for nome, valor in soma_categoria.items()),
        key=lambda item: item[1], reverse=True)
    return dict(
        competencia=competencia, linhas=linhas, total=total, por_tipo=por_tipo, categorias=categorias,
        total_excluidas=somar(d.valor for d in excluidas), multas=somar(d.multa_juros for d in linhas),
    )


def evolucao_despesas(quantidade=12):
    competencias = list(Competencia.objects.order_by('-referencia')[:quantidade])[::-1]
    resultado = []
    for competencia in competencias:
        por_tipo = competencia.total_por_tipo()
        energia = por_tipo[Despesa.Tipo.ENERGIA] or ZERO
        agua = por_tipo[Despesa.Tipo.AGUA] or ZERO
        demais = por_tipo[Despesa.Tipo.DEMAIS] or ZERO
        resultado.append(dict(competencia=competencia, energia=energia, agua=agua, demais=demais,
                              total=energia + agua + demais))
    maior = max((r['total'] for r in resultado), default=ZERO)
    for r in resultado:
        r['barra'] = int(percentual(r['total'], maior)) if maior else 0
    return resultado


# ---- Rateio -----------------------------------------------------------------

def rateio(competencia):
    registro = getattr(competencia, 'rateio', None)
    if registro is None:
        return None
    itens = list(registro.itens.select_related('unidade', 'locatario'))
    total = registro.total
    linhas = [dict(item=i, percentual=percentual(i.total, total)) for i in itens]
    sem_contrato = somar(i.total for i in itens if i.locatario_id is None)
    return dict(competencia=competencia, rateio=registro, linhas=linhas, total=total, sem_contrato=sem_contrato)


# ---- Inadimplência e cobranças ----------------------------------------------

def inadimplencia():
    atrasadas = list(Cobranca.atrasadas().select_related('unidade', 'locatario').order_by('vencimento'))
    por_locatario = {}
    faixas = {nome: dict(total=ZERO, quantidade=0) for _, nome in FAIXAS_ATRASO}
    for c in atrasadas:
        dados = por_locatario.setdefault(c.locatario_id, dict(
            locatario=c.locatario, unidades=set(), total=ZERO, quantidade=0, maior_atraso=0))
        dados['unidades'].add(c.unidade.nome)
        dados['total'] += c.valor
        dados['quantidade'] += 1
        dados['maior_atraso'] = max(dados['maior_atraso'], c.dias_atraso)
        for limite, nome in FAIXAS_ATRASO:
            if limite is None or c.dias_atraso <= limite:
                faixas[nome]['total'] += c.valor
                faixas[nome]['quantidade'] += 1
                break
    devedores = sorted(por_locatario.values(), key=lambda d: d['total'], reverse=True)
    for d in devedores:
        d['unidades'] = ', '.join(sorted(d['unidades']))
    total = somar(c.valor for c in atrasadas)
    return dict(atrasadas=atrasadas, devedores=devedores, faixas=faixas, total=total,
                a_vencer=somar(c.valor for c in Cobranca.em_aberto().filter(vencimento__gte=timezone.localdate())))


def resumo_mensal_cobrancas(meses=12):
    hoje = timezone.localdate()
    primeiro = date(hoje.year, hoje.month, 1)
    inicio = date((primeiro.year * 12 + primeiro.month - 1 - (meses - 1)) // 12,
                  (primeiro.year * 12 + primeiro.month - 1 - (meses - 1)) % 12 + 1, 1)
    por_mes = {}
    for c in Cobranca.objects.exclude(status=Cobranca.Status.CANCELADA).filter(referencia__gte=inicio):
        linha = por_mes.setdefault(c.referencia, dict(referencia=c.referencia, emitido=ZERO, pago=ZERO, aberto=ZERO, atrasado=ZERO))
        linha['emitido'] += c.valor
        if c.status == Cobranca.Status.PAGA:
            linha['pago'] += c.valor_pago if c.valor_pago is not None else c.valor
        else:
            linha['aberto'] += c.valor
            if c.atrasada:
                linha['atrasado'] += c.valor
    return [por_mes[k] for k in sorted(por_mes)]


# ---- Chamados ---------------------------------------------------------------

def chamados(de, ate):
    lista = list(Chamado.objects.select_related('locatario', 'unidade', 'responsavel').filter(
        aberto_em__date__gte=de, aberto_em__date__lte=ate).order_by('-aberto_em'))
    por_status = {valor: sum(1 for c in lista if c.status == valor) for valor in Chamado.Status.values}
    por_categoria, por_prioridade, por_local = defaultdict(int), defaultdict(int), defaultdict(int)
    custo_categoria = defaultdict(lambda: ZERO)
    for c in lista:
        por_categoria[c.get_categoria_display()] += 1
        por_prioridade[c.get_prioridade_display()] += 1
        por_local[str(c.locatario) if c.locatario_id else (str(c.unidade) if c.unidade_id else 'Área comum')] += 1
        if c.custo:
            custo_categoria[c.get_categoria_display()] += c.custo
    resolvidos = [c for c in lista if c.resolvido_em]
    dias = [(c.resolvido_em - c.aberto_em).total_seconds() / 86400 for c in resolvidos]
    ordenar = lambda d: sorted(d.items(), key=lambda item: item[1], reverse=True)
    return dict(
        de=de, ate=ate, lista=lista, total=len(lista), por_status=por_status,
        por_categoria=ordenar(por_categoria), por_prioridade=ordenar(por_prioridade), por_local=ordenar(por_local),
        em_aberto=sum(1 for c in lista if c.em_aberto), resolvidos=len(resolvidos),
        tempo_medio_dias=media(dias), custo_total=somar(c.custo for c in lista if c.custo),
        custo_categoria=ordenar(custo_categoria),
    )


# ---- Contratos --------------------------------------------------------------

def contratos():
    vigentes = list(Contrato.vigentes().select_related('unidade', 'locatario').order_by('data_fim', 'unidade__ordem'))
    for c in vigentes:
        c.estado = c.situacao_vencimento
    com_contrato = {c.unidade_id for c in vigentes}
    vagas = [u for u in Unidade.objects.filter(ativa=True) if u.pk not in com_contrato]
    return dict(
        vigentes=vigentes, vagas=vagas, receita_mensal=somar(c.valor_aluguel for c in vigentes),
        a_vencer=[c for c in vigentes if c.estado == 'a_vencer'], vencidos=[c for c in vigentes if c.estado == 'vencido'],
        em_negociacao=list(Contrato.objects.filter(status=Contrato.Status.NEGOCIACAO).select_related('unidade', 'locatario')),
        dias_alerta=DIAS_ALERTA_VENCIMENTO,
    )


# ---- Correspondências -------------------------------------------------------

def correspondencias(de, ate):
    lista = list(Correspondencia.objects.select_related('locatario', 'unidade').filter(
        recebido_em__date__gte=de, recebido_em__date__lte=ate).order_by('-recebido_em'))
    por_tipo, por_unidade = defaultdict(int), defaultdict(int)
    for c in lista:
        por_tipo[c.get_tipo_display()] += 1
        por_unidade[c.destino] += 1
    retiradas = [c for c in lista if c.status == Correspondencia.Status.RETIRADA and c.retirada_em]
    dias = [(c.retirada_em - c.recebido_em).total_seconds() / 86400 for c in retiradas]
    ordenar = lambda d: sorted(d.items(), key=lambda item: item[1], reverse=True)
    return dict(
        de=de, ate=ate, lista=lista, total=len(lista), retiradas=len(retiradas),
        devolvidas=sum(1 for c in lista if c.status == Correspondencia.Status.DEVOLVIDA),
        tempo_medio_dias=media(dias), por_tipo=ordenar(por_tipo), por_unidade=ordenar(por_unidade),
        pendentes_agora=Correspondencia.pendentes().select_related('locatario', 'unidade').order_by('recebido_em'),
        dias_alerta=DIAS_ALERTA,
    )
