from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.bloqueio import LoginAdminForm, LoginForm
from core.views import InicioView

admin.site.login_form = LoginAdminForm

# Os arquivos enviados (contratos etc.) não têm endereço público: são entregues por views
# que conferem a permissão de quem pede.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('entrar/', auth_views.LoginView.as_view(authentication_form=LoginForm), name='login'),
    path('sair/', auth_views.LogoutView.as_view(), name='logout'),
    path('', InicioView.as_view(), name='inicio'),
    path('', include('core.urls')),
    path('cadastros/', include('cadastros.urls')),
    path('contratos/', include('contratos.urls')),
    path('financeiro/', include('financeiro.urls')),
    path('chamados/', include('chamados.urls')),
    path('correspondencias/', include('correspondencias.urls')),
    path('relatorios/', include('relatorios.urls')),
    path('mural/', include('contatos.urls')),
]
