from django.urls import path

from . import views

urlpatterns = [
    path('', views.Indice.as_view(), name='relatorios'),
    path('despesas/', views.Despesas.as_view(), name='relatorio_despesas'),
    path('rateio/', views.Rateio.as_view(), name='relatorio_rateio'),
    path('inadimplencia/', views.Inadimplencia.as_view(), name='relatorio_inadimplencia'),
    path('chamados/', views.Chamados.as_view(), name='relatorio_chamados'),
    path('contratos/', views.Contratos.as_view(), name='relatorio_contratos'),
    path('correspondencias/', views.Correspondencias.as_view(), name='relatorio_correspondencias'),
]
