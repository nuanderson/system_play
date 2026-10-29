from django.conf import settings
from django.db import models


class Locatario(models.Model):
    """Empresa que ocupa uma ou mais unidades."""

    razao_social = models.CharField('razão social', max_length=200)
    cnpj = models.CharField('CNPJ', max_length=18, blank=True)
    contato = models.CharField('contato', max_length=120, blank=True)
    email_cobranca = models.EmailField('e-mail de cobrança', blank=True)
    telefone = models.CharField('telefone', max_length=30, blank=True)
    ativo = models.BooleanField('ativo', default=True)
    cobranca_unificada = models.BooleanField(
        'unificar a cobrança das unidades', default=True,
        help_text='Com mais de uma unidade, gera uma cobrança só de aluguel e uma só de condomínio por mês, '
                  'somando as unidades. Desmarque para cobrar cada unidade separadamente.',
    )

    class Meta:
        verbose_name = 'empresa'
        verbose_name_plural = 'empresas'
        ordering = ['razao_social']

    def __str__(self):
        return self.razao_social


class PessoaEmpresa(models.Model):
    """Quem trabalha na empresa, com ou sem acesso ao sistema. Serve para saber quantas pessoas o prédio tem."""

    locatario = models.ForeignKey(Locatario, verbose_name='empresa', on_delete=models.CASCADE, related_name='pessoas')
    nome = models.CharField('nome', max_length=150)
    cargo = models.CharField('cargo ou função', max_length=100, blank=True)
    telefone = models.CharField('telefone', max_length=30, blank=True)
    email = models.EmailField('e-mail', blank=True)
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='pessoa',
        help_text='Preenchido quando a pessoa também tem acesso ao sistema.',
    )
    ativo = models.BooleanField('na empresa', default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'pessoa da empresa'
        verbose_name_plural = 'pessoas da empresa'
        ordering = ['nome']

    def __str__(self):
        return self.nome

    @property
    def tem_acesso(self):
        return bool(self.usuario_id and self.usuario.is_active)


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
        Locatario, verbose_name='empresa', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='unidades',
    )
    ativa = models.BooleanField('ativa', default=True)

    class Meta:
        verbose_name = 'unidade'
        verbose_name_plural = 'unidades'
        ordering = ['ordem', 'nome']

    def __str__(self):
        return self.nome
