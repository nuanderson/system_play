import os

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView

from cadastros.models import Unidade
from core.permissions import PermissaoRequiredMixin

from .forms import AnexoForm, ChamadoForm, ComentarioForm, GerenciarForm
from .models import Anexo, Chamado, Comentario, registrar


def chamados_visiveis(usuario):
    """Equipe e síndico veem todos. Os demais veem os chamados da própria unidade."""
    if usuario.pode('chamados_gerenciar'):
        return Chamado.objects.all()
    if usuario.unidade_id:
        return Chamado.objects.filter(unidade_id=usuario.unidade_id)
    return Chamado.objects.none()


def pode_cancelar(usuario, chamado):
    return chamado.status == Chamado.Status.ABERTO and (usuario.pode('chamados_gerenciar') or chamado.solicitante_id == usuario.pk)


class ChamadoLista(PermissaoRequiredMixin, ListView):
    permissao = 'chamados_ver'
    template_name = 'chamados/chamado_lista.html'
    context_object_name = 'chamados'
    paginate_by = 30

    def get_queryset(self):
        consulta = chamados_visiveis(self.request.user).select_related('unidade', 'responsavel', 'solicitante')
        situacao = self.request.GET.get('situacao', 'abertos')
        if situacao == 'abertos':
            consulta = consulta.filter(status__in=Chamado.STATUS_EM_ABERTO)
        elif situacao in Chamado.Status.values:
            consulta = consulta.filter(status=situacao)
        prioridade = self.request.GET.get('prioridade', '')
        if prioridade.isdigit() and int(prioridade) in Chamado.Prioridade.values:
            consulta = consulta.filter(prioridade=int(prioridade))
        categoria = self.request.GET.get('categoria', '')
        if categoria in Chamado.Categoria.values:
            consulta = consulta.filter(categoria=categoria)
        unidade = self.request.GET.get('unidade', '')
        if self.request.user.pode('chamados_gerenciar') and unidade.isdigit():
            consulta = consulta.filter(unidade_id=int(unidade))
        return consulta.order_by('-prioridade', '-aberto_em') if situacao == 'abertos' else consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        contexto.update(
            filtros=dict(
                situacao=self.request.GET.get('situacao', 'abertos'),
                prioridade=self.request.GET.get('prioridade', ''),
                categoria=self.request.GET.get('categoria', ''),
                unidade=self.request.GET.get('unidade', ''),
            ),
            situacoes=[('abertos', 'Em aberto'), ('todos', 'Todos')] + list(Chamado.Status.choices),
            prioridades=Chamado.Prioridade.choices,
            categorias=Chamado.Categoria.choices,
            unidades=Unidade.objects.all() if usuario.pode('chamados_gerenciar') else [],
            pode_abrir=usuario.pode('chamados_gerenciar') or (usuario.pode('chamados_ver') and bool(usuario.unidade_id)),
        )
        return contexto


class ChamadoCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'chamados_ver'
    model = Chamado
    form_class = ChamadoForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Abrir chamado de manutenção'}

    def dispatch(self, request, *args, **kwargs):
        usuario = request.user
        if usuario.is_authenticated and not (usuario.pode('chamados_gerenciar') or (usuario.pode('chamados_ver') and usuario.unidade_id)):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['usuario'] = self.request.user
        return kwargs

    def form_valid(self, form):
        usuario = self.request.user
        chamado = form.save(commit=False)
        chamado.solicitante = usuario
        if not usuario.pode('chamados_gerenciar'):
            chamado.unidade = usuario.unidade
        chamado.save()
        for arquivo in form.cleaned_data['fotos']:
            Anexo.objects.create(
                chamado=chamado, arquivo=arquivo, enviado_por=usuario,
                nome_original=os.path.basename(arquivo.name)[:255])
        registrar(chamado, usuario, 'Chamado aberto.')
        messages.success(self.request, f'Chamado {chamado.numero} aberto.')
        return redirect('chamado_detalhe', pk=chamado.pk)


class ChamadoDetalhe(PermissaoRequiredMixin, View):
    permissao = 'chamados_ver'
    def get(self, request, pk):
        chamado = get_object_or_404(chamados_visiveis(request.user), pk=pk)
        return self.renderizar(request, chamado)

    @staticmethod
    def renderizar(request, chamado, **formularios):
        usuario = request.user
        historico = chamado.comentarios.select_related('autor')
        if not usuario.pode('chamados_gerenciar'):
            historico = historico.filter(interno=False)
        contexto = dict(
            chamado=chamado,
            historico=historico,
            anexos=chamado.anexos.all(),
            equipe=usuario.pode('chamados_gerenciar'),
            pode_cancelar=pode_cancelar(usuario, chamado),
            form_comentario=formularios.get('form_comentario') or ComentarioForm(usuario=usuario),
            form_anexo=AnexoForm(),
        )
        if usuario.pode('chamados_gerenciar'):
            contexto['form_gerenciar'] = formularios.get('form_gerenciar') or GerenciarForm(instance=chamado)
        return render(request, 'chamados/chamado_detalhe.html', contexto)


