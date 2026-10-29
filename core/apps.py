from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        from . import bloqueio  # noqa: F401  (liga os sinais da trava de login)
