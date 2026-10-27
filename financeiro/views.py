from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, UpdateView

from cadastros.models import Locatario
from core.permissions import PermissaoRequiredMixin

from . import servicos
from .calculo import ErroRateio, calcular_rateio
from .forms import (AluguelGerarForm, CobrancaEditarForm, CobrancaPagarForm, CompetenciaNovaForm,
                    DespesaMarcacaoForm)
from .importacao import ErroImportacao, importar, ler_planilha
from .models import Cobranca, Competencia, Despesa, Rateio

DespesaFormSet = modelformset_factory(Despesa, form=DespesaMarcacaoForm, extra=0)


class CompetenciaLista(PermissaoRequiredMixin, ListView):
    permissao = 'financeiro_preparar'
    model = Competencia
    template_name = 'financeiro/competencia_lista.html'
    context_object_name = 'competencias'


class CompetenciaNova(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def get(self, request):
        return render(request, 'financeiro/competencia_nova.html', {'form': CompetenciaNovaForm()})

    def post(self, request):
        form = CompetenciaNovaForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                linhas = ler_planilha(form.cleaned_data['arquivo'].read())
            except ErroImportacao as erro:
                form.add_error('arquivo', str(erro))
            else:
                competencia, _ = Competencia.objects.get_or_create(referencia=form.cleaned_data['mes'])
                if not competencia.editavel:
                    form.add_error('mes', 'Esta competência já foi aprovada e não aceita novas despesas.')
                else:
                    resumo = importar(competencia, linhas)
                    if competencia.status == Competencia.Status.CALCULADA and resumo['novas']:
                        Rateio.objects.filter(competencia=competencia).delete()
                        competencia.status = Competencia.Status.ABERTA
                        competencia.save(update_fields=['status'])
                    messages.success(
                        request,
                        f"{resumo['novas']} despesas importadas, {resumo['repetidas']} já existiam, "
                        f"{resumo['fora_do_mes']} fora do mês foram ignoradas.")
                    return redirect('competencia_detalhe', pk=competencia.pk)
        return render(request, 'financeiro/competencia_nova.html', {'form': form})


class CompetenciaDetalhe(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'
    def get(self, request, pk):
        competencia = get_object_or_404(Competencia, pk=pk)
        return self.renderizar(request, competencia)

    @staticmethod
    def renderizar(request, competencia, formset=None):
        despesas = competencia.despesas.all()
        if formset is None:
            formset = DespesaFormSet(queryset=despesas)
        totais = competencia.total_por_tipo()
        excluidas = competencia.despesas.filter(entra_no_rateio=False).aggregate(t=Sum('valor'))['t'] or Decimal('0')
        rateio = getattr(competencia, 'rateio', None) if competencia.status != Competencia.Status.ABERTA else None
        return render(request, 'financeiro/competencia_detalhe.html', {
            'competencia': competencia,
            'formset': formset,
            'totais': totais,
            'total_rateado': sum(totais.values()),
            'excluidas': excluidas,
            'rateio': rateio,
            'itens': rateio.itens.select_related('unidade', 'locatario') if rateio else [],
            'multas': competencia.despesas.aggregate(t=Sum('multa_juros'))['t'] or Decimal('0'),
        })


class DespesasSalvar(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'
    def post(self, request, pk):
        competencia = get_object_or_404(Competencia, pk=pk)
        if not competencia.editavel:
            raise PermissionDenied
        formset = DespesaFormSet(request.POST, queryset=competencia.despesas.all())
        if formset.is_valid():
            formset.save()
            if competencia.status == Competencia.Status.CALCULADA:
                Rateio.objects.filter(competencia=competencia).delete()
                competencia.status = Competencia.Status.ABERTA
                competencia.save(update_fields=['status'])
                messages.info(request, 'As marcações mudaram: calcule o rateio de novo.')
            messages.success(request, 'Marcações salvas.')
            return redirect('competencia_detalhe', pk=competencia.pk)
        return CompetenciaDetalhe.renderizar(request, competencia, formset)


class RateioCalcular(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'
    def post(self, request, pk):
        competencia = get_object_or_404(Competencia, pk=pk)
        try:
            calcular_rateio(competencia)
            messages.success(request, 'Rateio calculado. Confira e aprove para gerar as cobranças.')
        except ErroRateio as erro:
            messages.error(request, str(erro))
        return redirect('competencia_detalhe', pk=competencia.pk)


class RateioAprovar(PermissaoRequiredMixin, View):
    permissao = 'rateio_aprovar'
    def post(self, request, pk):
        competencia = get_object_or_404(Competencia, pk=pk)
        try:
            resumo = servicos.aprovar_rateio(competencia, request.user)
        except ErroRateio as erro:
            messages.error(request, str(erro))
        else:
            texto = f"Rateio aprovado. {resumo['criadas']} cobranças de condomínio criadas."
            if resumo['sem_contrato']:
                nomes = ', '.join(str(i.unidade) for i in resumo['sem_contrato'])
                texto += f' Sem cobrança (sem contrato vigente, custo da Play): {nomes}.'
            messages.success(request, texto)
        return redirect('competencia_detalhe', pk=competencia.pk)


# ---- Cobranças -------------------------------------------------------------

class CobrancaAcessoMixin(LoginRequiredMixin):
    """Síndico vê tudo; o responsável da unidade vê as cobranças da própria unidade."""

    def dispatch(self, request, *args, **kwargs):
        usuario = request.user
        if usuario.is_authenticated and not usuario.pode('cobrancas_ver_todas', 'cobrancas_ver_proprias'):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class CobrancaLista(CobrancaAcessoMixin, ListView):
    template_name = 'financeiro/cobranca_lista.html'
    context_object_name = 'cobrancas'

    def get_queryset(self):
        usuario = self.request.user
        consulta = Cobranca.objects.select_related('unidade', 'locatario')
        if not usuario.pode('cobrancas_ver_todas'):
            consulta = consulta.filter(unidade_id__in=usuario.unidades_ids()).exclude(status=Cobranca.Status.CANCELADA)
        situacao = self.request.GET.get('situacao', '')
        hoje = timezone.localdate()
        if situacao == 'atrasada':
            consulta = consulta.filter(status=Cobranca.Status.ABERTA, vencimento__lt=hoje)
        elif situacao == 'aberta':
            consulta = consulta.filter(status=Cobranca.Status.ABERTA)
        elif situacao in (Cobranca.Status.PAGA, Cobranca.Status.CANCELADA):
            consulta = consulta.filter(status=situacao)
        tipo = self.request.GET.get('tipo', '')
        if tipo in Cobranca.Tipo.values:
            consulta = consulta.filter(tipo=tipo)
        mes = self.request.GET.get('mes', '')
        if len(mes) == 7 and mes[4] == '-' and mes[:4].isdigit() and mes[5:].isdigit():
            consulta = consulta.filter(referencia__year=int(mes[:4]), referencia__month=int(mes[5:]))
        empresa = self.request.GET.get('empresa', '')
        if empresa.isdigit():
            consulta = consulta.filter(locatario_id=int(empresa))
        return consulta

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        consulta = contexto['cobrancas']
        hoje = timezone.localdate()
        abertas = consulta.filter(status=Cobranca.Status.ABERTA)
        contexto.update(
            total_aberto=abertas.aggregate(t=Sum('valor'))['t'] or Decimal('0'),
            total_atrasado=abertas.filter(vencimento__lt=hoje).aggregate(t=Sum('valor'))['t'] or Decimal('0'),
            filtros=dict(
                situacao=self.request.GET.get('situacao', ''), tipo=self.request.GET.get('tipo', ''),
                mes=self.request.GET.get('mes', ''), empresa=self.request.GET.get('empresa', ''),
            ),
            situacoes=[('aberta', 'Em aberto'), ('atrasada', 'Atrasadas'), ('paga', 'Pagas'), ('cancelada', 'Canceladas')],
            tipos=Cobranca.Tipo.choices,
            empresas=Locatario.objects.filter(ativo=True) if self.request.user.pode('cobrancas_ver_todas') else [],
        )
        return contexto


class AluguelGerar(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def get(self, request):
        form = AluguelGerarForm(initial={'mes': timezone.localdate().strftime('%Y-%m')})
        return render(request, 'financeiro/aluguel_gerar.html', {'form': form})

    def post(self, request):
        form = AluguelGerarForm(request.POST)
        if form.is_valid():
            resumo = servicos.gerar_alugueis(form.cleaned_data['mes'])
            messages.success(
                request, f"{resumo['criadas']} cobranças de aluguel criadas, {resumo['ja_existiam']} já existiam.")
            return redirect('cobranca_lista')
        return render(request, 'financeiro/aluguel_gerar.html', {'form': form})


class CobrancaPagar(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'
    def get(self, request, pk):
        cobranca = get_object_or_404(Cobranca, pk=pk, status=Cobranca.Status.ABERTA)
        return render(request, 'form.html', {
            'titulo': f'Registrar pagamento — {cobranca}', 'form': CobrancaPagarForm(cobranca=cobranca)})

    def post(self, request, pk):
        cobranca = get_object_or_404(Cobranca, pk=pk, status=Cobranca.Status.ABERTA)
        form = CobrancaPagarForm(request.POST, cobranca=cobranca)
        if form.is_valid():
            cobranca.status = Cobranca.Status.PAGA
            cobranca.pago_em = form.cleaned_data['pago_em']
            cobranca.valor_pago = form.cleaned_data['valor_pago']
            cobranca.save(update_fields=['status', 'pago_em', 'valor_pago'])
            messages.success(request, 'Pagamento registrado.')
            return redirect('cobranca_lista')
        return render(request, 'form.html', {'titulo': f'Registrar pagamento — {cobranca}', 'form': form})


class CobrancaCancelar(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'
    def post(self, request, pk):
        cobranca = get_object_or_404(Cobranca, pk=pk, status=Cobranca.Status.ABERTA)
        cobranca.status = Cobranca.Status.CANCELADA
        cobranca.save(update_fields=['status'])
        messages.success(request, 'Cobrança cancelada.')
        return redirect('cobranca_lista')


class CobrancaEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'financeiro_preparar'
    model = Cobranca
    form_class = CobrancaEditarForm
    template_name = 'form.html'
    extra_context = {'titulo': 'Editar cobrança'}

    def get_queryset(self):
        return Cobranca.objects.filter(status=Cobranca.Status.ABERTA)

    def get_success_url(self):
        return reverse('cobranca_lista')

    def form_valid(self, form):
        messages.success(self.request, 'Cobrança atualizada.')
        return super().form_valid(form)
