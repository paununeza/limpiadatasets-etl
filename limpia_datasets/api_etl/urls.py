from django.urls import path
from .views import ProcesarFamososView, ProcesarLugaresView, ProcesarComunasView

urlpatterns = [
    path('etl/comunas/', ProcesarComunasView.as_view(), name='etl_comunas'),
    path('etl/famosos/', ProcesarFamososView.as_view(), name='etl_famosos'),
    path('etl/lugares/', ProcesarLugaresView.as_view(), name='etl_lugares'),
    
]