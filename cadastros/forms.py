from django import forms

from .models import Locatario, Unidade


class LocatarioForm(forms.ModelForm):
    class Meta:
        model = Locatario
        fields = ['razao_social', 'cnpj', 'contato', 'email_cobranca', 'telefone', 'ativo']


class UnidadeForm(forms.ModelForm):
    class Meta:
        model = Unidade
        fields = [
            'nome', 'tipo', 'andar', 'ordem', 'area_privativa', 'area_rateio',
            'entra_no_rateio_por_area', 'locatario', 'ativa',
        ]
