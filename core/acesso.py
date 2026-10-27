"""Quem pode o quê. Esta é a única tabela de permissões do sistema.

Para mudar o acesso de um perfil, é só acrescentar ou tirar o perfil da permissão aqui.
Os perfis são strings para este arquivo não depender dos modelos.
"""

SINDICO = 'sindico'
ADMINISTRATIVO = 'administrativo'
EQUIPE = 'equipe'
CONSULTA = 'consulta'
LOCATARIO = 'locatario'
MEMBRO = 'membro'

TODOS = {SINDICO, ADMINISTRATIVO, EQUIPE, CONSULTA, LOCATARIO, MEMBRO}

PERMISSOES = {
    # Pessoas e acessos
    'usuarios_gerenciar': {SINDICO},               # cria, edita e desativa qualquer usuário
    'membros_gerenciar': {LOCATARIO},              # cria e desativa os membros da própria unidade

    # Cadastros
    'cadastros_ver': {SINDICO, ADMINISTRATIVO},    # listas de unidades e empresas
    'unidades_editar': {SINDICO},                  # mexe nas áreas que definem o rateio
    'locatarios_editar': {SINDICO, ADMINISTRATIVO},

    # Contratos
    'contratos_ver_todos': {SINDICO, ADMINISTRATIVO, CONSULTA},
    'contratos_ver_proprio': {LOCATARIO},
    'contratos_editar': {SINDICO, ADMINISTRATIVO},

    # Financeiro
    'cobrancas_ver_todas': {SINDICO, ADMINISTRATIVO, CONSULTA},
    'cobrancas_ver_proprias': {LOCATARIO},
    'financeiro_preparar': {SINDICO, ADMINISTRATIVO},  # importa, calcula, gera aluguel, dá baixa
    'rateio_aprovar': {SINDICO, ADMINISTRATIVO},       # libera as cobranças do mês

    # Relatórios
    'relatorios_ver': {SINDICO, ADMINISTRATIVO, CONSULTA},

    # Operação
    'chamados_ver': {SINDICO, EQUIPE, LOCATARIO, MEMBRO},
    'chamados_gerenciar': {SINDICO, EQUIPE},       # vê todos, muda situação, escreve notas internas
    'correspondencias_ver_proprias': {LOCATARIO, MEMBRO},
    'correspondencias_gerenciar': {SINDICO, ADMINISTRATIVO, EQUIPE},  # registra, dá baixa e devolve
    'mural_ver': TODOS,
    'mural_editar': {SINDICO, ADMINISTRATIVO, EQUIPE},
}


def tem_permissao(perfil, *permissoes):
    """Verdadeiro se o perfil tem pelo menos uma das permissões. Nome errado dá erro, não 'não'."""
    return any(perfil in PERMISSOES[nome] for nome in permissoes)


class Pode:
    """Para os modelos: `{% if pode.contratos_editar %}`. Nome de permissão inexistente dá erro."""

    def __init__(self, usuario):
        self._usuario = usuario

    def __getattr__(self, nome):
        if nome.startswith('_'):
            raise AttributeError(nome)
        if nome not in PERMISSOES:
            raise KeyError(f'Permissão desconhecida: {nome}')
        usuario = self._usuario
        return bool(usuario.is_authenticated and usuario.is_active and tem_permissao(usuario.perfil, nome))
