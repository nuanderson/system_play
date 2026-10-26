import re

from django.db import models


class Contato(models.Model):
    """Prestador de serviço ou telefone útil, mostrado como um post-it no mural."""

    class Categoria(models.TextChoices):
        ELETRICA = 'eletrica', 'Elétrica'
        HIDRAULICA = 'hidraulica', 'Hidráulica'
        AR = 'ar', 'Ar-condicionado'
        ELEVADOR = 'elevador', 'Elevador'
        LIMPEZA = 'limpeza', 'Limpeza'
        SEGURANCA = 'seguranca', 'Segurança e portaria'
        TI = 'ti', 'Internet e telefonia'
        CHAVEIRO = 'chaveiro', 'Chaveiro e serralheria'
        DEDETIZACAO = 'dedetizacao', 'Dedetização'
        JARDINAGEM = 'jardinagem', 'Jardinagem'
        EMERGENCIA = 'emergencia', 'Emergência e concessionárias'
        OUTROS = 'outros', 'Outros'

    categoria = models.CharField('categoria', max_length=12, choices=Categoria.choices, default=Categoria.OUTROS)
    nome = models.CharField('nome', max_length=120, help_text='Pessoa ou empresa. Ex.: João Eletricista.')
    telefone = models.CharField('telefone', max_length=25, help_text='Com DDD. Ex.: (63) 99999-0000.')
    whatsapp = models.BooleanField('atende por WhatsApp', default=False)
    observacoes = models.CharField('observações', max_length=200, blank=True, help_text='Ex.: atende 24 horas, só com agendamento.')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'contato'
        verbose_name_plural = 'contatos'
        ordering = ['nome']

    def __str__(self):
        return f'{self.nome} ({self.get_categoria_display()})'

    @property
    def digitos(self):
        return re.sub(r'\D', '', self.telefone)

    @property
    def telefone_exibido(self):
        d = self.digitos
        if len(d) == 11:
            return f'({d[:2]}) {d[2:7]}-{d[7:]}'
        if len(d) == 10:
            return f'({d[:2]}) {d[2:6]}-{d[6:]}'
        return self.telefone

    @property
    def link_telefone(self):
        d = self.digitos
        return f'tel:+55{d}' if len(d) in (10, 11) else f'tel:{d}'

    @property
    def link_whatsapp(self):
        d = self.digitos
        if not self.whatsapp or len(d) not in (10, 11):
            return ''
        return f'https://wa.me/55{d}'

    @property
    def cor(self):
        """Cor do post-it, estável por categoria (0 a 4)."""
        return list(self.Categoria.values).index(self.categoria) % 5

    @classmethod
    def ordem_da_categoria(cls, valor):
        return list(cls.Categoria.values).index(valor)
