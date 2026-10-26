import os

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from core.permissions import PermissaoRequiredMixin

from .forms import ContratoForm, DocumentoForm
from .models import Contrato, DocumentoContrato


def contratos_visiveis(usuario):
    """Síndico vê todos. O responsável da unidade vê só os contratos da própria unidade."""
    if usuario.pode('contratos_ver_todos'):
        return Contrato.objects.all()
    if usuario.pode('contratos_ver_proprio') and usuario.unidade_id:
        return Contrato.objects.filter(unidade_id=usuario.unidade_id)
    return Contrato.objects.none()


class ContratoVisivelMixin(LoginRequiredMixin):
    def get_queryset(self):
        return contratos_visiveis(self.request.user).select_related('unidade', 'locatario')

    def dispatch(self, request, *args, **kwargs):
        usuario = request.user
        if usuario.is_authenticated and not usuario.pode('contratos_ver_todos', 'contratos_ver_proprio'):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class ContratoLista(ContratoVisivelMixin, ListView):
    template_name = 'contratos/contrato_lista.html'
    context_object_name = 'contratos'

    def get_queryset(self):
        consulta = super().get_queryset()
        status = self.request.GET.get('status')
        if status in Contrato.Status.values:
            consulta = consulta.filter(status=status)
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['status_escolhido'] = self.request.GET.get('status', '')
        contexto['opcoes_status'] = Contrato.Status.choices
        return contexto


class ContratoDetalhe(ContratoVisivelMixin, DetailView):
    template_name = 'contratos/contrato_detalhe.html'
    context_object_name = 'contrato'

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto['documentos'] = self.object.documentos.all()
        if self.request.user.pode('contratos_editar'):
            contexto['form_documento'] = DocumentoForm()
        return contexto


class ContratoCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'contratos_editar'
    model = Contrato
    form_class = ContratoForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Novo contrato'}

    def get_success_url(self):
        return reverse('contrato_detalhe', args=[self.object.pk])

    def form_valid(self, form):
        messages.success(self.request, 'Contrato cadastrado.')
        return super().form_valid(form)


class ContratoEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'contratos_editar'
    model = Contrato
    form_class = ContratoForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Editar contrato'}

    def get_success_url(self):
        return reverse('contrato_detalhe', args=[self.object.pk])

    def form_valid(self, form):
        messages.success(self.request, 'Contrato atualizado.')
        return super().form_valid(form)


class DocumentoEnviar(PermissaoRequiredMixin, View):
    permissao = 'contratos_editar'
    def post(self, request, pk):
        contrato = get_object_or_404(Contrato, pk=pk)
        form = DocumentoForm(request.POST, request.FILES)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.contrato = contrato
            documento.enviado_por = request.user
            documento.nome_original = os.path.basename(request.FILES['arquivo'].name)[:255]
            documento.save()
            messages.success(request, 'Documento anexado.')
        else:
            for erros in form.errors.values():
                for erro in erros:
                    messages.error(request, erro)
        return redirect('contrato_detalhe', pk=contrato.pk)


class DocumentoBaixar(LoginRequiredMixin, View):
    """Entrega o arquivo só a quem pode ver o contrato. Os arquivos nunca ficam em endereço público."""

    def get(self, request, pk):
        documento = get_object_or_404(DocumentoContrato.objects.select_related('contrato'), pk=pk)
        if not contratos_visiveis(request.user).filter(pk=documento.contrato_id).exists():
            raise PermissionDenied
        try:
            arquivo = documento.arquivo.open('rb')
        except FileNotFoundError:
            raise Http404('Arquivo não encontrado.')
        return FileResponse(arquivo, as_attachment=True, filename=documento.nome_original or documento.arquivo.name)


class DocumentoExcluir(PermissaoRequiredMixin, View):
    permissao = 'contratos_editar'
    def post(self, request, pk):
        documento = get_object_or_404(DocumentoContrato, pk=pk)
        contrato_pk = documento.contrato_id
        documento.delete()
        messages.success(request, 'Documento removido.')
        return redirect('contrato_detalhe', pk=contrato_pk)
