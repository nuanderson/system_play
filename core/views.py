from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.generic import TemplateView

from cadastros.models import Locatario, Unidade
from chamados.models import Chamado
from contatos.models import Contato
from contratos.models import DIAS_ALERTA_VENCIMENTO, Contrato
from core.escopo import q_escopo
from core.models import Usuario
from core.usuarios import pessoas_da_empresa
from correspondencias.models import DIAS_ALERTA, Correspondencia
from financeiro.models import Cobranca, Competencia

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

    def cobrancas_atrasadas(consulta):
        n = consulta.count()
        if n:
            total = consulta.aggregate(t=Sum('valor'))['t']
            adicionar(ALTO, f'{plural(n, "cobrança atrasada", "cobranças atrasadas")} · {reais(total)}',
                      reverse('cobranca_lista') + '?situacao=atrasada')

    if usuario.pode('cobrancas_ver_todas'):
        cobrancas_atrasadas(Cobranca.atrasadas())
    elif usuario.pode('cobrancas_ver_proprias') and usuario.tem_escopo:
        cobrancas_atrasadas(Cobranca.atrasadas().filter(q_escopo(usuario)))

    if usuario.pode('contratos_ver_todos'):
        vencidos = Contrato.vigentes().filter(data_fim__lt=timezone.localdate()).count()
        if vencidos:
            adicionar(ALTO, plural(vencidos, 'contrato vencido', 'contratos vencidos'), reverse('contrato_lista') + '?status=vigente')
        a_vencer = Contrato.a_vencer().count() - vencidos
        if a_vencer > 0:
            adicionar(MEDIO, f'{plural(a_vencer, "contrato vence", "contratos vencem")} em até {DIAS_ALERTA_VENCIMENTO} dias',
                      reverse('contrato_lista') + '?status=vigente')

    if usuario.pode('chamados_gerenciar'):
        urgentes = Chamado.abertos().filter(prioridade=Chamado.Prioridade.URGENTE).count()
        if urgentes:
            adicionar(ALTO, plural(urgentes, 'chamado urgente em aberto', 'chamados urgentes em aberto'),
                      reverse('chamado_lista') + f'?prioridade={Chamado.Prioridade.URGENTE.value}')

    if usuario.pode('correspondencias_gerenciar'):
        paradas = Correspondencia.paradas().count()
        if paradas:
            adicionar(MEDIO, f'{plural(paradas, "correspondência parada", "correspondências paradas")} há {DIAS_ALERTA} dias ou mais',
                      reverse('correspondencia_lista'))
    elif usuario.pode('correspondencias_ver_proprias') and usuario.tem_escopo:
        esperando = Correspondencia.pendentes().filter(q_escopo(usuario)).count()
        if esperando:
            adicionar(INFO, plural(esperando, 'correspondência esperando por você', 'correspondências esperando por você'),
                      reverse('correspondencia_lista'))
    return lista


