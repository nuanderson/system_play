from .acesso import Pode


def acesso(request):
    """Deixa `pode` disponível em todos os templates."""
    return {'pode': Pode(request.user)}
