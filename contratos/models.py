import os
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.utils import timezone

from cadastros.models import Locatario, Unidade

DIAS_ALERTA_VENCIMENTO = 90


class Contrato(models.Model):
    class Status(models.TextChoices):
        NEGOCIACAO = 'negociacao', 'Em negociação'
        VIGENTE = 'vigente', 'Vigente'
        ENCERRADO = 'encerrado', 'Encerrado'
        RESCINDIDO = 'rescindido', 'Rescindido'

    class Indice(models.TextChoices):
        IGPM = 'igpm', 'IGP-M'
        IPCA = 'ipca', 'IPCA'
        FIXO = 'fixo', 'Percentual fixo'
        NENHUM = 'nenhum', 'Sem reajuste'

    unidade = models.ForeignKey(Unidade, verbose_name='unidade', on_delete=models.PROTECT, related_name='contratos')
    locatario = models.ForeignKey(Locatario, verbose_name='empresa', on_delete=models.PROTECT, related_name='contratos')
    status = models.CharField('situação', max_length=12, choices=Status.choices, default=Status.NEGOCIACAO)
    data_inicio = models.DateField('início', null=True, blank=True)
    data_fim = models.DateField('fim', null=True, blank=True)
    valor_aluguel = models.DecimalField(
        'aluguel mensal (R$)', max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
    )
    dia_vencimento = models.PositiveSmallIntegerField(
        'dia do vencimento', default=10, validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    indice_reajuste = models.CharField('índice de reajuste', max_length=10, choices=Indice.choices, default=Indice.IGPM)
    mes_reajuste = models.PositiveSmallIntegerField(
        'mês do reajuste', null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text='Número do mês (1 a 12). Em branco se não houver reajuste.',
    )
    pct_energia = models.DecimalField(
        '% da conta de energia', max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='Só para quem paga uma porcentagem fixa da energia (ex.: restaurante, 60). Em branco = rateio por área.',
    )
    pct_agua = models.DecimalField(
        '% da conta de água', max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text='Só para quem paga uma porcentagem fixa da água (ex.: restaurante, 40). Em branco = rateio por área.',
    )
    observacoes = models.TextField('observações', blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'contrato'
        verbose_name_plural = 'contratos'
        ordering = ['unidade__ordem', '-data_inicio']
        constraints = [
            models.UniqueConstraint(
                fields=['unidade'], condition=Q(status='vigente'), name='um_contrato_vigente_por_unidade',
            ),
        ]

    def __str__(self):
        return f'{self.unidade} — {self.locatario}'

    def clean(self):
        erros = {}
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            erros['data_fim'] = 'O fim não pode ser anterior ao início.'
        if self.status == self.Status.VIGENTE:
            if not self.data_inicio:
                erros['data_inicio'] = 'Contrato vigente precisa de data de início.'
            outro = Contrato.objects.filter(unidade_id=self.unidade_id, status=self.Status.VIGENTE).exclude(pk=self.pk)
            if self.unidade_id and outro.exists():
                erros['status'] = 'Esta unidade já tem um contrato vigente. Encerre o anterior antes.'
            elif self.unidade_id and self.locatario_id:
                ocupante = Unidade.objects.filter(pk=self.unidade_id).values_list('locatario__razao_social', 'locatario_id').first()
                if ocupante and ocupante[1] and ocupante[1] != self.locatario_id:
                    erros['locatario'] = (f'Esta unidade está cadastrada para {ocupante[0]}. '
                                          'Ajuste o cadastro das empresas antes de criar este contrato.')
        if erros:
            raise ValidationError(erros)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sincronizar_unidade()

    def _sincronizar_unidade(self):
        """Mantém a empresa da unidade em dia com o contrato vigente."""
        unidade = self.unidade
        if self.status == self.Status.VIGENTE:
            if unidade.locatario_id != self.locatario_id:
                unidade.locatario = self.locatario
                unidade.save(update_fields=['locatario'])
        elif self.status in (self.Status.ENCERRADO, self.Status.RESCINDIDO):
            tem_outro = Contrato.objects.filter(unidade=unidade, status=self.Status.VIGENTE).exists()
            if unidade.locatario_id == self.locatario_id and not tem_outro:
                unidade.locatario = None
                unidade.save(update_fields=['locatario'])

    @property
    def dias_para_vencer(self):
        if self.status != self.Status.VIGENTE or not self.data_fim:
            return None
        return (self.data_fim - timezone.localdate()).days

    @property
    def situacao_vencimento(self):
        """'vencido', 'a_vencer' (dentro do alerta) ou '' quando não há o que avisar."""
        dias = self.dias_para_vencer
        if dias is None:
            return ''
        if dias < 0:
            return 'vencido'
        if dias <= DIAS_ALERTA_VENCIMENTO:
            return 'a_vencer'
        return ''

    @classmethod
    def vigentes(cls):
        return cls.objects.filter(status=cls.Status.VIGENTE)

    @classmethod
    def a_vencer(cls):
        limite = timezone.localdate() + timedelta(days=DIAS_ALERTA_VENCIMENTO)
        return cls.vigentes().filter(data_fim__isnull=False, data_fim__lte=limite)


class AjusteContrato(models.Model):
    """Valor a mais ou desconto que entra nas cobranças do contrato (fixo em R$ ou em %)."""

    class Natureza(models.TextChoices):
        ACRESCIMO = 'acrescimo', 'Acréscimo'
        DESCONTO = 'desconto', 'Desconto'

    class Forma(models.TextChoices):
        VALOR = 'valor', 'R$ fixo'
        PERCENTUAL = 'percentual', '% do valor'

    class AplicaEm(models.TextChoices):
        ALUGUEL = 'aluguel', 'Aluguel'
        CONDOMINIO = 'condominio', 'Condomínio'

    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name='ajustes')
    descricao = models.CharField('descrição', max_length=120)
    natureza = models.CharField('tipo', max_length=10, choices=Natureza.choices, default=Natureza.DESCONTO)
    forma = models.CharField('forma', max_length=10, choices=Forma.choices, default=Forma.VALOR)
    valor = models.DecimalField('valor', max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    aplica_em = models.CharField('aplica em', max_length=12, choices=AplicaEm.choices, default=AplicaEm.ALUGUEL)
    inicio = models.DateField('a partir de', null=True, blank=True, help_text='Em branco = desde o início do contrato.')
    fim = models.DateField('até', null=True, blank=True, help_text='Em branco = sem data para acabar.')

    class Meta:
        verbose_name = 'ajuste do contrato'
        verbose_name_plural = 'ajustes do contrato'
        ordering = ['aplica_em', 'descricao']

    def __str__(self):
        return self.descricao

    def clean(self):
        if self.forma == self.Forma.PERCENTUAL and self.valor and self.valor > 100:
            raise ValidationError({'valor': 'Em porcentagem, o valor vai até 100.'})
        if self.inicio and self.fim and self.fim < self.inicio:
            raise ValidationError({'fim': 'O fim não pode ser anterior ao início.'})

    def vale_em(self, referencia):
        """Vale no mês da referência (qualquer dia do mês conta)."""
        primeiro = referencia.replace(day=1)
        proximo = (primeiro.replace(day=28) + timedelta(days=4)).replace(day=1)
        if self.inicio and self.inicio >= proximo:
            return False
        return not (self.fim and self.fim < primeiro)

    def calcular(self, base):
        """Valor com sinal: positivo soma, negativo desconta."""
        if self.forma == self.Forma.PERCENTUAL:
            montante = (base * self.valor / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        else:
            montante = self.valor
        return montante if self.natureza == self.Natureza.ACRESCIMO else -montante

    @property
    def resumo(self):
        quantia = f'{self.valor:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        return f'{quantia}%' if self.forma == self.Forma.PERCENTUAL else f'R$ {quantia}'


def caminho_documento(instancia, nome_arquivo):
    extensao = os.path.splitext(nome_arquivo)[1].lower()
    return f'contratos/{instancia.contrato_id}/{uuid.uuid4().hex}{extensao}'


class DocumentoContrato(models.Model):
    contrato = models.ForeignKey(Contrato, on_delete=models.CASCADE, related_name='documentos')
    descricao = models.CharField('descrição', max_length=120)
    arquivo = models.FileField('arquivo', upload_to=caminho_documento)
    nome_original = models.CharField(max_length=255, blank=True)
    enviado_em = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    class Meta:
        verbose_name = 'documento do contrato'
        verbose_name_plural = 'documentos do contrato'
        ordering = ['-enviado_em']

    def __str__(self):
        return self.descricao


@receiver(post_delete, sender=DocumentoContrato)
def apagar_arquivo(sender, instance, **kwargs):
    if instance.arquivo:
        instance.arquivo.delete(save=False)
