from django.contrib import admin

from .models import Correspondencia


@admin.register(Correspondencia)
class CorrespondenciaAdmin(admin.ModelAdmin):
    list_display = ('numero', 'locatario', 'unidade', 'tipo', 'destinatario', 'status', 'recebido_em')
    list_filter = ('status', 'tipo')
    search_fields = ('locatario__razao_social', 'destinatario', 'observacoes')
