import calendar
import os
import uuid
from datetime import date

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from cadastros.models import Locatario, Unidade


def caminho_boleto(instancia, nome_arquivo):
    extensao = os.path.splitext(nome_arquivo)[1].lower()
    return f'boletos/{instancia.locatario_id}/{uuid.uuid4().hex}{extensao}'


def data_no_mes(ano, mes, dia):
    """Data com o dia pedido, ou o último dia do mês quando ele não existe (ex.: 31 em fevereiro)."""
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def somar_meses(referencia, meses):
    indice = referencia.year * 12 + (referencia.month - 1) + meses
    return date(indice // 12, indice % 12 + 1, 1)


class Competencia(models.Model):
    """Mês de despesas do prédio que será rateado."""

    class Status(models.TextChoices):
        ABERTA = 'aberta', 'Em preparação'
        CALCULADA = 'calculada', 'Rateio calculado'
        APROVADA = 'aprovada', 'Aprovada'

    referencia = models.DateField('mês de referência', unique=True, help_text='Sempre o dia 1 do mês.')
    status = models.CharField('situação', max_length=10, choices=Status.choices, default=Status.ABERTA)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'competência'
        verbose_name_plural = 'competências'
        ordering = ['-referencia']

    def __str__(self):
        return f'{self.referencia.month:02d}/{self.referencia.year}'

    @property
    def editavel(self):
        return self.status != self.Status.APROVADA

    @property
    def despesas_no_rateio(self):
        return self.despesas.filter(entra_no_rateio=True).aggregate(t=Sum('valor'))['t'] or 0

    def total_por_tipo(self):
        """Soma das despesas que entram no rateio, por tipo."""
        soma = {tipo: 0 for tipo in Despesa.Tipo.values}
        linhas = self.despesas.filter(entra_no_rateio=True).values('tipo').annotate(t=Sum('valor'))
        for linha in linhas:
            soma[linha['tipo']] = linha['t']
        return soma


class Despesa(models.Model):
    class Tipo(models.TextChoices):
        ENERGIA = 'energia', 'Energia'
        AGUA = 'agua', 'Água'
        DEMAIS = 'demais', 'Demais'

    competencia = models.ForeignKey(Competencia, on_delete=models.CASCADE, related_name='despesas')
    fornecedor = models.CharField('fornecedor', max_length=200, blank=True)
    descricao = models.CharField('descrição', max_length=300)
    categoria = models.CharField('categoria', max_length=120, blank=True)
    centro_custo = models.CharField('centro de custo', max_length=120, blank=True)
    vencimento = models.DateField('vencimento')
    valor = models.DecimalField('valor (R$)', max_digits=12, decimal_places=2)
    multa_juros = models.DecimalField(
        'multa e juros (R$)', max_digits=10, decimal_places=2, default=0,
        help_text='Pagos por atraso. Informativo: não entram no rateio.',
    )
    tipo = models.CharField('tipo', max_length=10, choices=Tipo.choices, default=Tipo.DEMAIS)
    entra_no_rateio = models.BooleanField('entra no rateio', default=True)
    motivo_exclusao = models.CharField('motivo da exclusão', max_length=200, blank=True)
    chave = models.CharField(max_length=80, editable=False)

    class Meta:
        verbose_name = 'despesa'
        verbose_name_plural = 'despesas'
        ordering = ['vencimento', 'id']
        constraints = [
            models.UniqueConstraint(fields=['competencia', 'chave'], name='despesa_unica_por_competencia'),
        ]

    def __str__(self):
        return f'{self.descricao} ({self.valor})'


class Rateio(models.Model):
    competencia = models.OneToOneField(Competencia, on_delete=models.CASCADE, related_name='rateio')
    calculado_em = models.DateTimeField(auto_now=True)
    total_energia = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_agua = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_demais = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    aprovado_em = models.DateTimeField(null=True, blank=True)
    aprovado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        verbose_name = 'rateio'
        verbose_name_plural = 'rateios'

    def __str__(self):
        return f'Rateio {self.competencia}'

    @property
    def total(self):
        return self.total_energia + self.total_agua + self.total_demais


class RateioItem(models.Model):
    rateio = models.ForeignKey(Rateio, on_delete=models.CASCADE, related_name='itens')
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT, related_name='+')
    locatario = models.ForeignKey(
        Locatario, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
        help_text='Em branco quando a unidade não tem contrato vigente: o custo fica com a Play.',
    )
    area = models.DecimalField(max_digits=8, decimal_places=2)
    pct_energia = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    pct_agua = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    energia = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    agua = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    demais = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ['unidade__ordem']

    @property
    def total(self):
        return self.energia + self.agua + self.demais


