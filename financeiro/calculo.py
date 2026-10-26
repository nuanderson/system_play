"""Motor do rateio. Contas em Decimal, sem IA, sempre fechando no centavo.

Regras (ver PLANO-DE-RATEIO.md):
- Energia e água: quem tem porcentagem fixa no contrato paga essa % da conta. O restante da conta
  é dividido, por área, entre as unidades que não têm % fixa naquele item.
- Demais despesas: divididas entre todas as unidades, por área.
"""

from decimal import ROUND_HALF_UP, Decimal

from cadastros.models import Unidade
from contratos.models import Contrato

from .models import Competencia, Rateio, RateioItem

CENTAVO = Decimal('0.01')
CEM = Decimal('100')


class ErroRateio(Exception):
    pass


def arredondar(valor):
    return Decimal(valor).quantize(CENTAVO, ROUND_HALF_UP)


def dividir(total, pesos):
    """Divide `total` proporcionalmente aos pesos, em centavos. A soma das partes é exatamente `total`."""
    total = arredondar(total)
    soma = sum(pesos.values())
    if not pesos or soma == 0:
        raise ErroRateio('Não há unidades com área para dividir o valor.')
    partes = {chave: arredondar(total * peso / soma) for chave, peso in pesos.items()}
    diferenca = total - sum(partes.values())
    if diferenca:
        maior = max(pesos, key=lambda chave: pesos[chave])
        partes[maior] += diferenca
    return partes


def ratear_componente(total, unidades, campo_pct, nome):
    """Energia ou água. `unidades`: dict id -> dict(area, pct_energia, pct_agua)."""
    total = arredondar(total)
    fixas = {i: u[campo_pct] for i, u in unidades.items() if u[campo_pct] is not None}
    if sum(fixas.values()) > CEM:
        raise ErroRateio(f'As porcentagens fixas de {nome} somam mais de 100%.')
    resultado = {i: Decimal('0.00') for i in unidades}
    for i, pct in fixas.items():
        resultado[i] = arredondar(total * pct / CEM)
    restante = total - sum(resultado[i] for i in fixas)
    if restante:
        pesos = {i: u['area'] for i, u in unidades.items() if i not in fixas}
        for i, parte in dividir(restante, pesos).items():
            resultado[i] += parte
    return resultado


def calcular(totais, unidades):
    """
    totais: dict com 'energia', 'agua', 'demais' (valores das despesas que entram no rateio).
    unidades: dict id -> dict(area, pct_energia, pct_agua).
    Retorna dict id -> dict(energia, agua, demais).
    """
    if not unidades:
        raise ErroRateio('Nenhuma unidade participa do rateio.')
    energia = ratear_componente(totais['energia'], unidades, 'pct_energia', 'energia')
    agua = ratear_componente(totais['agua'], unidades, 'pct_agua', 'água')
    demais = dividir(totais['demais'], {i: u['area'] for i, u in unidades.items()}) if totais['demais'] else \
        {i: Decimal('0.00') for i in unidades}
    return {i: dict(energia=energia[i], agua=agua[i], demais=demais[i]) for i in unidades}


def unidades_do_rateio():
    """Unidades que participam e o contrato vigente de cada uma (se houver)."""
    contratos = {c.unidade_id: c for c in Contrato.vigentes().select_related('locatario')}
    unidades = {}
    for unidade in Unidade.objects.filter(ativa=True, entra_no_rateio_por_area=True, area_rateio__isnull=False):
        contrato = contratos.get(unidade.pk)
        unidades[unidade.pk] = dict(
            unidade=unidade,
            contrato=contrato,
            area=unidade.area_rateio,
            pct_energia=contrato.pct_energia if contrato else None,
            pct_agua=contrato.pct_agua if contrato else None,
        )
    return unidades


def calcular_rateio(competencia):
    """Calcula e grava o rateio da competência (substitui um cálculo anterior ainda não aprovado)."""
    if competencia.status == Competencia.Status.APROVADA:
        raise ErroRateio('Esta competência já foi aprovada.')
    totais = competencia.total_por_tipo()
    if not any(totais.values()):
        raise ErroRateio('Nenhuma despesa marcada para entrar no rateio.')
    dados = unidades_do_rateio()
    resultado = calcular(totais, dados)

    Rateio.objects.filter(competencia=competencia).delete()
    rateio = Rateio.objects.create(
        competencia=competencia,
        total_energia=totais['energia'], total_agua=totais['agua'], total_demais=totais['demais'],
    )
    RateioItem.objects.bulk_create([
        RateioItem(
            rateio=rateio, unidade=dados[i]['unidade'],
            locatario=dados[i]['contrato'].locatario if dados[i]['contrato'] else None,
            area=dados[i]['area'], pct_energia=dados[i]['pct_energia'], pct_agua=dados[i]['pct_agua'],
            **valores,
        )
        for i, valores in resultado.items()
    ])
    soma = sum(v['energia'] + v['agua'] + v['demais'] for v in resultado.values())
    if soma != rateio.total:
        raise ErroRateio(f'O rateio não fecha: soma {soma} e despesas {rateio.total}.')
    competencia.status = Competencia.Status.CALCULADA
    competencia.save(update_fields=['status'])
    return rateio
