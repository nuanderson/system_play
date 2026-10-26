from django.db import models


class Locatario(models.Model):
    """Empresa que ocupa uma ou mais unidades."""

    razao_social = models.CharField('razão social', max_length=200)
    cnpj = models.CharField('CNPJ', max_length=18, blank=True)
    contato = models.CharField('contato', max_length=120, blank=True)
    email_cobranca = models.EmailField('e-mail de cobrança', blank=True)
    telefone = models.CharField('telefone', max_length=30, blank=True)
    ativo = models.BooleanField('ativo', default=True)

    class Meta:
        verbose_name = 'locatário'
        verbose_name_plural = 'locatários'
        ordering = ['razao_social']

    def __str__(self):
        return self.razao_social


class Unidade(models.Model):
    """Espaço alugável do prédio (andar inteiro ou sala)."""

    class Tipo(models.TextChoices):
        ANDAR = 'andar', 'Andar'
        SALA = 'sala', 'Sala'

    nome = models.CharField('nome', max_length=80, unique=True)
    tipo = models.CharField('tipo', max_length=10, choices=Tipo.choices)
    andar = models.CharField('andar', max_length=30)
    ordem = models.PositiveSmallIntegerField('ordem', default=0)
    area_privativa = models.DecimalField('área privativa (m²)', max_digits=8, decimal_places=2, null=True, blank=True)
    area_rateio = models.DecimalField(
        'área no rateio (m²)', max_digits=8, decimal_places=2, null=True, blank=True,
        help_text='Para salas, é a parte proporcional da área do andar, dividida pela área privativa.',
    )
    entra_no_rateio_por_area = models.BooleanField(
        'entra no rateio por área', default=True,
        help_text='Desmarque para unidades que não participam da divisão por área.',
    )
    locatario = models.ForeignKey(
        Locatario, verbose_name='locatário', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='unidades',
    )
    ativa = models.BooleanField('ativa', default=True)

    class Meta:
        verbose_name = 'unidade'
        verbose_name_plural = 'unidades'
        ordering = ['ordem', 'nome']

    def __str__(self):
        return self.nome
