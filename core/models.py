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
    locatario = models.ForeignKey(
        'cadastros.Locatario', verbose_name='empresa (locatário)', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='usuarios',
        help_text='A empresa do usuário. Ele enxerga todas as unidades que ela ocupa.',
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

    def unidades_ids(self):
        """
        Unidades que o responsável ou o membro enxerga.
        Com uma unidade marcada, só ela. Sem unidade, todas as unidades ativas que a empresa ocupa.
        """
        if self.unidade_id:
            return [self.unidade_id]
        if self.locatario_id:
            from cadastros.models import Unidade
            return list(Unidade.objects.filter(locatario_id=self.locatario_id, ativa=True).values_list('pk', flat=True))
        return []

    def unidades(self):
        from cadastros.models import Unidade
        return Unidade.objects.filter(pk__in=self.unidades_ids())

    @property
    def tem_escopo(self):
        """Está ligado a uma empresa ou a uma unidade (mesmo que a empresa ainda não ocupe nenhuma)."""
        return bool(self.unidade_id or self.locatario_id)

    @property
    def e_sindico(self):
        return self.perfil == self.Perfil.SINDICO

    def __str__(self):
        return self.get_full_name() or self.username
