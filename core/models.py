from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from .acesso import tem_permissao


class UsuarioManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        # Quem é criado pelo createsuperuser já nasce como síndico.
        extra_fields.setdefault('perfil', 'sindico')
        return super().create_superuser(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    """Usuário do sistema. O perfil define o que ele pode fazer (ver core/acesso.py)."""

    class Perfil(models.TextChoices):
        SINDICO = 'sindico', 'Síndico'
        ADMINISTRATIVO = 'administrativo', 'Administrativo'
        EQUIPE = 'equipe', 'Equipe e portaria'
        CONSULTA = 'consulta', 'Consulta (só leitura)'
        LOCATARIO = 'locatario', 'Responsável da unidade'
        MEMBRO = 'membro', 'Membro da unidade'

    PERFIS_DA_UNIDADE = (Perfil.LOCATARIO, Perfil.MEMBRO)

    perfil = models.CharField('perfil', max_length=20, choices=Perfil.choices, default=Perfil.MEMBRO)
    unidade = models.ForeignKey(
        'cadastros.Unidade', verbose_name='unidade', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='usuarios',
    )
    telefone = models.CharField('telefone', max_length=30, blank=True)
    trocar_senha = models.BooleanField(
        'precisa trocar a senha', default=False,
        help_text='Ligado quando a senha é provisória. O usuário só segue depois de escolher a dele.',
    )

    objects = UsuarioManager()

    class Meta:
        verbose_name = 'usuário'
        verbose_name_plural = 'usuários'

    def pode(self, *permissoes):
        return self.is_active and tem_permissao(self.perfil, *permissoes)

    @property
    def e_sindico(self):
        return self.perfil == self.Perfil.SINDICO

    def __str__(self):
        return self.get_full_name() or self.username
