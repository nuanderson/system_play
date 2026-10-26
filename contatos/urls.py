from django.urls import path

from . import views

urlpatterns = [
    path('', views.Mural.as_view(), name='mural'),
    path('novo/', views.ContatoCriar.as_view(), name='contato_criar'),
    path('<int:pk>/editar/', views.ContatoEditar.as_view(), name='contato_editar'),
    path('<int:pk>/excluir/', views.ContatoExcluir.as_view(), name='contato_excluir'),
]
