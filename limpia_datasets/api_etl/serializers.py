from rest_framework import serializers
from .models import DiccionarioReferencia, TerminoValido, Famoso, Lugar, Georeferencia, Direccion

class DiccionarioReferenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiccionarioReferencia
        fields = '__all__'

class FamosoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Famoso
        fields = '__all__'

# Serializadores anidados para la estructura de la Parte 2.2
class GeoreferenciaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Georeferencia
        fields = ['latitud', 'longitud']

class DireccionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Direccion
        fields = ['id', 'nombre_calle', 'numero_calle', 'ciudad_estado_provincia', 'pais']
class LugarDetalleSerializer(serializers.ModelSerializer):
    georeferencia = GeoreferenciaSerializer(read_only=True)
    direccion = DireccionSerializer(read_only=True)

    class Meta:
        model = Lugar
        fields = ['id', 'nombre_lugar', 'georeferencia', 'direccion']