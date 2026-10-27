from django.urls import path

from . import views

urlpatterns = [
    path('', views.ContratoLista.as_view(), name='contrato_lista'),
    path('novo/', views.ContratoCriar.as_view(), name='contrato_criar'),
    path('<int:pk>/', views.ContratoDetalhe.as_view(), name='contrato_detalhe'),
    path('<int:pk>/editar/', views.ContratoEditar.as_view(), name='contrato_editar'),
    path('<int:pk>/documentos/novo/', views.DocumentoEnviar.as_view(), name='documento_enviar'),
    path('<int:pk>/ajustes/novo/', views.AjusteCriar.as_view(), name='ajuste_criar'),
    path('ajustes/<int:pk>/excluir/', views.AjusteExcluir.as_view(), name='ajuste_excluir'),
    path('documentos/<int:pk>/baixar/', views.DocumentoBaixar.as_view(), name='documento_baixar'),
    path('documentos/<int:pk>/excluir/', views.DocumentoExcluir.as_view(), name='documento_excluir'),
]
