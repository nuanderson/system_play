"""CSV para abrir direto no Excel em português: separador ponto e vírgula, vírgula decimal, acentos corretos."""

import csv
from decimal import Decimal

from django.http import HttpResponse

PERIGOSOS = ('=', '+', '-', '@', '\t', '\r')


def numero(valor, casas=2):
    if valor is None:
        return ''
    return f'{Decimal(valor):.{casas}f}'.replace('.', ',')


def data(valor):
    return valor.strftime('%d/%m/%Y') if valor else ''


def data_hora(valor):
    if not valor:
        return ''
    from django.utils import timezone
    return timezone.localtime(valor).strftime('%d/%m/%Y %H:%M')


def texto(valor):
    """Texto digitado por pessoas. Evita que o Excel trate como fórmula (=, +, -, @)."""
    valor = '' if valor is None else str(valor)
    return "'" + valor if valor.startswith(PERIGOSOS) else valor


def resposta_csv(nome_arquivo, cabecalho, linhas):
    resposta = HttpResponse(content_type='text/csv; charset=utf-8')
    resposta['Content-Disposition'] = f'attachment; filename="{nome_arquivo}.csv"'
    resposta.write('﻿')
    escritor = csv.writer(resposta, delimiter=';')
    escritor.writerow(cabecalho)
    for linha in linhas:
        escritor.writerow(linha)
    return resposta
