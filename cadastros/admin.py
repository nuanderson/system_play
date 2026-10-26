from django.contrib import admin

from .models import Locatario, Unidade


@admin.register(Locatario)
class LocatarioAdmin(admin.ModelAdmin):
    list_display = ('razao_social', 'cnpj', 'contato', 'email_cobranca', 'ativo')
    search_fields = ('razao_social', 'cnpj')
    list_filter = ('ativo',)


@admin.register(Unidade)
class UnidadeAdmin(admin.ModelAdmin):
    list_display = ('nome', 'tipo', 'andar', 'area_privativa', 'area_rateio', 'locatario', 'ativa')
    list_filter = ('tipo', 'ativa')
    search_fields = ('nome',)
