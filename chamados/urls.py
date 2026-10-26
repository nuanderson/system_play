from django.urls import path

from . import views

urlpatterns = [
    path('', views.ChamadoLista.as_view(), name='chamado_lista'),
    path('novo/', views.ChamadoCriar.as_view(), name='chamado_criar'),
    path('<int:pk>/', views.ChamadoDetalhe.as_view(), name='chamado_detalhe'),
    path('<int:pk>/gerenciar/', views.Gerenciar.as_view(), name='chamado_gerenciar'),
    path('<int:pk>/comentar/', views.Comentar.as_view(), name='chamado_comentar'),
    path('<int:pk>/anexar/', views.AnexoEnviar.as_view(), name='chamado_anexar'),
    path('<int:pk>/cancelar/', views.Cancelar.as_view(), name='chamado_cancelar'),
    path('anexos/<int:pk>/ver/', views.AnexoBaixar.as_view(), name='anexo_baixar'),
    path('anexos/<int:pk>/excluir/', views.AnexoExcluir.as_view(), name='anexo_excluir'),
]
