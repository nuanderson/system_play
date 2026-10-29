from django.urls import path

from . import views_usuarios as v

urlpatterns = [
    path('usuarios/', v.UsuarioLista.as_view(), name='usuarios'),
    path('usuarios/novo/', v.UsuarioCriar.as_view(), name='usuario_criar'),
    path('usuarios/<int:pk>/editar/', v.UsuarioEditar.as_view(), name='usuario_editar'),
    path('usuarios/<int:pk>/ativar/', v.UsuarioAlternar.as_view(), name='usuario_alternar'),
    path('usuarios/<int:pk>/desbloquear/', v.UsuarioDesbloquear.as_view(), name='usuario_desbloquear'),
    path('usuarios/<int:pk>/senha/', v.UsuarioNovaSenha.as_view(), name='usuario_senha'),
    path('pessoas/', v.MembroLista.as_view(), name='membros'),
    path('pessoas/nova/', v.MembroCriar.as_view(), name='membro_criar'),
    path('pessoas/<int:pk>/ativar/', v.MembroAlternar.as_view(), name='membro_alternar'),
    path('pessoas/<int:pk>/senha/', v.MembroNovaSenha.as_view(), name='membro_senha'),
    path('senha-provisoria/', v.SenhaProvisoria.as_view(), name='senha_provisoria'),
    path('conta/senha/', v.TrocarSenha.as_view(), name='trocar_senha'),
]
