from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.generic import TemplateView

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from contratos.models import DIAS_ALERTA_VENCIMENTO, Contrato
from core.models import Usuario
from correspondencias.models import DIAS_ALERTA, Correspondencia
from financeiro.models import Cobranca, Competencia

SINDICO = 'sindico'
EQUIPE = 'equipe'
LOCATARIO = 'locatario'
MEMBRO = 'membro'
TODOS = {SINDICO, EQUIPE, LOCATARIO, MEMBRO}

ALTO, MEDIO, INFO = 'alto', 'medio', 'info'


def plural(n, singular, plural_):
    return f'{n} {singular if n == 1 else plural_}'


def reais(valor):
    return 'R$ ' + number_format(valor or 0, 2, force_grouping=True)


def alertas(usuario):
    """O que precisa de ação agora. Só entra na lista o que tem pelo menos um item."""
    lista = []

    def adicionar(nivel, texto, url):
        lista.append(dict(nivel=nivel, texto=texto, url=url))

    if usuario.e_sindico:
        atrasadas = Cobranca.atrasadas()
        n = atrasadas.count()
        if n:
            total = atrasadas.aggregate(t=Sum('valor'))['t']
            adicionar(ALTO, f'{plural(n, "cobrança atrasada", "cobranças atrasadas")} · {reais(total)}',
                      reverse('cobranca_lista') + '?situacao=atrasada')
        vencidos = Contrato.vigentes().filter(data_fim__lt=timezone.localdate()).count()
        if vencidos:
            adicionar(ALTO, plural(vencidos, 'contrato vencido', 'contratos vencidos'), reverse('contrato_lista') + '?status=vigente')
        a_vencer = Contrato.a_vencer().count() - vencidos
        if a_vencer > 0:
            adicionar(MEDIO, f'{plural(a_vencer, "contrato vence", "contratos vencem")} em até {DIAS_ALERTA_VENCIMENTO} dias',
                      reverse('contrato_lista') + '?status=vigente')
    if usuario.e_equipe:
        urgentes = Chamado.abertos().filter(prioridade=Chamado.Prioridade.URGENTE).count()
        if urgentes:
            adicionar(ALTO, plural(urgentes, 'chamado urgente em aberto', 'chamados urgentes em aberto'),
                      reverse('chamado_lista') + f'?prioridade={Chamado.Prioridade.URGENTE.value}')
        paradas = Correspondencia.paradas().count()
        if paradas:
            adicionar(MEDIO, f'{plural(paradas, "correspondência parada", "correspondências paradas")} há {DIAS_ALERTA} dias ou mais',
                      reverse('correspondencia_lista'))
    elif usuario.unidade_id:
        if usuario.perfil == Usuario.Perfil.LOCATARIO:
            atrasadas = Cobranca.atrasadas().filter(unidade_id=usuario.unidade_id)
            n = atrasadas.count()
            if n:
                total = atrasadas.aggregate(t=Sum('valor'))['t']
                adicionar(ALTO, f'{plural(n, "cobrança atrasada", "cobranças atrasadas")} · {reais(total)}',
                          reverse('cobranca_lista') + '?situacao=atrasada')
        esperando = Correspondencia.pendentes().filter(unidade_id=usuario.unidade_id).count()
        if esperando:
            adicionar(INFO, plural(esperando, 'correspondência esperando por você', 'correspondências esperando por você'),
                      reverse('correspondencia_lista'))
    return lista


