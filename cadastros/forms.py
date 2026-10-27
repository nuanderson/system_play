from django import forms
from django.db import transaction
from django.db.models import Q

from .models import Locatario, Unidade


class UnidadesOcupadasField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, unidade):
        return f'{unidade.nome} · {unidade.andar}'


class LocatarioForm(forms.ModelForm):
    unidades = UnidadesOcupadasField(
        label='Unidades que ocupa', required=False, queryset=Unidade.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text='Marque todas as unidades da empresa. Só aparecem as livres (e as que já são dela). '
                  'Cada unidade continua com o seu próprio contrato.',
    )

    class Meta:
        model = Locatario
        fields = ['razao_social', 'cnpj', 'contato', 'email_cobranca', 'telefone', 'unidades', 'cobranca_unificada', 'ativo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        livres = Q(locatario__isnull=True)
        if self.instance.pk:
            livres |= Q(locatario=self.instance)
            self.initial['unidades'] = list(self.instance.unidades.values_list('pk', flat=True))
        self.fields['unidades'].queryset = Unidade.objects.filter(ativa=True).filter(livres)

    def clean_unidades(self):
        escolhidas = self.cleaned_data['unidades']
        if self.instance.pk:
            retiradas = self.instance.unidades.exclude(pk__in=[u.pk for u in escolhidas])
            com_contrato = [
                u.nome for u in retiradas
                if u.contratos.filter(locatario=self.instance, status='vigente').exists()
            ]
            if com_contrato:
                raise forms.ValidationError(
                    'Há contrato vigente em: ' + ', '.join(com_contrato) + '. Encerre o contrato antes de tirar a unidade.')
        return escolhidas

    @transaction.atomic
    def save(self, commit=True):
        locatario = super().save(commit=commit)
        if commit:
            escolhidas = self.cleaned_data['unidades']
            Unidade.objects.filter(locatario=locatario).exclude(pk__in=[u.pk for u in escolhidas]).update(locatario=None)
            Unidade.objects.filter(pk__in=[u.pk for u in escolhidas]).update(locatario=locatario)
        return locatario


class UnidadeForm(forms.ModelForm):
    class Meta:
        model = Unidade
        fields = [
            'nome', 'tipo', 'andar', 'ordem', 'area_privativa', 'area_rateio',
            'entra_no_rateio_por_area', 'locatario', 'ativa',
        ]
