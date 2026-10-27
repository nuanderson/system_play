import os
import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from cadastros.models import Locatario, Unidade


class Chamado(models.Model):
    class Categoria(models.TextChoices):
        ELETRICA = 'eletrica', 'Elétrica'
        HIDRAULICA = 'hidraulica', 'Hidráulica'
        AR = 'ar', 'Ar-condicionado'
        ELEVADOR = 'elevador', 'Elevador'
        LIMPEZA = 'limpeza', 'Limpeza'
        ESTRUTURA = 'estrutura', 'Estrutura e pintura'
        SEGURANCA = 'seguranca', 'Segurança e acesso'
        TI = 'ti', 'Internet e telefonia'
        OUTROS = 'outros', 'Outros'

    class Prioridade(models.IntegerChoices):
        BAIXA = 1, 'Baixa'
        NORMAL = 2, 'Normal'
        ALTA = 3, 'Alta'
        URGENTE = 4, 'Urgente'

    class Status(models.TextChoices):
        ABERTO = 'aberto', 'Aberto'
        ANDAMENTO = 'andamento', 'Em andamento'
        AGUARDANDO = 'aguardando', 'Aguardando peça ou terceiro'
        RESOLVIDO = 'resolvido', 'Resolvido'
        CANCELADO = 'cancelado', 'Cancelado'

    STATUS_EM_ABERTO = (Status.ABERTO, Status.ANDAMENTO, Status.AGUARDANDO)

    locatario = models.ForeignKey(
        Locatario, verbose_name='empresa', null=True, blank=True, on_delete=models.SET_NULL, related_name='chamados',
        help_text='Em branco para áreas comuns do prédio.',
    )
    unidade = models.ForeignKey(
        Unidade, verbose_name='unidade', null=True, blank=True, on_delete=models.SET_NULL, related_name='chamados',
        help_text='Preenchida sozinha quando a empresa ocupa uma só unidade.',
    )
    local = models.CharField('local do problema', max_length=120, blank=True, help_text='Ex.: banheiro do 2º andar, portão da garagem.')
    solicitante = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='aberto por', null=True, on_delete=models.SET_NULL, related_name='chamados_abertos',
    )
    titulo = models.CharField('título', max_length=150)
    descricao = models.TextField('descrição')
    categoria = models.CharField('categoria', max_length=12, choices=Categoria.choices, default=Categoria.OUTROS)
    prioridade = models.PositiveSmallIntegerField('prioridade', choices=Prioridade.choices, default=Prioridade.NORMAL)
    status = models.CharField('situação', max_length=12, choices=Status.choices, default=Status.ABERTO)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='responsável', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='chamados_atribuidos',
    )
    fornecedor = models.CharField('fornecedor ou prestador', max_length=150, blank=True)
    custo = models.DecimalField('custo (R$)', max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    aberto_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)
    resolvido_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'chamado'
        verbose_name_plural = 'chamados'
        ordering = ['-aberto_em']

    def __str__(self):
        return f'{self.numero} {self.titulo}'

    @property
    def numero(self):
        return f'#{self.pk:04d}' if self.pk else '#—'

    @property
    def em_aberto(self):
        return self.status in self.STATUS_EM_ABERTO

    @property
    def onde(self):
        if self.locatario_id:
            quem = str(self.locatario)
        elif self.unidade_id:
            quem = str(self.unidade)
        else:
            quem = 'Área comum'
        partes = [quem, self.local]
        return ' · '.join(p for p in partes if p)

    @property
    def dias_em_aberto(self):
        fim = self.resolvido_em or timezone.now()
        return (fim - self.aberto_em).days

    @classmethod
    def abertos(cls):
        return cls.objects.filter(status__in=cls.STATUS_EM_ABERTO)


class Comentario(models.Model):
    class Tipo(models.TextChoices):
        COMENTARIO = 'comentario', 'Comentário'
        SISTEMA = 'sistema', 'Registro do sistema'

    chamado = models.ForeignKey(Chamado, on_delete=models.CASCADE, related_name='comentarios')
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    tipo = models.CharField(max_length=10, choices=Tipo.choices, default=Tipo.COMENTARIO)
    texto = models.TextField('comentário')
    interno = models.BooleanField(
        'nota interna', default=False, help_text='Só a equipe e o síndico veem. A empresa não.',
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['criado_em', 'id']

    def __str__(self):
        return self.texto[:50]


def caminho_anexo(instancia, nome_arquivo):
    extensao = os.path.splitext(nome_arquivo)[1].lower()
    return f'chamados/{instancia.chamado_id}/{uuid.uuid4().hex}{extensao}'


class Anexo(models.Model):
    chamado = models.ForeignKey(Chamado, on_delete=models.CASCADE, related_name='anexos')
    arquivo = models.FileField('arquivo', upload_to=caminho_anexo)
    nome_original = models.CharField(max_length=255, blank=True)
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    enviado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['enviado_em', 'id']

    @property
    def eh_imagem(self):
        return os.path.splitext(self.arquivo.name)[1].lower() in ('.jpg', '.jpeg', '.png', '.webp')


@receiver(post_delete, sender=Anexo)
def apagar_arquivo_do_anexo(sender, instance, **kwargs):
    if instance.arquivo:
        instance.arquivo.delete(save=False)


def registrar(chamado, autor, texto, interno=False):
    """Linha do histórico do chamado, escrita pelo sistema."""
    return Comentario.objects.create(
        chamado=chamado, autor=autor, tipo=Comentario.Tipo.SISTEMA, texto=texto, interno=interno)
