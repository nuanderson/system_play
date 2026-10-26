"""Ações que mudam o estado do financeiro: aprovar rateio e gerar cobranças."""

from django.db import transaction
from django.utils import timezone

from contratos.models import Contrato

from .calculo import ErroRateio
from .models import Cobranca, Competencia, data_no_mes, somar_meses

DIA_VENCIMENTO_PADRAO = 10


def _ja_existe(unidade, tipo, referencia):
    return Cobranca.objects.filter(unidade=unidade, tipo=tipo, referencia=referencia).exclude(
        status=Cobranca.Status.CANCELADA).exists()


@transaction.atomic
def aprovar_rateio(competencia, usuario):
    """
    Trava a competência e gera uma cobrança de condomínio por unidade com contrato vigente.
    O vencimento padrão é o dia do contrato no mês seguinte ao das despesas; dá para ajustar depois.
    Unidades sem contrato não geram cobrança: o custo fica com a Play.
    """
    if competencia.status != Competencia.Status.CALCULADA:
        raise ErroRateio('Calcule o rateio antes de aprovar.')
    rateio = competencia.rateio
    mes_cobranca = somar_meses(competencia.referencia, 1)
    contratos = {c.unidade_id: c for c in Contrato.vigentes()}
    criadas, ja_existiam, sem_contrato = 0, 0, []
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
        Cobranca.objects.create(
            unidade=item.unidade, locatario=item.locatario, tipo=Cobranca.Tipo.CONDOMINIO,
            referencia=competencia.referencia, valor=item.total, rateio=rateio,
            vencimento=data_no_mes(mes_cobranca.year, mes_cobranca.month, dia),
        )
        criadas += 1
    rateio.aprovado_em = timezone.now()
    rateio.aprovado_por = usuario
    rateio.save(update_fields=['aprovado_em', 'aprovado_por'])
    competencia.status = Competencia.Status.APROVADA
    competencia.save(update_fields=['status'])
    return dict(criadas=criadas, ja_existiam=ja_existiam, sem_contrato=sem_contrato)


@transaction.atomic
def gerar_alugueis(referencia):
    """Uma cobrança de aluguel por contrato vigente no mês. Não calcula proporcional de mês parcial."""
    primeiro = referencia
    ultimo = data_no_mes(referencia.year, referencia.month, 31)
    contratos = Contrato.vigentes().select_related('unidade', 'locatario').filter(data_inicio__lte=ultimo)
    criadas, ja_existiam = 0, 0
    for contrato in contratos:
        if contrato.data_fim and contrato.data_fim < primeiro:
            continue
        if _ja_existe(contrato.unidade, Cobranca.Tipo.ALUGUEL, referencia):
            ja_existiam += 1
            continue
        Cobranca.objects.create(
            unidade=contrato.unidade, locatario=contrato.locatario, tipo=Cobranca.Tipo.ALUGUEL,
            referencia=referencia, valor=contrato.valor_aluguel,
            vencimento=data_no_mes(referencia.year, referencia.month, contrato.dia_vencimento),
        )
        criadas += 1
    return dict(criadas=criadas, ja_existiam=ja_existiam)
