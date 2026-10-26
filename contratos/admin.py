from django.contrib import admin

from .models import Contrato, DocumentoContrato


class DocumentoInline(admin.TabularInline):
    model = DocumentoContrato
    extra = 0
    readonly_fields = ('enviado_em', 'enviado_por', 'nome_original')


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ('unidade', 'locatario', 'status', 'data_inicio', 'data_fim', 'valor_aluguel')
    list_filter = ('status',)
    search_fields = ('unidade__nome', 'locatario__razao_social')
    inlines = [DocumentoInline]
