from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.escopo import empresa_do_usuario
from core.permissions import PermissaoRequiredMixin

from .forms import LocatarioForm, PessoaEmpresaForm, UnidadeForm
from .models import Locatario, PessoaEmpresa, Unidade


class UnidadeLista(PermissaoRequiredMixin, ListView):
    permissao = 'cadastros_ver'
    model = Unidade
    template_name = 'cadastros/unidade_lista.html'
    context_object_name = 'unidades'

    def get_queryset(self):
        return Unidade.objects.select_related('locatario')


class UnidadeCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'unidades_editar'
    model = Unidade
    form_class = UnidadeForm
    template_name = 'form.html'
    success_url = reverse_lazy('unidade_lista')
    extra_context = {'titulo': 'Nova unidade'}

    def form_valid(self, form):
        messages.success(self.request, 'Unidade cadastrada.')
        return super().form_valid(form)


class UnidadeEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'unidades_editar'
    model = Unidade
    form_class = UnidadeForm
    template_name = 'form.html'
    success_url = reverse_lazy('unidade_lista')
    extra_context = {'titulo': 'Editar unidade'}

    def form_valid(self, form):
        messages.success(self.request, 'Unidade atualizada.')
        return super().form_valid(form)


class LocatarioLista(PermissaoRequiredMixin, ListView):
    permissao = 'cadastros_ver'
    model = Locatario
    template_name = 'cadastros/locatario_lista.html'
    context_object_name = 'locatarios'

    def get_queryset(self):
        return Locatario.objects.annotate(
            total_pessoas=Count('pessoas', filter=Q(pessoas__ativo=True), distinct=True))

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['total_pessoas'] = PessoaEmpresa.objects.filter(ativo=True, locatario__ativo=True).count()
        return contexto


class LocatarioCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'locatarios_editar'
    model = Locatario
    form_class = LocatarioForm
    template_name = 'form.html'
    success_url = reverse_lazy('locatario_lista')
    extra_context = {'titulo': 'Nova empresa'}

    def form_valid(self, form):
        messages.success(self.request, 'Empresa cadastrada.')
        return super().form_valid(form)


class LocatarioEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'locatarios_editar'
    model = Locatario
    form_class = LocatarioForm
    template_name = 'form.html'
    success_url = reverse_lazy('locatario_lista')
    extra_context = {'titulo': 'Editar empresa'}

    def form_valid(self, form):
        messages.success(self.request, 'Empresa atualizada.')
        return super().form_valid(form)


# ---- Pessoas da empresa (contagem, com ou sem acesso ao sistema) -------------

def pode_gerir_pessoas(usuario, empresa):
    """Síndico e administrativo mexem em qualquer empresa. O responsável, só na dele."""
    if usuario.pode('pessoas_gerenciar_todas'):
        return True
    return bool(usuario.pode('membros_gerenciar') and usuario.tem_escopo and empresa_do_usuario(usuario) == empresa)


class PessoaBase(LoginRequiredMixin, View):
    def empresa_e_pessoa(self, request, pk, por_pessoa):
        if por_pessoa:
            pessoa = get_object_or_404(PessoaEmpresa.objects.select_related('locatario', 'usuario'), pk=pk, ativo=True)
            empresa = pessoa.locatario
        else:
            pessoa, empresa = None, get_object_or_404(Locatario, pk=pk)
        if not pode_gerir_pessoas(request.user, empresa):
            raise PermissionDenied
        return empresa, pessoa

    def voltar(self, request, empresa):
        if request.user.pode('pessoas_gerenciar_todas'):
            return redirect('pessoa_lista', pk=empresa.pk)
        return redirect('membros')


class PessoaLista(PermissaoRequiredMixin, DetailView):
    permissao = 'pessoas_gerenciar_todas'
    model = Locatario
    template_name = 'cadastros/pessoa_lista.html'
    context_object_name = 'empresa'

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['pessoas'] = self.object.pessoas.filter(ativo=True).select_related('usuario')
        return contexto


class PessoaCriar(PessoaBase):
    def get(self, request, pk):
        empresa, _ = self.empresa_e_pessoa(request, pk, por_pessoa=False)
        return render(request, 'form.html', {'titulo': f'Nova pessoa · {empresa}', 'form': PessoaEmpresaForm(),
                                              'botao_extra': 'Salvar e adicionar outra'})

    def post(self, request, pk):
        empresa, _ = self.empresa_e_pessoa(request, pk, por_pessoa=False)
        form = PessoaEmpresaForm(request.POST)
        if not form.is_valid():
            return render(request, 'form.html', {'titulo': f'Nova pessoa · {empresa}', 'form': form,
                                                  'botao_extra': 'Salvar e adicionar outra'})
        pessoa = form.save(commit=False)
        pessoa.locatario = empresa
        pessoa.save()
        messages.success(request, f'{pessoa.nome} foi cadastrada.')
        if 'outra' in request.POST:
            return redirect('pessoa_criar', pk=empresa.pk)
        return self.voltar(request, empresa)


class PessoaEditar(PessoaBase):
    def get(self, request, pk):
        empresa, pessoa = self.empresa_e_pessoa(request, pk, por_pessoa=True)
        return render(request, 'form.html', {'titulo': f'Editar pessoa · {empresa}', 'form': PessoaEmpresaForm(instance=pessoa)})

    def post(self, request, pk):
        empresa, pessoa = self.empresa_e_pessoa(request, pk, por_pessoa=True)
        form = PessoaEmpresaForm(request.POST, instance=pessoa)
        if not form.is_valid():
            return render(request, 'form.html', {'titulo': f'Editar pessoa · {empresa}', 'form': form})
        form.save()
        messages.success(request, 'Pessoa atualizada.')
        return self.voltar(request, empresa)


class PessoaRemover(PessoaBase):
    """Tira a pessoa da contagem. Se ela tinha acesso ao sistema, o acesso é desativado junto."""

    def post(self, request, pk):
        empresa, pessoa = self.empresa_e_pessoa(request, pk, por_pessoa=True)
        if pessoa.usuario_id and pessoa.usuario.pk == request.user.pk:
            messages.error(request, 'Você não pode remover a si mesmo.')
            return self.voltar(request, empresa)
        pessoa.ativo = False
        pessoa.save(update_fields=['ativo'])
        if pessoa.usuario_id and pessoa.usuario.is_active:
            pessoa.usuario.is_active = False
            pessoa.usuario.save(update_fields=['is_active'])
        messages.success(request, f'{pessoa.nome} saiu da empresa'
                                  + (' e o acesso ao sistema foi desativado.' if pessoa.usuario_id else '.'))
        return self.voltar(request, empresa)
