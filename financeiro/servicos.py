"""Ações que mudam o estado do financeiro: aprovar rateio e gerar cobranças."""

from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from contratos.models import Contrato

from .calculo import ErroRateio
from .models import Cobranca, CobrancaItem, Competencia, data_no_mes, somar_meses

DIA_VENCIMENTO_PADRAO = 10


def _ja_existe(unidade, tipo, referencia):
    """A unidade já está numa cobrança ativa do mês, sozinha ou somada com as outras da empresa."""
    ativas = Cobranca.objects.filter(tipo=tipo, referencia=referencia).exclude(status=Cobranca.Status.CANCELADA)
    return ativas.filter(Q(unidade=unidade) | Q(itens__unidade=unidade)).exists()


def _linhas(contrato, unidade, tipo, referencia, descricao, base):
    """O valor da unidade e, abaixo dele, os acréscimos e descontos do contrato que valem no mês."""
    linhas = [(unidade, descricao, base)]
    if contrato is not None:
        for ajuste in contrato.ajustes.all():
            if ajuste.aplica_em == tipo and ajuste.vale_em(referencia):
                linhas.append((unidade, ajuste.descricao, ajuste.calcular(base)))
    return linhas


def _emitir(pedidos, tipo, referencia, rateio=None):
    """
    Cria as cobranças. Cada pedido é uma unidade com as suas linhas e o seu vencimento.
    Empresa com mais de uma unidade (e com a opção ligada) recebe uma cobrança só, com o vencimento mais cedo.
    Cobrança que ficaria zerada ou negativa não é criada.
    """
    por_empresa = defaultdict(list)
    for pedido in pedidos:
        por_empresa[pedido['empresa']].append(pedido)
    criadas = 0
    for empresa, da_empresa in por_empresa.items():
        ja_tem_unificada = Cobranca.objects.filter(
            locatario=empresa, tipo=tipo, referencia=referencia, unidade__isnull=True,
        ).exclude(status=Cobranca.Status.CANCELADA).exists()
        unificar = empresa.cobranca_unificada and len(da_empresa) > 1 and not ja_tem_unificada
        grupos = [da_empresa] if unificar else [[p] for p in da_empresa]
        for grupo in grupos:
            linhas = [linha for p in grupo for linha in p['linhas']]
            total = sum(valor for _, _, valor in linhas)
            if total <= 0:
                continue
            cobranca = Cobranca.objects.create(
                unidade=grupo[0]['unidade'] if len(grupo) == 1 else None, locatario=empresa, tipo=tipo,
                referencia=referencia, valor=total, rateio=rateio, vencimento=min(p['vencimento'] for p in grupo),
            )
            CobrancaItem.objects.bulk_create([
                CobrancaItem(cobranca=cobranca, unidade=unidade, descricao=descricao, valor=valor)
                for unidade, descricao, valor in linhas
            ])
            criadas += 1
    return criadas


@transaction.atomic
def aprovar_rateio(competencia, usuario):
    """
    Trava a competência e gera as cobranças de condomínio das unidades com contrato vigente.
    O vencimento padrão é o dia do contrato no mês seguinte ao das despesas; dá para ajustar depois.
    Unidades sem contrato não geram cobrança: o custo fica com a Play.
    """
    if competencia.status != Competencia.Status.CALCULADA:
        raise ErroRateio('Calcule o rateio antes de aprovar.')
    rateio = competencia.rateio
    mes_cobranca = somar_meses(competencia.referencia, 1)
    contratos = {c.unidade_id: c for c in Contrato.vigentes().prefetch_related('ajustes')}
    pedidos, ja_existiam, sem_contrato = [], 0, []
    for item in rateio.itens.select_related('unidade', 'locatario'):
        if item.locatario_id is None:
            sem_contrato.append(item)
            continue
        if item.total <= 0:
            continue
        if _ja_existe(item.unidade, Cobranca.Tipo.CONDOMINIO, competencia.referencia):
            ja_existiam += 1
            continue
        contrato = contratos.get(item.unidade_id)
        dia = contrato.dia_vencimento if contrato else DIA_VENCIMENTO_PADRAO
        pedidos.append(dict(
            empresa=item.locatario, unidade=item.unidade,
            vencimento=data_no_mes(mes_cobranca.year, mes_cobranca.month, dia),
            linhas=_linhas(contrato, item.unidade, Cobranca.Tipo.CONDOMINIO, competencia.referencia,
                           'Condomínio', item.total),
        ))
    criadas = _emitir(pedidos, Cobranca.Tipo.CONDOMINIO, competencia.referencia, rateio)
    rateio.aprovado_em = timezone.now()
    rateio.aprovado_por = usuario
    rateio.save(update_fields=['aprovado_em', 'aprovado_por'])
    competencia.status = Competencia.Status.APROVADA
    competencia.save(update_fields=['status'])
    return dict(criadas=criadas, ja_existiam=ja_existiam, sem_contrato=sem_contrato)


@transaction.atomic
def gerar_alugueis(referencia):
    """Os alugueis do mês, um por contrato vigente. Não calcula proporcional de mês parcial."""
    primeiro = referencia
    ultimo = data_no_mes(referencia.year, referencia.month, 31)
    contratos = (Contrato.vigentes().select_related('unidade', 'locatario').prefetch_related('ajustes')
                 .filter(data_inicio__lte=ultimo))
    pedidos, ja_existiam = [], 0
    for contrato in contratos:
        if contrato.data_fim and contrato.data_fim < primeiro:
            continue
        if _ja_existe(contrato.unidade, Cobranca.Tipo.ALUGUEL, referencia):
            ja_existiam += 1
            continue
        pedidos.append(dict(
            empresa=contrato.locatario, unidade=contrato.unidade,
            vencimento=data_no_mes(referencia.year, referencia.month, contrato.dia_vencimento),
            linhas=_linhas(contrato, contrato.unidade, Cobranca.Tipo.ALUGUEL, referencia,
                           'Aluguel', contrato.valor_aluguel),
        ))
    criadas = _emitir(pedidos, Cobranca.Tipo.ALUGUEL, referencia)
    return dict(criadas=criadas, ja_existiam=ja_existiam)