def resumos(usuario):
    """Uma linha de número para cada módulo, mostrada dentro do próprio cartão."""
    dados = {}
    if usuario.pode('cadastros_ver'):
        ocupadas = Unidade.objects.filter(ativa=True, locatario__isnull=False).count()
        dados['Unidades'] = f'{plural(Unidade.objects.filter(ativa=True).count(), "unidade", "unidades")} · {ocupadas} com empresa'
        dados['Empresas'] = plural(Locatario.objects.filter(ativo=True).count(), 'ativo', 'ativos')
    if usuario.pode('usuarios_gerenciar'):
        dados['Usuários e acessos'] = plural(Usuario.objects.filter(is_active=True).count(), 'usuário ativo', 'usuários ativos')
    if usuario.pode('membros_gerenciar') and usuario.tem_escopo:
        ativos = pessoas_da_empresa(usuario).filter(is_active=True).count()
        dados['Pessoas da empresa'] = plural(ativos, 'pessoa com acesso', 'pessoas com acesso')
    if usuario.pode('financeiro_preparar'):
        ultima = Competencia.objects.first()
        dados['Condomínio e rateio'] = (
            f'Último mês: {ultima} · {ultima.get_status_display().lower()}' if ultima else 'Nenhum mês importado')

    if usuario.pode('contratos_ver_todos'):
        dados['Contratos'] = plural(Contrato.vigentes().count(), 'vigente', 'vigentes')
    elif usuario.pode('contratos_ver_proprio'):
        dados['Contratos'] = plural(Contrato.vigentes().filter(unidade_id__in=usuario.unidades_ids()).count(), 'vigente', 'vigentes')
    if usuario.pode('cobrancas_ver_todas'):
        dados['Cobranças'] = f'{reais(Cobranca.em_aberto().aggregate(t=Sum("valor"))["t"])} em aberto'
    elif usuario.pode('cobrancas_ver_proprias'):
        aberto = Cobranca.em_aberto().filter(q_escopo(usuario)).aggregate(t=Sum('valor'))['t']
        dados['Cobranças'] = f'{reais(aberto)} em aberto'

    if usuario.pode('chamados_ver'):
        chamados = Chamado.abertos()
        if not usuario.pode('chamados_gerenciar'):
            chamados = chamados.filter(q_escopo(usuario)) if usuario.tem_escopo else chamados.none()
        dados['Chamados de manutenção'] = f'{chamados.count()} em aberto'
    if usuario.pode('correspondencias_gerenciar', 'correspondencias_ver_proprias'):
        correspondencias = Correspondencia.pendentes()
        if not usuario.pode('correspondencias_gerenciar'):
            correspondencias = correspondencias.filter(q_escopo(usuario)) if usuario.tem_escopo else correspondencias.none()
        dados['Correspondências'] = f'{correspondencias.count()} aguardando retirada'
    dados['Mural de contatos'] = plural(Contato.objects.count(), 'contato', 'contatos')
    return dados


# Cada módulo aparece para quem tem pelo menos uma das permissões listadas (ver core/acesso.py).
MODULOS = [
    ('Cadastros', [
        dict(titulo='Unidades', texto='Andares, salas e as áreas usadas no rateio', icone='🏢', cor='azul',
             rota='unidade_lista', permissoes=['cadastros_ver']),
        dict(titulo='Empresas', texto='Quem ocupa o prédio, contatos e as unidades de cada uma', icone='🤝', cor='ciano',
             rota='locatario_lista', permissoes=['cadastros_ver']),
        dict(titulo='Usuários e acessos', texto='Quem acessa o sistema e com qual perfil', icone='🔑', cor='marinho',
             rota='usuarios', permissoes=['usuarios_gerenciar']),
        dict(titulo='Pessoas da empresa', texto='Quem da sua empresa pode entrar no sistema', icone='👥', cor='marinho',
             rota='membros', permissoes=['membros_gerenciar']),
    ]),
    ('Financeiro', [
        dict(titulo='Contratos', texto='Vigência, aluguel, reajuste e documentos', icone='📄', cor='azul',
             rota='contrato_lista', permissoes=['contratos_ver_todos', 'contratos_ver_proprio']),
        dict(titulo='Condomínio e rateio', texto='Despesas do mês, rateio e aprovação', icone='🧮', cor='ciano',
             rota='competencia_lista', permissoes=['financeiro_preparar']),
        dict(titulo='Cobranças', texto='Aluguel e condomínio por unidade', icone='💳', cor='marinho',
             rota='cobranca_lista', permissoes=['cobrancas_ver_todas', 'cobrancas_ver_proprias']),
    ]),
    ('Operação', [
        dict(titulo='Chamados de manutenção', texto='Abertura, acompanhamento e custos', icone='🛠️', cor='azul',
             rota='chamado_lista', permissoes=['chamados_ver']),
        dict(titulo='Correspondências', texto='Entrada, aviso e retirada', icone='📬', cor='ciano',
             rota='correspondencia_lista', permissoes=['correspondencias_gerenciar', 'correspondencias_ver_proprias']),
        dict(titulo='Mural de contatos', texto='Telefones de eletricista, encanador e outros prestadores', icone='📌', cor='marinho',
             rota='mural', permissoes=['mural_ver']),
    ]),
    ('Relatórios', [
        dict(titulo='Relatórios', texto='Despesas, inadimplência, chamados e contratos', icone='📊', cor='marinho',
             rota='relatorios', permissoes=['relatorios_ver']),
    ]),
]


def modulos(usuario):
    numeros = resumos(usuario)
    resultado = []
    for nome, itens in MODULOS:
        visiveis = [
            dict(i, url=reverse(i['rota']), resumo=numeros.get(i['titulo'], ''))
            for i in itens if usuario.pode(*i['permissoes'])
        ]
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