def resumos(usuario):
    """Uma linha de número para cada módulo, mostrada dentro do próprio cartão."""
    dados = {}
    if usuario.e_sindico:
        ocupadas = Unidade.objects.filter(ativa=True, locatario__isnull=False).count()
        dados['Unidades'] = f'{plural(Unidade.objects.filter(ativa=True).count(), "unidade", "unidades")} · {ocupadas} com locatário'
        dados['Locatários'] = plural(Locatario.objects.filter(ativo=True).count(), 'ativo', 'ativos')
        dados['Usuários e acessos'] = plural(Usuario.objects.filter(is_active=True).count(), 'usuário ativo', 'usuários ativos')
        ultima = Competencia.objects.first()
        dados['Condomínio e rateio'] = (
            f'Último mês: {ultima} · {ultima.get_status_display().lower()}' if ultima else 'Nenhum mês importado')
        dados['Contratos'] = plural(Contrato.vigentes().count(), 'vigente', 'vigentes')
        aberto = Cobranca.em_aberto().aggregate(t=Sum('valor'))['t']
        dados['Cobranças'] = f'{reais(aberto)} em aberto'
    elif usuario.perfil == Usuario.Perfil.LOCATARIO and usuario.unidade_id:
        dados['Contratos'] = plural(Contrato.vigentes().filter(unidade_id=usuario.unidade_id).count(), 'vigente', 'vigentes')
        aberto = Cobranca.em_aberto().filter(unidade_id=usuario.unidade_id).aggregate(t=Sum('valor'))['t']
        dados['Cobranças'] = f'{reais(aberto)} em aberto'

    chamados = Chamado.abertos()
    correspondencias = Correspondencia.pendentes()
    if not usuario.e_equipe:
        chamados = chamados.filter(unidade_id=usuario.unidade_id) if usuario.unidade_id else chamados.none()
        correspondencias = (correspondencias.filter(unidade_id=usuario.unidade_id)
                            if usuario.unidade_id else correspondencias.none())
    dados['Chamados de manutenção'] = f'{chamados.count()} em aberto'
    dados['Correspondências'] = f'{correspondencias.count()} aguardando retirada'
    return dados


def modulos(usuario):
    secoes = [
        ('Cadastros', [
            dict(titulo='Unidades', texto='Andares, salas e as áreas usadas no rateio', icone='🏢', cor='azul',
                 url=reverse('unidade_lista'), perfis={SINDICO}),
            dict(titulo='Locatários', texto='Empresas, contatos e e-mails de cobrança', icone='🤝', cor='ciano',
                 url=reverse('locatario_lista'), perfis={SINDICO}),
            dict(titulo='Usuários e acessos', texto='Quem acessa o sistema e com qual perfil', icone='🔑', cor='marinho',
                 url=reverse('admin:core_usuario_changelist'), perfis={SINDICO}),
        ]),
        ('Financeiro', [
            dict(titulo='Contratos', texto='Vigência, aluguel, reajuste e documentos', icone='📄', cor='azul',
                 url=reverse('contrato_lista'), perfis={SINDICO, LOCATARIO}),
            dict(titulo='Condomínio e rateio', texto='Despesas do mês, rateio e aprovação', icone='🧮', cor='ciano',
                 url=reverse('competencia_lista'), perfis={SINDICO}),
            dict(titulo='Cobranças', texto='Aluguel e condomínio por unidade', icone='💳', cor='marinho',
                 url=reverse('cobranca_lista'), perfis={SINDICO, LOCATARIO}),
        ]),
        ('Operação', [
            dict(titulo='Chamados de manutenção', texto='Abertura, acompanhamento e custos', icone='🛠️', cor='azul',
                 url=reverse('chamado_lista'), perfis=TODOS),
            dict(titulo='Correspondências', texto='Entrada, aviso e retirada', icone='📬', cor='ciano',
                 url=reverse('correspondencia_lista'), perfis=TODOS),
        ]),
        ('Relatórios', [
            dict(titulo='Relatórios', texto='Despesas, inadimplência, chamados e contratos', icone='📊', cor='marinho',
                 url=reverse('relatorios'), perfis={SINDICO}),
        ]),
    ]
    numeros = resumos(usuario)
    resultado = []
    for nome, itens in secoes:
        visiveis = [dict(i, resumo=numeros.get(i['titulo'], '')) for i in itens if usuario.perfil in i['perfis']]
        if visiveis:
            resultado.append((nome, visiveis))
    return resultado


class InicioView(LoginRequiredMixin, TemplateView):
    template_name = 'inicio.html'

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        usuario = self.request.user
        agora = timezone.localtime()
        if agora.hour < 12:
            saudacao = 'Bom dia'
        elif agora.hour < 18:
            saudacao = 'Boa tarde'
        else:
            saudacao = 'Boa noite'
        contexto.update(agora=agora, saudacao=saudacao, secoes=modulos(usuario), alertas=alertas(usuario))
        return contexto
