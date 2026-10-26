from django.contrib import messages
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView

from core.permissions import SindicoRequiredMixin

from .forms import LocatarioForm, UnidadeForm
from .models import Locatario, Unidade


class UnidadeLista(SindicoRequiredMixin, ListView):
    model = Unidade
    template_name = 'cadastros/unidade_lista.html'
    context_object_name = 'unidades'

    def get_queryset(self):
        return Unidade.objects.select_related('locatario')


class UnidadeCriar(SindicoRequiredMixin, CreateView):
    model = Unidade
    form_class = UnidadeForm
    template_name = 'form.html'
    success_url = reverse_lazy('unidade_lista')
    extra_context = {'titulo': 'Nova unidade'}

    def form_valid(self, form):
        messages.success(self.request, 'Unidade cadastrada.')
        return super().form_valid(form)


class UnidadeEditar(SindicoRequiredMixin, UpdateView):
    model = Unidade
    form_class = UnidadeForm
    template_name = 'form.html'
    success_url = reverse_lazy('unidade_lista')
    extra_context = {'titulo': 'Editar unidade'}

    def form_valid(self, form):
        messages.success(self.request, 'Unidade atualizada.')
        return super().form_valid(form)


class LocatarioLista(SindicoRequiredMixin, ListView):
    model = Locatario
    template_name = 'cadastros/locatario_lista.html'
    context_object_name = 'locatarios'


class LocatarioCriar(SindicoRequiredMixin, CreateView):
    model = Locatario
    form_class = LocatarioForm
    template_name = 'form.html'
    success_url = reverse_lazy('locatario_lista')
    extra_context = {'titulo': 'Novo locatário'}

    def form_valid(self, form):
        messages.success(self.request, 'Locatário cadastrado.')
        return super().form_valid(form)


class LocatarioEditar(SindicoRequiredMixin, UpdateView):
    model = Locatario
    form_class = LocatarioForm
    template_name = 'form.html'
    success_url = reverse_lazy('locatario_lista')
    extra_context = {'titulo': 'Editar locatário'}

    def form_valid(self, form):
        messages.success(self.request, 'Locatário atualizado.')
        return super().form_valid(form)
