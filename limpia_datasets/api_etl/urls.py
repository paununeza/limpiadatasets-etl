from django.urls import path
from .views import ProcesarFamososView, ProcesarLugaresView, ProcesarComunasView, GuardarImagenFamosoView, SugerenciasComunaView

urlpatterns = [
    path('etl/comunas/', ProcesarComunasView.as_view(), name='etl_comunas'),
    path('etl/comunas/sugerencias/', SugerenciasComunaView.as_view(), name='sugerencias_comunas'),
    path('etl/famosos/', ProcesarFamososView.as_view(), name='etl_famosos'),
    path('etl/lugares/', ProcesarLugaresView.as_view(), name='etl_lugares'),
    path('etl/famosos/guardar-imagen/', GuardarImagenFamosoView.as_view(), name='guardar_imagen_famoso'),
]