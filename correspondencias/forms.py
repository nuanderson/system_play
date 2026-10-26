import os
from datetime import timedelta

from django import forms
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from cadastros.models import Unidade

from .models import Correspondencia

EXTENSOES_PERMITIDAS = {'.jpg', '.jpeg', '.png', '.webp'}
TAMANHO_MAXIMO = 8 * 1024 * 1024  # 8 MB
FORMATO_DATA_HORA = '%Y-%m-%dT%H:%M'


def campo_data_hora(rotulo, **kwargs):
    return forms.DateTimeField(
        label=rotulo,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=FORMATO_DATA_HORA),
        **kwargs,
    )


class CorrespondenciaForm(forms.ModelForm):
    recebido_em = campo_data_hora('Recebido em')

    class Meta:
        model = Correspondencia
        fields = ['unidade', 'tipo', 'destinatario', 'recebido_em', 'foto', 'observacoes']
        widgets = {'foto': forms.FileInput(attrs={'accept': 'image/*'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unidade'].queryset = Unidade.objects.filter(ativa=True)
        if not self.is_bound and not self.instance.pk:
            self.initial['recebido_em'] = timezone.localtime().replace(second=0, microsecond=0)

    def clean_foto(self):
        foto = self.cleaned_data.get('foto')
        if isinstance(foto, UploadedFile):
            extensao = os.path.splitext(foto.name)[1].lower()
            if extensao not in EXTENSOES_PERMITIDAS:
                raise forms.ValidationError('Use uma foto JPG, PNG ou WEBP.')
            if foto.size > TAMANHO_MAXIMO:
                raise forms.ValidationError('A foto passa de 8 MB.')
        return foto

    def clean_recebido_em(self):
        valor = self.cleaned_data['recebido_em']
        if valor > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError('A data de recebimento não pode ser no futuro.')
        return valor


class BaixaForm(forms.Form):
    retirada_por = forms.CharField(label='Quem retirou', max_length=150)
    retirada_em = campo_data_hora('Retirada em')
    baixa_obs = forms.CharField(label='Observação', max_length=300, required=False)

    def __init__(self, *args, correspondencia, **kwargs):
        super().__init__(*args, **kwargs)
        self.correspondencia = correspondencia
        if not self.is_bound:
            self.initial['retirada_em'] = timezone.localtime().replace(second=0, microsecond=0)

    def clean_retirada_em(self):
        valor = self.cleaned_data['retirada_em']
        if valor < self.correspondencia.recebido_em:
            raise forms.ValidationError('A retirada não pode ser antes do recebimento.')
        if valor > timezone.now() + timedelta(minutes=5):
            raise forms.ValidationError('A retirada não pode ser no futuro.')
        return valor


class DevolucaoForm(forms.Form):
    baixa_obs = forms.CharField(label='Motivo da devolução', max_length=300)
