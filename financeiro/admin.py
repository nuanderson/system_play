from django.contrib import admin

from .models import Cobranca, CobrancaItem, Competencia, Despesa, Rateio, RateioItem


class DespesaInline(admin.TabularInline):
    model = Despesa
    extra = 0
    fields = ('vencimento', 'descricao', 'valor', 'tipo', 'entra_no_rateio', 'motivo_exclusao')


@admin.register(Competencia)
class CompetenciaAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'status')
    inlines = [DespesaInline]


class RateioItemInline(admin.TabularInline):
    model = RateioItem
    extra = 0


@admin.register(Rateio)
class RateioAdmin(admin.ModelAdmin):
    list_display = ('competencia', 'calculado_em', 'aprovado_em')
    inlines = [RateioItemInline]


class CobrancaItemInline(admin.TabularInline):
    model = CobrancaItem
    extra = 0


@admin.register(Cobranca)
class CobrancaAdmin(admin.ModelAdmin):
    list_display = ('locatario', 'unidade', 'tipo', 'referencia', 'valor', 'vencimento', 'status')
    list_filter = ('tipo', 'status')
    inlines = [CobrancaItemInline]
