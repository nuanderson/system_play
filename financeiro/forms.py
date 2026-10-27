import os
import re
from datetime import date
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Cobranca, Despesa

EXTENSOES_BOLETO = {'.pdf', '.jpg', '.jpeg', '.png'}


class MesField(forms.CharField):
    """Campo de mês (AAAA-MM) que devolve o dia 1 do mês."""

    def __init__(self, **kwargs):
        kwargs.setdefault('widget', forms.DateInput(attrs={'type': 'month'}, format='%Y-%m'))
        super().__init__(**kwargs)

    def clean(self, valor):
        valor = super().clean(valor)
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', valor or ''):
            raise forms.ValidationError('Informe o mês no formato AAAA-MM.')
        return date(int(valor[:4]), int(valor[5:]), 1)


class CompetenciaNovaForm(forms.Form):
    mes = MesField(label='Mês das despesas')
    arquivo = forms.FileField(
        label='Relatório de contas a pagar (.xls ou .xlsx)',
        help_text='Só entram as despesas com vencimento no mês escolhido.',
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if not arquivo.name.lower().endswith(('.xls', '.xlsx')):
            raise forms.ValidationError('Envie um arquivo .xls ou .xlsx.')
        if arquivo.size > 5 * 1024 * 1024:
            raise forms.ValidationError('O arquivo passa de 5 MB.')
        return arquivo


class DespesaMarcacaoForm(forms.ModelForm):
    class Meta:
        model = Despesa
        fields = ['tipo', 'entra_no_rateio', 'motivo_exclusao']
        widgets = {'motivo_exclusao': forms.TextInput(attrs={'size': 22})}

    def clean(self):
        dados = super().clean()
        if not dados.get('entra_no_rateio') and not dados.get('motivo_exclusao'):
            self.add_error('motivo_exclusao', 'Diga o motivo.')
        return dados


class BoletoForm(forms.Form):
    arquivo = forms.FileField(label='Boleto (PDF ou imagem)')

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        if os.path.splitext(arquivo.name)[1].lower() not in EXTENSOES_BOLETO:
            raise forms.ValidationError('Tipo de arquivo não permitido. Use PDF, JPG ou PNG.')
        if arquivo.size > 10 * 1024 * 1024:
            raise forms.ValidationError('O arquivo passa de 10 MB.')
        return arquivo


class EnviarCobrancaForm(forms.Form):
    para = forms.EmailField(label='Enviar para')
    mensagem = forms.CharField(label='Recado (opcional)', required=False, max_length=500,
                               widget=forms.Textarea(attrs={'rows': 2}))
    anexar_boleto = forms.BooleanField(label='Anexar o boleto', required=False, initial=True)
    anexar_fatura = forms.BooleanField(label='Anexar a fatura do condomínio (PDF)', required=False, initial=True)


class AluguelGerarForm(forms.Form):
    mes = MesField(label='Mês do aluguel')


class CobrancaPagarForm(forms.Form):
    pago_em = forms.DateField(label='Pago em', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    valor_pago = forms.DecimalField(label='Valor pago (R$)', max_digits=12, decimal_places=2, min_value=Decimal('0'))

    def __init__(self, *args, cobranca=None, **kwargs):
        super().__init__(*args, **kwargs)
        if cobranca is not None and not self.is_bound:
            self.initial = dict(pago_em=timezone.localdate(), valor_pago=cobranca.valor)


class CobrancaEditarForm(forms.ModelForm):
    class Meta:
        model = Cobranca
        fields = ['valor', 'vencimento', 'observacao']
        widgets = {'vencimento': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')}
