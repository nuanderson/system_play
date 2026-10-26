from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models


class UsuarioManager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        # Quem é criado pelo createsuperuser já nasce como síndico.
        extra_fields.setdefault('perfil', 'sindico')
        return super().create_superuser(username, email, password, **extra_fields)


class Usuario(AbstractUser):
    """Usuário do sistema. O perfil define o que ele pode fazer."""

    class Perfil(models.TextChoices):
        SINDICO = 'sindico', 'Síndico'
        EQUIPE = 'equipe', 'Equipe do prédio'
        LOCATARIO = 'locatario', 'Responsável da unidade'
        MEMBRO = 'membro', 'Membro da unidade'

    perfil = models.CharField('perfil', max_length=20, choices=Perfil.choices, default=Perfil.MEMBRO)
    unidade = models.ForeignKey(
        'cadastros.Unidade', verbose_name='unidade', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='usuarios',
    )
    telefone = models.CharField('telefone', max_length=30, blank=True)

    objects = UsuarioManager()

    class Meta:
        verbose_name = 'usuário'
        verbose_name_plural = 'usuários'

    @property
    def e_sindico(self):
        return self.perfil == self.Perfil.SINDICO

    @property
    def e_equipe(self):
        return self.perfil in (self.Perfil.SINDICO, self.Perfil.EQUIPE)

    def __str__(self):
        return self.get_full_name() or self.username
