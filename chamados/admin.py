from django.contrib import admin

from .models import Anexo, Chamado, Comentario


class ComentarioInline(admin.TabularInline):
    model = Comentario
    extra = 0


class AnexoInline(admin.TabularInline):
    model = Anexo
    extra = 0
    readonly_fields = ('enviado_em', 'enviado_por', 'nome_original')


@admin.register(Chamado)
class ChamadoAdmin(admin.ModelAdmin):
    list_display = ('numero', 'titulo', 'locatario', 'unidade', 'categoria', 'prioridade', 'status', 'aberto_em')
    list_filter = ('status', 'prioridade', 'categoria')
    search_fields = ('titulo', 'descricao')
    inlines = [ComentarioInline, AnexoInline]
