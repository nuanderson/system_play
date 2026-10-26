from django import forms

from cadastros.models import Locatario, Unidade

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
        fields = ['first_name', 'last_name', 'username', 'email', 'telefone', 'perfil', 'locatario', 'unidade']

    def __init__(self, *args, editor, **kwargs):
        super().__init__(*args, **kwargs)
        self.editor = editor
        _preparar_campos_comuns(self)
        self.fields['locatario'].queryset = Locatario.objects.filter(ativo=True)
        self.fields['locatario'].required = False
        self.fields['locatario'].empty_label = '— (não se aplica)'
        self.fields['locatario'].label = 'Empresa'
        self.fields['locatario'].help_text = (
            'Para Responsável e Membro da unidade. O usuário enxerga todas as unidades que a empresa ocupa.')
        self.fields['unidade'].queryset = Unidade.objects.filter(ativa=True)
        self.fields['unidade'].required = False
        self.fields['unidade'].empty_label = '— todas as da empresa'
        self.fields['unidade'].label = 'Limitar a uma unidade'
        self.fields['unidade'].help_text = 'Opcional. Preencha só se esta pessoa deve ver apenas uma das unidades.'
        if self.instance.pk and self.instance.pk == editor.pk:
            self.fields['perfil'].disabled = True
            self.fields['perfil'].help_text = 'Você não pode mudar o seu próprio perfil.'

    def clean_username(self):
        return self.cleaned_data['username'].strip().lower()

    def clean(self):
        dados = super().clean()
        perfil, unidade, locatario = dados.get('perfil'), dados.get('unidade'), dados.get('locatario')
        if perfil in Usuario.PERFIS_DA_UNIDADE:
            if not unidade and not locatario:
                self.add_error('locatario', 'Informe a empresa (ou ao menos a unidade) deste usuário.')
            elif unidade and locatario and unidade.locatario_id and unidade.locatario_id != locatario.pk:
                self.add_error('unidade', f'{unidade.nome} está cadastrada para {unidade.locatario}, e não para esta empresa.')
        elif perfil:
            dados['unidade'] = None
            dados['locatario'] = None
        if self.instance.pk and perfil != Usuario.Perfil.SINDICO and self.instance.perfil == Usuario.Perfil.SINDICO:
            outros = Usuario.objects.filter(perfil=Usuario.Perfil.SINDICO, is_active=True).exclude(pk=self.instance.pk)
            if not outros.exists():
                self.add_error('perfil', 'O sistema precisa ter pelo menos um síndico ativo.')
        return dados


class MembroForm(forms.ModelForm):
    """Cadastro simples, feito pelo responsável. Perfil e empresa são fixos; a unidade é opcional."""

    class Meta:
        model = Usuario
        fields = ['first_name', 'last_name', 'username', 'email', 'telefone', 'unidade']

    def __init__(self, *args, responsavel, **kwargs):
        super().__init__(*args, **kwargs)
        _preparar_campos_comuns(self)
        unidades = responsavel.unidades()
        if responsavel.locatario_id and unidades.count() > 1:
            self.fields['unidade'].queryset = unidades
            self.fields['unidade'].required = False
            self.fields['unidade'].empty_label = '— todas as unidades da empresa'
            self.fields['unidade'].label = 'Limitar a uma unidade'
            self.fields['unidade'].help_text = 'Opcional. Deixe em branco se esta pessoa pode ver todas as unidades.'
        else:
            del self.fields['unidade']

    def clean_username(self):
        return self.cleaned_data['username'].strip().lower()
