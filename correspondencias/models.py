import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from cadastros.models import Locatario, Unidade

DIAS_ALERTA = 7  # correspondência parada há tantos dias ou mais ganha destaque


def caminho_foto(instancia, nome_arquivo):
    extensao = os.path.splitext(nome_arquivo)[1].lower()
    return f'correspondencias/{uuid.uuid4().hex}{extensao}'


class Correspondencia(models.Model):
    class Tipo(models.TextChoices):
        CARTA = 'carta', 'Carta'
        ENCOMENDA = 'encomenda', 'Encomenda'
        DOCUMENTO = 'documento', 'Documento ou malote'
        NOTIFICACAO = 'notificacao', 'Notificação ou intimação'
        OUTRO = 'outro', 'Outro'

    class Status(models.TextChoices):
        AGUARDANDO = 'aguardando', 'Aguardando retirada'
        RETIRADA = 'retirada', 'Retirada'
        DEVOLVIDA = 'devolvida', 'Devolvida ao remetente'

    locatario = models.ForeignKey(
        Locatario, verbose_name='destinatário (empresa)', null=True, blank=True, on_delete=models.PROTECT,
        related_name='correspondencias', help_text='Para qual empresa chegou.',
    )
    unidade = models.ForeignKey(
        Unidade, verbose_name='unidade', null=True, blank=True, on_delete=models.PROTECT, related_name='correspondencias',
        help_text='Preenchida sozinha quando a empresa ocupa uma só unidade.',
    )
    destinatario = models.CharField('aos cuidados de', max_length=150, blank=True, help_text='Nome que está no envelope, se houver.')
    tipo = models.CharField('tipo', max_length=12, choices=Tipo.choices, default=Tipo.ENCOMENDA)
    observacoes = models.CharField('observações', max_length=300, blank=True)
    foto = models.FileField('foto', upload_to=caminho_foto, blank=True)
    status = models.CharField('situação', max_length=12, choices=Status.choices, default=Status.AGUARDANDO)
    recebido_em = models.DateTimeField('recebido em', default=timezone.now)
    registrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='correspondencias_registradas')
    retirada_em = models.DateTimeField('retirada em', null=True, blank=True)
    retirada_por = models.CharField('retirada por', max_length=150, blank=True)
    baixa_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='baixas_de_correspondencia')
    baixa_obs = models.CharField('observação da baixa', max_length=300, blank=True)

    class Meta:
        verbose_name = 'correspondência'
        verbose_name_plural = 'correspondências'
        ordering = ['-recebido_em', '-id']

    def __str__(self):
        return f'{self.numero} {self.get_tipo_display()} — {self.destino}'

    @property
    def numero(self):
        return f'C{self.pk:04d}' if self.pk else 'C—'

    @property
    def destino(self):
        if self.locatario_id:
            return str(self.locatario)
        return str(self.unidade) if self.unidade_id else '—'

    @property
    def aguardando(self):
        return self.status == self.Status.AGUARDANDO

    @property
    def dias_aguardando(self):
        fim = self.retirada_em if (self.retirada_em and not self.aguardando) else timezone.now()
        return max((fim - self.recebido_em).days, 0)

    @property
    def parada(self):
        return self.aguardando and self.dias_aguardando >= DIAS_ALERTA

    @property
    def tem_foto(self):
        return bool(self.foto)

    @classmethod
    def pendentes(cls):
        return cls.objects.filter(status=cls.Status.AGUARDANDO)

    @classmethod
    def paradas(cls):
        return cls.pendentes().filter(recebido_em__lte=timezone.now() - timedelta(days=DIAS_ALERTA))


@receiver(post_delete, sender=Correspondencia)
def apagar_foto(sender, instance, **kwargs):
    if instance.foto:
        instance.foto.delete(save=False)
