from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin

from .acesso import PERMISSOES


class PermissaoRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Exige uma (ou qualquer uma de várias) permissões da tabela em core/acesso.py."""
    permissao = None  # nome ou lista de nomes

    def get_permissoes(self):
        permissoes = self.permissao
        if permissoes is None:
            raise NotImplementedError(f'{type(self).__name__} precisa definir `permissao`.')
        permissoes = (permissoes,) if isinstance(permissoes, str) else tuple(permissoes)
        for nome in permissoes:
            if nome not in PERMISSOES:
                raise KeyError(f'Permissão desconhecida: {nome}')
        return permissoes

    def test_func(self):
        return self.request.user.pode(*self.get_permissoes())
