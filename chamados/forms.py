import os

from django import forms

from cadastros.models import Unidade
from core.models import Usuario

from .models import Anexo, Chamado, Comentario

EXTENSOES_PERMITIDAS = {'.jpg', '.jpeg', '.png', '.webp', '.pdf'}
TAMANHO_MAXIMO = 10 * 1024 * 1024  # 10 MB por arquivo
MAXIMO_NA_ABERTURA = 5


def validar_arquivo(arquivo):
    extensao = os.path.splitext(arquivo.name)[1].lower()
    if extensao not in EXTENSOES_PERMITIDAS:
        raise forms.ValidationError(f'"{arquivo.name}": use foto (JPG, PNG, WEBP) ou PDF.')
    if arquivo.size > TAMANHO_MAXIMO:
        raise forms.ValidationError(f'"{arquivo.name}" passa de 10 MB.')
    return arquivo


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput(attrs={'accept': 'image/*,.pdf'}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            return []
        arquivos = data if isinstance(data, (list, tuple)) else [data]
        return [validar_arquivo(super(MultipleFileField, self).clean(arquivo, initial)) for arquivo in arquivos]


class ChamadoForm(forms.ModelForm):
    fotos = MultipleFileField(
        label='Fotos ou PDF', required=False,
        help_text=f'Até {MAXIMO_NA_ABERTURA} arquivos de até 10 MB. Uma foto ajuda muito a resolver mais rápido.',
    )

    class Meta:
        model = Chamado
        fields = ['titulo', 'categoria', 'prioridade', 'unidade', 'local', 'descricao']
        widgets = {'descricao': forms.Textarea(attrs={'rows': 5})}

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        self.usuario = usuario
        if usuario.pode('chamados_gerenciar'):
            self.fields['unidade'].queryset = Unidade.objects.filter(ativa=True)
            self.fields['unidade'].empty_label = 'Área comum do prédio'
        else:
            unidades = usuario.unidades()
            if unidades.count() > 1:
                self.fields['unidade'].queryset = unidades
                self.fields['unidade'].required = True
                self.fields['unidade'].empty_label = 'Escolha a unidade'
                self.fields['unidade'].help_text = 'Em qual unidade está o problema?'
            else:
                del self.fields['unidade']

    def clean_fotos(self):
        fotos = self.cleaned_data.get('fotos') or []
        if len(fotos) > MAXIMO_NA_ABERTURA:
            raise forms.ValidationError(f'Envie no máximo {MAXIMO_NA_ABERTURA} arquivos de uma vez.')
        return fotos


class GerenciarForm(forms.ModelForm):
    class Meta:
        model = Chamado
        fields = ['status', 'prioridade', 'responsavel', 'fornecedor', 'custo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['responsavel'].queryset = Usuario.objects.filter(
            perfil__in=[Usuario.Perfil.SINDICO, Usuario.Perfil.EQUIPE], is_active=True)
        self.fields['responsavel'].empty_label = 'Ninguém'


class ComentarioForm(forms.ModelForm):
    class Meta:
        model = Comentario
        fields = ['texto', 'interno']
        widgets = {'texto': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'})}
        labels = {'texto': 'Escreva um comentário'}

    def __init__(self, *args, usuario, **kwargs):
        super().__init__(*args, **kwargs)
        if not usuario.pode('chamados_gerenciar'):
            del self.fields['interno']


class AnexoForm(forms.ModelForm):
    class Meta:
        model = Anexo
        fields = ['arquivo']

    def clean_arquivo(self):
        return validar_arquivo(self.cleaned_data['arquivo'])