class Gerenciar(PermissaoRequiredMixin, View):
    permissao = 'chamados_gerenciar'
    """Equipe e síndico: situação, prioridade, responsável, fornecedor e custo."""

    def post(self, request, pk):
        chamado = get_object_or_404(Chamado, pk=pk)
        antes = {c: getattr(chamado, c) for c in GerenciarForm.Meta.fields}
        form = GerenciarForm(request.POST, instance=chamado)
        if not form.is_valid():
            return ChamadoDetalhe.renderizar(request, chamado, form_gerenciar=form)
        chamado = form.save(commit=False)
        if chamado.status == Chamado.Status.RESOLVIDO and not chamado.resolvido_em:
            chamado.resolvido_em = timezone.now()
        elif chamado.status != Chamado.Status.RESOLVIDO:
            chamado.resolvido_em = None
        chamado.save()
        autor = request.user
        if 'status' in form.changed_data:
            registrar(chamado, autor, f'Situação: {Chamado.Status(antes["status"]).label} → {chamado.get_status_display()}.')
        if 'prioridade' in form.changed_data:
            registrar(chamado, autor, f'Prioridade: {Chamado.Prioridade(antes["prioridade"]).label} → {chamado.get_prioridade_display()}.')
        if 'responsavel' in form.changed_data:
            registrar(chamado, autor, f'Responsável: {chamado.responsavel or "ninguém"}.')
        if 'fornecedor' in form.changed_data or 'custo' in form.changed_data:
            registrar(chamado, autor, f'Fornecedor: {chamado.fornecedor or "—"}; custo: {chamado.custo if chamado.custo is not None else "—"}.', interno=True)
        if form.changed_data:
            messages.success(request, 'Chamado atualizado.')
        return redirect('chamado_detalhe', pk=chamado.pk)


class Comentar(PermissaoRequiredMixin, View):
    permissao = 'chamados_ver'
    def post(self, request, pk):
        chamado = get_object_or_404(chamados_visiveis(request.user), pk=pk)
        if chamado.status == Chamado.Status.CANCELADO:
            messages.error(request, 'Este chamado foi cancelado.')
            return redirect('chamado_detalhe', pk=chamado.pk)
        form = ComentarioForm(request.POST, usuario=request.user)
        if not form.is_valid():
            return ChamadoDetalhe.renderizar(request, chamado, form_comentario=form)
        comentario = form.save(commit=False)
        comentario.chamado = chamado
        comentario.autor = request.user
        comentario.interno = bool(request.user.pode('chamados_gerenciar') and form.cleaned_data.get('interno'))
        comentario.save()
        chamado.save(update_fields=['atualizado_em'])
        return redirect('chamado_detalhe', pk=chamado.pk)


class AnexoEnviar(PermissaoRequiredMixin, View):
    permissao = 'chamados_ver'
    def post(self, request, pk):
        chamado = get_object_or_404(chamados_visiveis(request.user), pk=pk)
        if chamado.status == Chamado.Status.CANCELADO:
            raise PermissionDenied
        form = AnexoForm(request.POST, request.FILES)
        if form.is_valid():
            anexo = form.save(commit=False)
            anexo.chamado = chamado
            anexo.enviado_por = request.user
            anexo.nome_original = os.path.basename(request.FILES['arquivo'].name)[:255]
            anexo.save()
            registrar(chamado, request.user, f'Anexou {anexo.nome_original}.')
            messages.success(request, 'Arquivo anexado.')
        else:
            for erros in form.errors.values():
                for erro in erros:
                    messages.error(request, erro)
        return redirect('chamado_detalhe', pk=chamado.pk)


class AnexoBaixar(PermissaoRequiredMixin, View):
    permissao = 'chamados_ver'
    """Entrega o arquivo só a quem vê o chamado. Fotos abrem na tela; PDFs são baixados."""

    def get(self, request, pk):
        anexo = get_object_or_404(Anexo.objects.select_related('chamado'), pk=pk)
        if not chamados_visiveis(request.user).filter(pk=anexo.chamado_id).exists():
            raise PermissionDenied
        try:
            arquivo = anexo.arquivo.open('rb')
        except FileNotFoundError:
            raise Http404('Arquivo não encontrado.')
        return FileResponse(arquivo, as_attachment=not anexo.eh_imagem, filename=anexo.nome_original or anexo.arquivo.name)


class AnexoExcluir(PermissaoRequiredMixin, View):
    permissao = 'chamados_gerenciar'
    def post(self, request, pk):
        anexo = get_object_or_404(Anexo, pk=pk)
        chamado_pk = anexo.chamado_id
        anexo.delete()
        messages.success(request, 'Arquivo removido.')
        return redirect('chamado_detalhe', pk=chamado_pk)


class Cancelar(PermissaoRequiredMixin, View):
    permissao = 'chamados_ver'
    def post(self, request, pk):
        chamado = get_object_or_404(chamados_visiveis(request.user), pk=pk)
        if not pode_cancelar(request.user, chamado):
            raise PermissionDenied
        chamado.status = Chamado.Status.CANCELADO
        chamado.save(update_fields=['status', 'atualizado_em'])
        registrar(chamado, request.user, 'Chamado cancelado.')
        messages.success(request, 'Chamado cancelado.')
        return redirect('chamado_detalhe', pk=chamado.pk)
