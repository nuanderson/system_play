from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class SindicoRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Só o síndico acessa."""

    def test_func(self):
        return self.request.user.e_sindico


class EquipeRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Síndico e equipe do prédio acessam."""

    def test_func(self):
        return self.request.user.e_equipe
