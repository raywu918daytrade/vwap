from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('health', views.health, name='health'),
    path('proxy/stream', views.proxy_stream, name='proxy_stream'),
    path('proxy/<path:api_path>', views.proxy_api, name='proxy_api'),
]
