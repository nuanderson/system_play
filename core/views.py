from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.generic import TemplateView

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from correspondencias.models import DIAS_ALERTA, Correspondencia
from contratos.models import DIAS_ALERTA_VENCIMENTO, Contrato
from financeiro.models import Cobranca

SINDICO = 'sindico'
EQUIPE = 'equipe'
LOCATARIO = 'locatario'
MEMBRO = 'membro'
TODOS = {SINDICO, EQUIPE, LOCATARIO, MEMBRO}


def modulos(usuario):
    """Módulos mostrados na tela inicial. `url` vazia = ainda não disponível."""
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
    resultado = []
    for nome, itens in secoes:
        visiveis = [i for i in itens if usuario.perfil in i['perfis']]
        if visiveis:
            resultado.append((nome, visiveis))
    return resultado


def indicadores_de_chamados():
    abertos = Chamado.abertos()
    return [
        (abertos.count(), 'chamados em aberto'),
        (abertos.filter(prioridade=Chamado.Prioridade.URGENTE).count(), 'chamados urgentes'),
        (Correspondencia.pendentes().count(), 'correspondências aguardando retirada'),
        (Correspondencia.paradas().count(), f'paradas há {DIAS_ALERTA} dias ou mais'),
    ]


def indicadores_da_unidade(usuario):
    return [
        (Chamado.abertos().filter(unidade_id=usuario.unidade_id).count(), 'chamados em aberto da sua unidade'),
        (Correspondencia.pendentes().filter(unidade_id=usuario.unidade_id).count(), 'correspondências esperando por você'),
    ]


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
        contexto.update(
            agora=agora,
            saudacao=saudacao,
            secoes=modulos(usuario),
        )
        if usuario.e_sindico:
            em_aberto = Cobranca.em_aberto().aggregate(t=Sum('valor'))['t'] or 0
            contexto['indicadores'] = [
                (Unidade.objects.filter(ativa=True).count(), 'unidades ativas'),
                (Locatario.objects.filter(ativo=True).count(), 'locatários ativos'),
                (Contrato.vigentes().count(), 'contratos vigentes'),
                (Contrato.a_vencer().count(), f'contratos vencem em até {DIAS_ALERTA_VENCIMENTO} dias'),
                ('R$ ' + number_format(em_aberto, 2, force_grouping=True), 'cobranças em aberto'),
                (Cobranca.atrasadas().count(), 'cobranças atrasadas'),
            ] + indicadores_de_chamados()
        elif usuario.e_equipe:
            contexto['indicadores'] = indicadores_de_chamados()
        elif usuario.unidade_id:
            contexto['indicadores'] = indicadores_da_unidade(usuario)
        return contexto
