import os

from django import forms

from .models import Contrato, DocumentoContrato

EXTENSOES_PERMITIDAS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.jpg', '.jpeg', '.png'}
TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB


class ContratoForm(forms.ModelForm):
    class Meta:
        model = Contrato
        fields = [
            'unidade', 'locatario', 'status', 'data_inicio', 'data_fim',
            'valor_aluguel', 'dia_vencimento', 'indice_reajuste', 'mes_reajuste',
            'pct_energia', 'pct_agua', 'observacoes',
        ]
        widgets = {
            'data_inicio': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'data_fim': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'observacoes': forms.Textarea(attrs={'rows': 3}),
        }


class DocumentoForm(forms.ModelForm):
    class Meta:
        model = DocumentoContrato
        fields = ['descricao', 'arquivo']

    def clean_arquivo(self):
        arquivo = self.cleaned_data['arquivo']
        extensao = os.path.splitext(arquivo.name)[1].lower()
        if extensao not in EXTENSOES_PERMITIDAS:
            raise forms.ValidationError('Tipo de arquivo não permitido. Use PDF, Word, Excel, JPG ou PNG.')
        if arquivo.size > TAMANHO_MAXIMO:
            raise forms.ValidationError('O arquivo passa de 10 MB.')
        return arquivo
