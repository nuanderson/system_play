from django.urls import path

from . import views

urlpatterns = [
    path('', views.CorrespondenciaLista.as_view(), name='correspondencia_lista'),
    path('nova/', views.CorrespondenciaCriar.as_view(), name='correspondencia_criar'),
    path('<int:pk>/', views.CorrespondenciaDetalhe.as_view(), name='correspondencia_detalhe'),
    path('<int:pk>/editar/', views.CorrespondenciaEditar.as_view(), name='correspondencia_editar'),
    path('<int:pk>/baixa/', views.DarBaixa.as_view(), name='correspondencia_baixa'),
    path('<int:pk>/devolver/', views.Devolver.as_view(), name='correspondencia_devolver'),
    path('<int:pk>/foto/', views.FotoVer.as_view(), name='correspondencia_foto'),
]
