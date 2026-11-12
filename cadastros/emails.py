"""Vários e-mails num campo só: separados por vírgula, ponto e vírgula ou espaço."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

LIMITE_DE_EMAILS = 10


def separar_emails(texto):
    """Lista de endereços, sem repetir (maiúsculas não contam) e na ordem em que foram escritos."""
    vistos, lista = set(), []
    for email in re.split(r'[,;\s]+', texto or ''):
        if email and email.lower() not in vistos:
            vistos.add(email.lower())
            lista.append(email)
    return lista


def validar_lista_de_emails(texto, obrigatorio=False):
    """Devolve a lista limpa ou levanta ValidationError dizendo qual endereço está errado."""
    lista = separar_emails(texto)
    if obrigatorio and not lista:
        raise ValidationError('Informe pelo menos um e-mail.')
    if len(lista) > LIMITE_DE_EMAILS:
        raise ValidationError(f'No máximo {LIMITE_DE_EMAILS} e-mails.')
    for email in lista:
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError(f'"{email}" não parece um e-mail válido.') from None
    return lista
