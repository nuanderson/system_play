from django.urls import path

from . import views

urlpatterns = [
    path('unidades/', views.UnidadeLista.as_view(), name='unidade_lista'),
    path('unidades/nova/', views.UnidadeCriar.as_view(), name='unidade_criar'),
    path('unidades/<int:pk>/editar/', views.UnidadeEditar.as_view(), name='unidade_editar'),
    path('empresas/', views.LocatarioLista.as_view(), name='locatario_lista'),
    path('empresas/nova/', views.LocatarioCriar.as_view(), name='locatario_criar'),
    path('empresas/<int:pk>/editar/', views.LocatarioEditar.as_view(), name='locatario_editar'),
]
