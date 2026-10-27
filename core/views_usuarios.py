from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.generic import CreateView, ListView, UpdateView


from .forms import MembroForm, UsuarioForm
from .models import Usuario
from .permissions import PermissaoRequiredMixin
from .usuarios import definir_senha_provisoria, pessoas_da_empresa


def guardar_senha_provisoria(request, usuario, voltar):
    """A senha provisória aparece uma única vez, na página seguinte. Não fica gravada em lugar nenhum."""
    request.session['senha_provisoria'] = dict(
        nome=str(usuario), login=usuario.username, senha=definir_senha_provisoria(usuario), voltar=voltar)
    return redirect('senha_provisoria')


def e_o_ultimo_sindico(usuario):
    return (usuario.perfil == Usuario.Perfil.SINDICO and usuario.is_active
            and not Usuario.objects.filter(perfil=Usuario.Perfil.SINDICO, is_active=True).exclude(pk=usuario.pk).exists())


# ---- Usuários (síndico) -----------------------------------------------------

class UsuarioLista(PermissaoRequiredMixin, ListView):
    permissao = 'usuarios_gerenciar'
    template_name = 'core/usuario_lista.html'
    context_object_name = 'usuarios'

    def get_queryset(self):
        consulta = Usuario.objects.select_related('unidade').order_by('-is_active', 'first_name', 'username')
        perfil = self.request.GET.get('perfil', '')
        if perfil in Usuario.Perfil.values:
            consulta = consulta.filter(perfil=perfil)
        situacao = self.request.GET.get('situacao', 'ativos')
        if situacao == 'ativos':
            consulta = consulta.filter(is_active=True)
        elif situacao == 'inativos':
            consulta = consulta.filter(is_active=False)
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            perfis=Usuario.Perfil.choices,
            filtros=dict(perfil=self.request.GET.get('perfil', ''), situacao=self.request.GET.get('situacao', 'ativos')),
        )
        return contexto


class UsuarioCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'usuarios_gerenciar'
    model = Usuario
    form_class = UsuarioForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Novo usuário'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['editor'] = self.request.user
        return kwargs

    def form_valid(self, form):
        usuario = form.save(commit=False)
        usuario.set_unusable_password()
        usuario.save()
        messages.success(self.request, f'{usuario} foi cadastrado.')
        return guardar_senha_provisoria(self.request, usuario, reverse('usuarios'))


class UsuarioEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'usuarios_gerenciar'
    model = Usuario
    form_class = UsuarioForm
    template_name = 'form.html'
    success_url = reverse_lazy('usuarios')
    extra_context = {'titulo': 'Editar usuário'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['editor'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, 'Usuário atualizado.')
        return super().form_valid(form)


class UsuarioAlternar(PermissaoRequiredMixin, View):
    """Ativa ou desativa o acesso. Desativar não apaga nada: o histórico continua com o nome da pessoa."""
    permissao = 'usuarios_gerenciar'

    def post(self, request, pk):
        usuario = get_object_or_404(Usuario, pk=pk)
        if usuario.pk == request.user.pk:
            messages.error(request, 'Você não pode desativar o seu próprio acesso.')
        elif e_o_ultimo_sindico(usuario):
            messages.error(request, 'O sistema precisa ter pelo menos um síndico ativo.')
        else:
            usuario.is_active = not usuario.is_active
            usuario.save(update_fields=['is_active'])
            messages.success(request, f'{usuario}: acesso {"liberado" if usuario.is_active else "desativado"}.')
        return redirect('usuarios')


class UsuarioNovaSenha(PermissaoRequiredMixin, View):
    permissao = 'usuarios_gerenciar'

    def post(self, request, pk):
        usuario = get_object_or_404(Usuario, pk=pk, is_active=True)
        if usuario.pk == request.user.pk:
            messages.error(request, 'Para trocar a sua senha, use o seu nome no alto da página.')
            return redirect('usuarios')
        return guardar_senha_provisoria(request, usuario, reverse('usuarios'))


# ---- Membros da unidade (responsável) ---------------------------------------

class MembrosMixin(PermissaoRequiredMixin):
    """O responsável só mexe nas pessoas da própria unidade, e só nas de perfil Membro."""
    permissao = 'membros_gerenciar'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.pode('membros_gerenciar') and not request.user.tem_escopo:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def membros(self):
        return pessoas_da_empresa(self.request.user).filter(perfil=Usuario.Perfil.MEMBRO)


class MembroLista(MembrosMixin, ListView):
    template_name = 'core/membro_lista.html'
    context_object_name = 'membros'

    def get_queryset(self):
        return self.membros().order_by('-is_active', 'first_name', 'username')

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        contexto['empresa'] = usuario.locatario
        contexto['unidades'] = usuario.unidades()
        contexto['responsaveis'] = pessoas_da_empresa(usuario).filter(perfil=Usuario.Perfil.LOCATARIO, is_active=True)
        return contexto


class MembroCriar(MembrosMixin, CreateView):
    model = Usuario
    form_class = MembroForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Nova pessoa da empresa'}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['responsavel'] = self.request.user
        return kwargs

    def form_valid(self, form):
        responsavel = self.request.user
        usuario = form.save(commit=False)
        usuario.perfil = Usuario.Perfil.MEMBRO
        usuario.locatario = responsavel.locatario
        if 'unidade' not in form.fields:
            # Sem escolha de unidade: o membro nunca enxerga mais do que o próprio responsável.
            usuario.unidade = responsavel.unidade if responsavel.unidade_id else None
        usuario.set_unusable_password()
        usuario.save()
        messages.success(self.request, f'{usuario} agora tem acesso.')
        return guardar_senha_provisoria(self.request, usuario, reverse('membros'))


class MembroAlternar(MembrosMixin, View):
    def post(self, request, pk):
        membro = get_object_or_404(self.membros(), pk=pk)
        membro.is_active = not membro.is_active
        membro.save(update_fields=['is_active'])
        messages.success(request, f'{membro}: acesso {"liberado" if membro.is_active else "desativado"}.')
        return redirect('membros')


class MembroNovaSenha(MembrosMixin, View):
    def post(self, request, pk):
        membro = get_object_or_404(self.membros(), pk=pk, is_active=True)
        return guardar_senha_provisoria(request, membro, reverse('membros'))


# ---- Senha provisória e troca de senha --------------------------------------

@method_decorator(never_cache, name='dispatch')
class SenhaProvisoria(LoginRequiredMixin, View):
    def get(self, request):
        dados = request.session.pop('senha_provisoria', None)
        if not dados:
            return redirect('inicio')
        return render(request, 'core/senha_provisoria.html', dados)


class TrocarSenha(LoginRequiredMixin, PasswordChangeView):
    template_name = 'registration/trocar_senha.html'
    success_url = reverse_lazy('inicio')

    def form_valid(self, form):
        resposta = super().form_valid(form)
        usuario = self.request.user
        if usuario.trocar_senha:
            usuario.trocar_senha = False
            usuario.save(update_fields=['trocar_senha'])
        messages.success(self.request, 'Senha alterada.')
        return resposta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['obrigatoria'] = self.request.user.trocar_senha
        return contexto
