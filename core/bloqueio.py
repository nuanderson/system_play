"""Trava de tentativas de login: erros seguidos para o mesmo usuário bloqueiam o acesso por um tempo.

Vale também para nomes que não existem, para não revelar quais usuários estão cadastrados.
Durante o bloqueio nem a senha certa entra, e novas tentativas não prolongam o tempo.
"""

import math
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth import forms as auth_forms
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.exceptions import ValidationError
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from .models import BloqueioLogin


def chave(username):
    return (username or '').strip().lower()[:150]


def _janela():
    return timedelta(minutes=settings.LOGIN_BLOQUEIO_MINUTOS)


def bloqueado_ate(username):
    """Até quando o usuário está bloqueado, ou None se pode tentar."""
    ate = BloqueioLogin.objects.filter(username=chave(username)).values_list('bloqueado_ate', flat=True).first()
    return ate if ate and ate > timezone.now() else None


def registrar_falha(username):
    agora = timezone.now()
    with transaction.atomic():
        registro, _ = BloqueioLogin.objects.select_for_update().get_or_create(username=chave(username))
        if registro.bloqueado_ate and registro.bloqueado_ate > agora:
            return registro
        if registro.ultima_falha is None or agora - registro.ultima_falha > _janela():
            registro.falhas = 0
        registro.falhas += 1
        registro.ultima_falha = agora
        if registro.falhas >= settings.LOGIN_MAX_FALHAS:
            registro.bloqueado_ate = agora + _janela()
            registro.falhas = 0
        registro.save()
    return registro


def limpar(username):
    BloqueioLogin.objects.filter(username=chave(username)).delete()


def bloqueados_agora():
    """{usuário: até quando}, para a tela do síndico."""
    return dict(BloqueioLogin.objects.filter(bloqueado_ate__gt=timezone.now()).values_list('username', 'bloqueado_ate'))


class TravaDeLoginMixin:
    """Recusa o login de quem está bloqueado, antes de olhar a senha."""

    def clean(self):
        ate = bloqueado_ate(self.cleaned_data.get('username'))
        if ate:
            minutos = max(1, math.ceil((ate - timezone.now()).total_seconds() / 60))
            raise ValidationError(
                f'Muitas tentativas erradas. Tente de novo em {minutos} minuto{"s" if minutos > 1 else ""}.',
                code='bloqueado')
        return super().clean()


class LoginForm(TravaDeLoginMixin, auth_forms.AuthenticationForm):
    pass


class LoginAdminForm(TravaDeLoginMixin, AdminAuthenticationForm):
    pass


@receiver(user_login_failed)
def _falhou(sender, credentials, **kwargs):
    registrar_falha(credentials.get('username'))


@receiver(user_logged_in)
def _entrou(sender, user, **kwargs):
    limpar(user.get_username())
