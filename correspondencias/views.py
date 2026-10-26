import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from cadastros.models import Unidade
from core.permissions import EquipeRequiredMixin

from .forms import BaixaForm, CorrespondenciaForm, DevolucaoForm
from .models import Correspondencia


def correspondencias_visiveis(usuario):
    """Equipe e síndico veem tudo. Os demais veem só o que chegou para a própria unidade."""
    if usuario.e_equipe:
        return Correspondencia.objects.all()
    if usuario.unidade_id:
        return Correspondencia.objects.filter(unidade_id=usuario.unidade_id)
    return Correspondencia.objects.none()


class CorrespondenciaLista(LoginRequiredMixin, ListView):
    template_name = 'correspondencias/correspondencia_lista.html'
    context_object_name = 'itens'
    paginate_by = 30

    def get_queryset(self):
        consulta = correspondencias_visiveis(self.request.user).select_related('unidade')
        situacao = self.request.GET.get('situacao', 'aguardando')
        if situacao in Correspondencia.Status.values:
            consulta = consulta.filter(status=situacao)
        tipo = self.request.GET.get('tipo', '')
        if tipo in Correspondencia.Tipo.values:
            consulta = consulta.filter(tipo=tipo)
        unidade = self.request.GET.get('unidade', '')
        if self.request.user.e_equipe and unidade.isdigit():
            consulta = consulta.filter(unidade_id=int(unidade))
        busca = self.request.GET.get('q', '').strip()
        if busca:
            consulta = consulta.filter(Q(destinatario__icontains=busca) | Q(observacoes__icontains=busca))
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto.update(
            filtros=dict(
                situacao=self.request.GET.get('situacao', 'aguardando'), tipo=self.request.GET.get('tipo', ''),
                unidade=self.request.GET.get('unidade', ''), q=self.request.GET.get('q', ''),
            ),
            situacoes=[('todas', 'Todas')] + list(Correspondencia.Status.choices),
            tipos=Correspondencia.Tipo.choices,
            unidades=Unidade.objects.all() if self.request.user.e_equipe else [],
        )
        return contexto


class CorrespondenciaCriar(EquipeRequiredMixin, CreateView):
    model = Correspondencia
    form_class = CorrespondenciaForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Registrar correspondência', 'botao_extra': 'Salvar e registrar outra'}

    def form_valid(self, form):
        correspondencia = form.save(commit=False)
        correspondencia.registrado_por = self.request.user
        correspondencia.save()
        messages.success(self.request, f'{correspondencia.numero} registrada para {correspondencia.unidade}.')
        if 'outra' in self.request.POST:
            return redirect('correspondencia_criar')
        return redirect('correspondencia_detalhe', pk=correspondencia.pk)


class CorrespondenciaEditar(EquipeRequiredMixin, UpdateView):
    model = Correspondencia
    form_class = CorrespondenciaForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Editar correspondência'}

    def get_queryset(self):
        return Correspondencia.objects.filter(status=Correspondencia.Status.AGUARDANDO)

    def get_success_url(self):
        return reverse('correspondencia_detalhe', args=[self.object.pk])

    def form_valid(self, form):
        messages.success(self.request, 'Correspondência atualizada.')
        return super().form_valid(form)


class CorrespondenciaDetalhe(LoginRequiredMixin, View):
    def get(self, request, pk):
        item = get_object_or_404(correspondencias_visiveis(request.user), pk=pk)
        return self.renderizar(request, item)

    @staticmethod
    def renderizar(request, item, form_baixa=None, form_devolucao=None):
        contexto = dict(item=item, equipe=request.user.e_equipe)
        if request.user.e_equipe and item.aguardando:
            contexto['form_baixa'] = form_baixa or BaixaForm(correspondencia=item)
            contexto['form_devolucao'] = form_devolucao or DevolucaoForm()
        return render(request, 'correspondencias/correspondencia_detalhe.html', contexto)


class DarBaixa(EquipeRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(Correspondencia, pk=pk, status=Correspondencia.Status.AGUARDANDO)
        form = BaixaForm(request.POST, correspondencia=item)
        if not form.is_valid():
            return CorrespondenciaDetalhe.renderizar(request, item, form_baixa=form)
        item.status = Correspondencia.Status.RETIRADA
        item.retirada_por = form.cleaned_data['retirada_por']
        item.retirada_em = form.cleaned_data['retirada_em']
        item.baixa_obs = form.cleaned_data['baixa_obs']
        item.baixa_por = request.user
        item.save()
        messages.success(request, f'{item.numero}: retirada registrada.')
        return redirect('correspondencia_detalhe', pk=item.pk)


class Devolver(EquipeRequiredMixin, View):
    def post(self, request, pk):
        item = get_object_or_404(Correspondencia, pk=pk, status=Correspondencia.Status.AGUARDANDO)
        form = DevolucaoForm(request.POST)
        if not form.is_valid():
            return CorrespondenciaDetalhe.renderizar(request, item, form_devolucao=form)
        item.status = Correspondencia.Status.DEVOLVIDA
        item.retirada_em = timezone.now()
        item.baixa_obs = form.cleaned_data['baixa_obs']
        item.baixa_por = request.user
        item.save()
        messages.success(request, f'{item.numero}: marcada como devolvida.')
        return redirect('correspondencia_detalhe', pk=item.pk)


class FotoVer(LoginRequiredMixin, View):
    """A foto só é entregue a quem pode ver a correspondência. Não há endereço público."""

    def get(self, request, pk):
        item = get_object_or_404(correspondencias_visiveis(request.user), pk=pk)
        if not item.tem_foto:
            raise Http404
        try:
            arquivo = item.foto.open('rb')
        except FileNotFoundError:
            raise Http404('Arquivo não encontrado.')
        return FileResponse(arquivo, filename=os.path.basename(item.foto.name))
