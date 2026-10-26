from django.urls import path

from . import views

urlpatterns = [
    path('', views.CompetenciaLista.as_view(), name='competencia_lista'),
    path('competencias/nova/', views.CompetenciaNova.as_view(), name='competencia_nova'),
    path('competencias/<int:pk>/', views.CompetenciaDetalhe.as_view(), name='competencia_detalhe'),
    path('competencias/<int:pk>/despesas/', views.DespesasSalvar.as_view(), name='despesas_salvar'),
    path('competencias/<int:pk>/calcular/', views.RateioCalcular.as_view(), name='rateio_calcular'),
    path('competencias/<int:pk>/aprovar/', views.RateioAprovar.as_view(), name='rateio_aprovar'),
    path('cobrancas/', views.CobrancaLista.as_view(), name='cobranca_lista'),
    path('cobrancas/aluguel/', views.AluguelGerar.as_view(), name='aluguel_gerar'),
    path('cobrancas/<int:pk>/pagar/', views.CobrancaPagar.as_view(), name='cobranca_pagar'),
    path('cobrancas/<int:pk>/cancelar/', views.CobrancaCancelar.as_view(), name='cobranca_cancelar'),
    path('cobrancas/<int:pk>/editar/', views.CobrancaEditar.as_view(), name='cobranca_editar'),
]
