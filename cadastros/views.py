from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from core.permissions import PermissaoRequiredMixin

from .forms import LocatarioForm, UnidadeForm
from .models import Locatario, Unidade


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
