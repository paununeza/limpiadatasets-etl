from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.get_admin_urls() if hasattr(admin, 'get_admin_urls') else admin.site.urls),
    path('api/', include('api_etl.urls')),
]