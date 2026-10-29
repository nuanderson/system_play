from django.urls import path

from . import views

urlpatterns = [
    path('unidades/', views.UnidadeLista.as_view(), name='unidade_lista'),
    path('unidades/nova/', views.UnidadeCriar.as_view(), name='unidade_criar'),
    path('unidades/<int:pk>/editar/', views.UnidadeEditar.as_view(), name='unidade_editar'),
    path('empresas/', views.LocatarioLista.as_view(), name='locatario_lista'),
    path('empresas/nova/', views.LocatarioCriar.as_view(), name='locatario_criar'),
    path('empresas/<int:pk>/editar/', views.LocatarioEditar.as_view(), name='locatario_editar'),
    path('empresas/<int:pk>/pessoas/', views.PessoaLista.as_view(), name='pessoa_lista'),
    path('empresas/<int:pk>/pessoas/nova/', views.PessoaCriar.as_view(), name='pessoa_criar'),
    path('pessoas-da-empresa/<int:pk>/editar/', views.PessoaEditar.as_view(), name='pessoa_editar'),
    path('pessoas-da-empresa/<int:pk>/remover/', views.PessoaRemover.as_view(), name='pessoa_remover'),
]