class Cobranca(models.Model):
    class Tipo(models.TextChoices):
        ALUGUEL = 'aluguel', 'Aluguel'
        CONDOMINIO = 'condominio', 'Condomínio'

    class Status(models.TextChoices):
        ABERTA = 'aberta', 'Em aberto'
        PAGA = 'paga', 'Paga'
        CANCELADA = 'cancelada', 'Cancelada'

    unidade = models.ForeignKey(
        Unidade, null=True, blank=True, on_delete=models.PROTECT, related_name='cobrancas',
        help_text='Em branco quando a cobrança soma várias unidades da empresa (veja os itens).',
    )
    locatario = models.ForeignKey(Locatario, on_delete=models.PROTECT, related_name='cobrancas')
    tipo = models.CharField('tipo', max_length=12, choices=Tipo.choices)
    referencia = models.DateField('mês de referência')
    valor = models.DecimalField('valor (R$)', max_digits=12, decimal_places=2)
    vencimento = models.DateField('vencimento')
    status = models.CharField('situação', max_length=10, choices=Status.choices, default=Status.ABERTA)
    pago_em = models.DateField('pago em', null=True, blank=True)
    valor_pago = models.DecimalField('valor pago (R$)', max_digits=12, decimal_places=2, null=True, blank=True)
    rateio = models.ForeignKey(Rateio, null=True, blank=True, on_delete=models.SET_NULL, related_name='cobrancas')
    observacao = models.CharField('observação', max_length=300, blank=True)
    boleto = models.FileField('boleto', upload_to=caminho_boleto, blank=True)
    boleto_nome = models.CharField(max_length=255, blank=True)
    boleto_enviado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'cobrança'
        verbose_name_plural = 'cobranças'
        ordering = ['-vencimento', 'unidade__ordem', 'locatario__razao_social']
        constraints = [
            models.UniqueConstraint(
                fields=['unidade', 'tipo', 'referencia'],
                condition=~Q(status='cancelada'),
                name='uma_cobranca_ativa_por_unidade_tipo_mes',
            ),
            models.UniqueConstraint(
                fields=['locatario', 'tipo', 'referencia'],
                condition=~Q(status='cancelada') & Q(unidade__isnull=True),
                name='uma_cobranca_unificada_ativa_por_empresa_tipo_mes',
            ),
        ]

    def __str__(self):
        return f'{self.get_tipo_display()} {self.referencia:%m/%Y} — {self.origem}'

    @property
    def fatura_disponivel(self):
        """Só o condomínio que veio de um rateio tem fatura (com os gastos do mês)."""
        return self.tipo == self.Tipo.CONDOMINIO and self.rateio_id is not None

    @property
    def origem(self):
        """A unidade da cobrança, ou a lista de unidades quando ela soma várias."""
        if self.unidade_id:
            return str(self.unidade)
        nomes = sorted({i.unidade.nome for i in self.itens.all() if i.unidade_id})
        return ', '.join(nomes) if nomes else 'várias unidades'

    @property
    def atrasada(self):
        return self.status == self.Status.ABERTA and self.vencimento < timezone.localdate()

    @property
    def dias_atraso(self):
        return (timezone.localdate() - self.vencimento).days if self.atrasada else 0

    @classmethod
    def em_aberto(cls):
        return cls.objects.filter(status=cls.Status.ABERTA)

    @classmethod
    def atrasadas(cls):
        return cls.em_aberto().filter(vencimento__lt=timezone.localdate())


class CobrancaItem(models.Model):
    """Uma linha da cobrança: o valor de uma unidade, ou um acréscimo/desconto do contrato (valor negativo)."""

    cobranca = models.ForeignKey(Cobranca, on_delete=models.CASCADE, related_name='itens')
    unidade = models.ForeignKey(Unidade, on_delete=models.PROTECT, related_name='+')
    descricao = models.CharField('descrição', max_length=200)
    valor = models.DecimalField('valor (R$)', max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['unidade__ordem', 'id']

    def __str__(self):
        return f'{self.unidade} — {self.descricao}'


class EnvioCobranca(models.Model):
    """Registro de cada e-mail de cobrança disparado pelo sistema, com o resultado."""

    cobranca = models.ForeignKey(Cobranca, on_delete=models.CASCADE, related_name='envios')
    para = models.EmailField('enviado para')
    com_boleto = models.BooleanField('com boleto', default=False)
    com_fatura = models.BooleanField('com fatura', default=False)
    enviado_em = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )
    ok = models.BooleanField('entregue ao servidor de e-mail', default=True)
    erro = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-enviado_em']


@receiver(post_delete, sender=Cobranca)
def apagar_boleto(sender, instance, **kwargs):
    if instance.boleto:
        instance.boleto.delete(save=False)
