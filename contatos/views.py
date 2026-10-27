from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from core.permissions import PermissaoRequiredMixin

from .forms import ContatoForm
from .models import Contato


class Mural(PermissaoRequiredMixin, ListView):
    permissao = 'mural_ver'
    """Todos os usuários logados veem o mural. Só a equipe e o síndico mexem nele."""
    template_name = 'contatos/mural.html'
    context_object_name = 'contatos'

    def get_queryset(self):
        consulta = Contato.objects.all()
        categoria = self.request.GET.get('categoria', '')
        if categoria in Contato.Categoria.values:
            consulta = consulta.filter(categoria=categoria)
        busca = self.request.GET.get('q', '').strip()
        if busca:
            consulta = consulta.filter(
                Q(nome__icontains=busca) | Q(observacoes__icontains=busca) | Q(telefone__icontains=busca))
        return sorted(consulta, key=lambda c: (Contato.ordem_da_categoria(c.categoria), c.nome.lower()))

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usadas = set(Contato.objects.values_list('categoria', flat=True))
        contexto.update(
            categoria_escolhida=self.request.GET.get('categoria', ''),
            busca=self.request.GET.get('q', ''),
            categorias=[(valor, nome) for valor, nome in Contato.Categoria.choices if valor in usadas],
            total=Contato.objects.count(),
        )
        return contexto


class ContatoCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'mural_editar'
    model = Contato
    form_class = ContatoForm
    template_name = 'form.html'
    success_url = reverse_lazy('mural')
    extra_context = {'titulo': 'Novo contato', 'botao_extra': 'Salvar e adicionar outro'}

    def get_initial(self):
        categoria = self.request.GET.get('categoria', '')
        return {'categoria': categoria} if categoria in Contato.Categoria.values else {}

    def form_valid(self, form):
        super().form_valid(form)
        messages.success(self.request, f'{self.object.nome} foi para o mural.')
        if 'outra' in self.request.POST:
            return redirect(f"{reverse_lazy('contato_criar')}?categoria={self.object.categoria}")
        return redirect(self.success_url)


class ContatoEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'mural_editar'
    model = Contato
    form_class = ContatoForm
    template_name = 'form.html'
    success_url = reverse_lazy('mural')
    extra_context = {'titulo': 'Editar contato'}

    def form_valid(self, form):
        messages.success(self.request, 'Contato atualizado.')
        return super().form_valid(form)


class ContatoExcluir(PermissaoRequiredMixin, View):
    permissao = 'mural_editar'

    def post(self, request, pk):
        contato = get_object_or_404(Contato, pk=pk)
        nome = contato.nome
        contato.delete()
        messages.success(request, f'{nome} saiu do mural.')
        return redirect('mural')
