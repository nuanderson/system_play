import os
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.forms import modelformset_factory
from django.core.mail import get_connection
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from cadastros.emails import separar_emails
from cadastros.models import Locatario, Unidade
from core.escopo import q_escopo
from core.permissions import PermissaoRequiredMixin

from . import envio, fatura, pix, servicos
from .calculo import ErroRateio, calcular_rateio
from .forms import (AluguelGerarForm, BoletoForm, CobrancaEditarForm, CobrancaPagarForm, CompetenciaNovaForm,
                    DespesaMarcacaoForm, EnviarCobrancaForm, EnviarTodasForm, TransferenciaEnergiaForm)
from .importacao import ErroImportacao, importar, ler_planilha
from .models import Cobranca, Competencia, Despesa, EnvioCobranca, Rateio, TransferenciaEnergia

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


def redistribuicao(itens):
    """Resumo da energia redistribuída neste cálculo (quem pagou menos, quanto saiu, quem recebeu), ou None."""
    saiu = [i for i in itens if i.energia_transferida < 0]
    entrou = [i for i in itens if i.energia_transferida > 0]
    if not saiu:
        return None
    return dict(saiu=saiu, entrou=entrou, total=sum(-i.energia_transferida for i in saiu),
                igual=len({i.energia_transferida for i in entrou}) == 1)


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
        itens = list(rateio.itens.select_related('unidade', 'locatario')) if rateio else []
        return render(request, 'financeiro/competencia_detalhe.html', {
            'competencia': competencia,
            'formset': formset,
            'totais': totais,
            'total_rateado': sum(totais.values()),
            'excluidas': excluidas,
            'rateio': rateio,
            'itens': itens,
            'redistribuicao': redistribuicao(itens),
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


class RateioReabrir(PermissaoRequiredMixin, View):
    """Destrava um mês aprovado para recalcular (só se não houver cobrança aberta ou paga dele)."""
    permissao = 'rateio_aprovar'

    def post(self, request, pk):
        competencia = get_object_or_404(Competencia, pk=pk)
        try:
            servicos.reabrir_rateio(competencia)
        except ErroRateio as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, 'Rateio reaberto. Recalcule, confira e aprove de novo.')
        return redirect('competencia_detalhe', pk=competencia.pk)


class RegrasRateio(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def get(self, request):
        return render(request, 'financeiro/regras_rateio.html', {
            'regras': TransferenciaEnergia.objects.select_related('origem'),
            'receptores': Unidade.objects.filter(ativa=True, recebe_energia_transferida=True).order_by('ordem'),
        })


class RegraEnergiaCriar(PermissaoRequiredMixin, CreateView):
    permissao = 'financeiro_preparar'
    model = TransferenciaEnergia
    form_class = TransferenciaEnergiaForm
    template_name = 'form.html'
    success_url = reverse_lazy('regras_rateio')
    extra_context = {'titulo': 'Nova redistribuição de energia'}

    def form_valid(self, form):
        messages.success(self.request, 'Regra cadastrada. Vale a partir do próximo cálculo do rateio.')
        return super().form_valid(form)


class RegraEnergiaEditar(PermissaoRequiredMixin, UpdateView):
    permissao = 'financeiro_preparar'
    model = TransferenciaEnergia
    form_class = TransferenciaEnergiaForm
    template_name = 'form.html'
    success_url = reverse_lazy('regras_rateio')
    extra_context = {'titulo': 'Editar redistribuição de energia'}

    def form_valid(self, form):
        messages.success(self.request, 'Regra atualizada. Vale a partir do próximo cálculo do rateio.')
        return super().form_valid(form)


class RegraEnergiaExcluir(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def post(self, request, pk):
        get_object_or_404(TransferenciaEnergia, pk=pk).delete()
        messages.success(request, 'Regra removida. Vale a partir do próximo cálculo do rateio.')
        return redirect('regras_rateio')


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


def cobrancas_visiveis(usuario):
    """Quem vê tudo vê tudo. A empresa vê as suas cobranças (e a de uma unidade só, se for restrita a ela)."""
    consulta = Cobranca.objects.select_related('unidade', 'locatario').prefetch_related('itens__unidade')
    if usuario.pode('cobrancas_ver_todas'):
        return consulta
    return consulta.filter(q_escopo(usuario)).exclude(status=Cobranca.Status.CANCELADA)


def filtrar_cobrancas(consulta, params):
    """Os filtros da lista (situação, tipo, mês e empresa), também usados pelo envio em lote."""
    situacao = params.get('situacao', '')
    hoje = timezone.localdate()
    if situacao == 'atrasada':
        consulta = consulta.filter(status=Cobranca.Status.ABERTA, vencimento__lt=hoje)
    elif situacao == 'aberta':
        consulta = consulta.filter(status=Cobranca.Status.ABERTA)
    elif situacao in (Cobranca.Status.PAGA, Cobranca.Status.CANCELADA):
        consulta = consulta.filter(status=situacao)
    tipo = params.get('tipo', '')
    if tipo in Cobranca.Tipo.values:
        consulta = consulta.filter(tipo=tipo)
    mes = params.get('mes', '')
    if len(mes) == 7 and mes[4] == '-' and mes[:4].isdigit() and mes[5:].isdigit():
        consulta = consulta.filter(referencia__year=int(mes[:4]), referencia__month=int(mes[5:]))
    empresa = params.get('empresa', '')
    if empresa.isdigit():
        consulta = consulta.filter(locatario_id=int(empresa))
    return consulta


class CobrancaLista(CobrancaAcessoMixin, ListView):
    template_name = 'financeiro/cobranca_lista.html'
    context_object_name = 'cobrancas'

    def get_queryset(self):
        return filtrar_cobrancas(cobrancas_visiveis(self.request.user), self.request.GET)

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
            consulta_texto=self.request.GET.urlencode(),
            email_configurado=envio.email_configurado(),
            empresas=Locatario.objects.filter(ativo=True) if self.request.user.pode('cobrancas_ver_todas') else [],
        )
        return contexto


class CobrancaDetalhe(CobrancaAcessoMixin, DetailView):
    template_name = 'financeiro/cobranca_detalhe.html'
    context_object_name = 'cobranca'

    def get_queryset(self):
        return cobrancas_visiveis(self.request.user)

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        codigo = pix.codigo_da_cobranca(self.object) if self.object.status == Cobranca.Status.ABERTA else None
        if codigo:
            contexto.update(pix_codigo=codigo, pix_qr=pix.qr_svg(codigo))
        if self.request.user.pode('financeiro_preparar'):
            cobranca = self.object
            contexto['form_boleto'] = BoletoForm()
            contexto['form_envio'] = EnviarCobrancaForm(initial=dict(
                para=cobranca.locatario.email_cobranca, anexar_boleto=bool(cobranca.boleto)))
            contexto['email_configurado'] = envio.email_configurado()
            contexto['envios'] = cobranca.envios.select_related('enviado_por')
        return contexto


class CobrancaEnviar(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def post(self, request, pk):
        cobranca = get_object_or_404(Cobranca.objects.exclude(status=Cobranca.Status.CANCELADA), pk=pk)
        form = EnviarCobrancaForm(request.POST)
        if not form.is_valid():
            for erros in form.errors.values():
                for erro in erros:
                    messages.error(request, erro)
            return redirect('cobranca_detalhe', pk=cobranca.pk)
        try:
            registro = envio.enviar_cobranca(
                cobranca, form.cleaned_data['para'], request.user, form.cleaned_data['mensagem'],
                form.cleaned_data['anexar_boleto'], form.cleaned_data['anexar_fatura'], form.cleaned_data['incluir_pix'])
        except envio.EmailNaoConfigurado as erro:
            messages.error(request, str(erro))
        else:
            if registro.ok:
                anexos = [nome for nome, tem in (('o boleto', registro.com_boleto), ('a fatura', registro.com_fatura),
                                                  ('o Pix', registro.com_pix)) if tem]
                messages.success(request, f'Cobrança enviada para {registro.para}'
                                          + (f' com {" e ".join(anexos)}.' if anexos else ' (sem anexos).'))
            else:
                messages.error(request, 'Não foi possível enviar. O motivo ficou registrado no histórico abaixo.')
        return redirect('cobranca_detalhe', pk=cobranca.pk)


class BoletoEnviar(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def post(self, request, pk):
        cobranca = get_object_or_404(Cobranca.objects.exclude(status=Cobranca.Status.CANCELADA), pk=pk)
        form = BoletoForm(request.POST, request.FILES)
        if form.is_valid():
            arquivo = form.cleaned_data['arquivo']
            if cobranca.boleto:
                cobranca.boleto.delete(save=False)
            cobranca.boleto = arquivo
            cobranca.boleto_nome = os.path.basename(arquivo.name)[:255]
            cobranca.boleto_enviado_em = timezone.now()
            cobranca.save(update_fields=['boleto', 'boleto_nome', 'boleto_enviado_em'])
            messages.success(request, 'Boleto salvo.')
        else:
            for erros in form.errors.values():
                for erro in erros:
                    messages.error(request, erro)
        return redirect('cobranca_detalhe', pk=cobranca.pk)


class CobrancaEnviarTodas(PermissaoRequiredMixin, View):
    """Envia por e-mail as cobranças em aberto da lista (com os mesmos filtros), depois de uma conferência."""
    permissao = 'financeiro_preparar'
    LIMITE = 50

    def separar(self, request, pular_enviadas):
        """Divide as cobranças em aberto da lista em: a enviar, sem e-mail e já enviadas."""
        consulta = (filtrar_cobrancas(cobrancas_visiveis(request.user), request.GET)
                    .filter(status=Cobranca.Status.ABERTA).order_by('locatario__razao_social', 'vencimento', 'tipo'))
        cobrancas = list(consulta)
        ja_enviadas = set(EnvioCobranca.objects.filter(ok=True, cobranca__in=cobrancas)
                          .values_list('cobranca_id', flat=True))
        a_enviar, sem_email, enviadas = [], [], []
        for c in cobrancas:
            c.emails = separar_emails(c.locatario.email_cobranca)
            if not c.emails:
                sem_email.append(c)
            elif pular_enviadas and c.pk in ja_enviadas:
                enviadas.append(c)
            else:
                a_enviar.append(c)
        return a_enviar, sem_email, enviadas

    def tela(self, request, form, pular_enviadas):
        a_enviar, sem_email, enviadas = self.separar(request, pular_enviadas)
        visiveis = a_enviar[:self.LIMITE]
        return render(request, 'financeiro/enviar_todas.html', dict(
            form=form, a_enviar=visiveis, excedente=max(0, len(a_enviar) - self.LIMITE),
            sem_email=sem_email, enviadas=enviadas, consulta_texto=request.GET.urlencode(),
            email_configurado=envio.email_configurado(), total_empresas=len({c.locatario_id for c in visiveis}),
            tem_boleto=any(c.boleto for c in visiveis), tem_pix=pix.configurado(),
        ))

    def get(self, request):
        return self.tela(request, EnviarTodasForm(), pular_enviadas=True)

    def post(self, request):
        form = EnviarTodasForm(request.POST)
        if not form.is_valid():
            return self.tela(request, form, pular_enviadas=True)
        dados = form.cleaned_data
        a_enviar, _, _ = self.separar(request, dados['pular_enviadas'])
        a_enviar = a_enviar[:self.LIMITE]
        voltar = f"{reverse('cobranca_lista')}?{request.GET.urlencode()}"
        if not a_enviar:
            messages.info(request, 'Nenhuma cobrança para enviar com esses filtros.')
            return redirect(voltar)
        conexao = get_connection()  # uma conexão só para todos, em vez de uma por e-mail
        feitos, falhas = 0, []
        try:
            for c in a_enviar:
                registro = envio.enviar_cobranca(
                    c, c.emails, request.user, dados['mensagem'], dados['anexar_boleto'], dados['anexar_fatura'],
                    dados['incluir_pix'], conexao=conexao)
                if registro.ok:
                    feitos += 1
                else:
                    falhas.append(f'{c.locatario} ({c.get_tipo_display().lower()}): {registro.erro}')
        except envio.EmailNaoConfigurado as erro:
            messages.error(request, str(erro))
        finally:
            try:
                conexao.close()
            except Exception:
                pass
        if feitos:
            messages.success(request, f'{feitos} cobrança{"s" if feitos > 1 else ""} '
                                      f'enviada{"s" if feitos > 1 else ""} por e-mail.')
        if falhas:
            messages.error(request, f'{len(falhas)} não saiu: ' + '; '.join(falhas)[:600])
        return redirect(voltar)


class FaturaBaixar(CobrancaAcessoMixin, View):
    """A fatura do condomínio em PDF, para quem pode ver a cobrança."""

    def get(self, request, pk):
        cobranca = get_object_or_404(cobrancas_visiveis(request.user), pk=pk)
        if not cobranca.fatura_disponivel:
            raise Http404('Só o condomínio tem fatura.')
        resposta = HttpResponse(fatura.gerar_pdf(cobranca), content_type='application/pdf')
        nome = slugify(cobranca.locatario.razao_social)[:40] or 'empresa'
        resposta['Content-Disposition'] = f'attachment; filename="fatura-{cobranca.referencia:%Y-%m}-{nome}.pdf"'
        return resposta


class BoletoBaixar(CobrancaAcessoMixin, View):
    """Entrega o boleto só a quem pode ver a cobrança. O arquivo nunca fica em endereço público."""

    def get(self, request, pk):
        cobranca = get_object_or_404(cobrancas_visiveis(request.user), pk=pk)
        if not cobranca.boleto:
            raise Http404('Esta cobrança não tem boleto.')
        try:
            arquivo = cobranca.boleto.open('rb')
        except FileNotFoundError:
            raise Http404('Arquivo não encontrado.')
        return FileResponse(arquivo, as_attachment=True, filename=cobranca.boleto_nome or cobranca.boleto.name)


class BoletoRemover(PermissaoRequiredMixin, View):
    permissao = 'financeiro_preparar'

    def post(self, request, pk):
        cobranca = get_object_or_404(Cobranca, pk=pk)
        if cobranca.boleto:
            cobranca.boleto.delete(save=False)
            cobranca.boleto = ''
            cobranca.boleto_nome = ''
            cobranca.boleto_enviado_em = None
            cobranca.save(update_fields=['boleto', 'boleto_nome', 'boleto_enviado_em'])
            messages.success(request, 'Boleto removido.')
        return redirect('cobranca_detalhe', pk=cobranca.pk)


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


class CobrancaReativar(PermissaoRequiredMixin, View):
    """Desfaz um cancelamento feito por engano. Recusa se já existe outra cobrança ativa no lugar dela."""
    permissao = 'financeiro_preparar'

    def post(self, request, pk):
        cobranca = get_object_or_404(Cobranca, pk=pk, status=Cobranca.Status.CANCELADA)
        outra = servicos.conflito_ao_reativar(cobranca)
        if outra:
            messages.error(request, f'Não dá para reativar: já existe outra cobrança ativa de {cobranca.get_tipo_display().lower()} '
                                    f'{cobranca.referencia:%m/%Y} para {outra.origem} (situação: {outra.get_status_display().lower()}).')
            return redirect('cobranca_detalhe', pk=cobranca.pk)
        cobranca.status = Cobranca.Status.ABERTA
        cobranca.save(update_fields=['status'])
        messages.success(request, 'Cobrança reativada. Ela voltou para "em aberto".')
        return redirect('cobranca_detalhe', pk=cobranca.pk)


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
