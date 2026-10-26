from django.shortcuts import redirect
from django.urls import reverse


class TrocarSenhaMiddleware:
    """Quem entrou com senha provisória só consegue trocar a senha (ou sair) antes de usar o sistema."""

    LIBERADOS = ('/static/', '/sair/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is not None and usuario.is_authenticated and usuario.trocar_senha:
            destino = reverse('trocar_senha')
            if request.path != destino and not request.path.startswith(self.LIBERADOS):
                return redirect(destino)
        return self.get_response(request)
