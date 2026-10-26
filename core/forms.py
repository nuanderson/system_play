from django import forms

from cadastros.models import Unidade

from .models import Usuario


def _preparar_campos_comuns(form):
    form.fields['first_name'].label = 'Nome'
    form.fields['first_name'].required = True
    form.fields['last_name'].label = 'Sobrenome'
    form.fields['username'].label = 'Usuário (para entrar)'
    form.fields['username'].help_text = 'Sem espaços. Letras, números e . @ + - _'
    form.fields['email'].label = 'E-mail'
    form.fields['email'].required = True
    form.fields['telefone'].required = False


class UsuarioForm(forms.ModelForm):
    """Cadastro completo, feito pelo síndico."""

    class Meta:
        model = Usuario
        fields = ['first_name', 'last_name', 'username', 'email', 'telefone', 'perfil', 'unidade']

    def __init__(self, *args, editor, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        _preparar_campos_comuns(self)
        self.fields['unidade'].queryset = Unidade.objects.filter(ativa=True)
        self.fields['unidade'].required = False
        self.fields['unidade'].empty_label = '— (não se aplica)'
        self.fields['unidade'].help_text = 'Obrigatória para Responsável e Membro da unidade.'
        if self.instance.pk and self.instance.pk == editor.pk:
            self.fields['perfil'].disabled = True
            self.fields['perfil'].help_text = 'Você não pode mudar o seu próprio perfil.'

    def clean_username(self):
        return self.cleaned_data['username'].strip().lower()

    def clean(self):
        dados = super().clean()
        perfil, unidade = dados.get('perfil'), dados.get('unidade')
        if perfil in Usuario.PERFIS_DA_UNIDADE and not unidade:
            self.add_error('unidade', 'Informe a unidade deste usuário.')
        if perfil and perfil not in Usuario.PERFIS_DA_UNIDADE:
            dados['unidade'] = None
        if self.instance.pk and perfil != Usuario.Perfil.SINDICO and self.instance.perfil == Usuario.Perfil.SINDICO:
            outros = Usuario.objects.filter(perfil=Usuario.Perfil.SINDICO, is_active=True).exclude(pk=self.instance.pk)
            if not outros.exists():
                self.add_error('perfil', 'O sistema precisa ter pelo menos um síndico ativo.')
        return dados


class MembroForm(forms.ModelForm):
    """Cadastro simples, feito pelo responsável da unidade. Perfil e unidade são fixos."""

    class Meta:
        model = Usuario
        fields = ['first_name', 'last_name', 'username', 'email', 'telefone']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _preparar_campos_comuns(self)

    def clean_username(self):
        return self.cleaned_data['username'].strip().lower()
