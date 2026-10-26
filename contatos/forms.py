import re

from django import forms

from .models import Contato


class ContatoForm(forms.ModelForm):
    class Meta:
        model = Contato
        fields = ['categoria', 'nome', 'telefone', 'whatsapp', 'observacoes']

    def clean_nome(self):
        return self.cleaned_data['nome'].strip()

    def clean_telefone(self):
        telefone = self.cleaned_data['telefone'].strip()
        if not re.fullmatch(r'[\d\s()+\-.]+', telefone):
            raise forms.ValidationError('Use só números, espaços, parênteses e traço.')
        quantidade = len(re.sub(r'\D', '', telefone))
        if not 3 <= quantidade <= 13:
            raise forms.ValidationError('O telefone parece incompleto. Informe com o DDD.')
        return telefone
