from django.db import models

# =====================================================================
# MODULO BASE: DICCIONARIOS DINÁMICOS (Para Lógica Difusa)
# =====================================================================

class DiccionarioReferencia(models.Model):
    nombre = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField(blank=True, null=True)
    creado_el = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre

class TerminoValido(models.Model):
    diccionario = models.ForeignKey(DiccionarioReferencia, on_delete=models.CASCADE, related_name='terminos')
    valor_oficial = models.CharField(max_length=255)

    class Meta:
        unique_together = ('diccionario', 'valor_oficial')

    def __str__(self):
        return f"{self.valor_oficial} ({self.diccionario.nombre})"


# =====================================================================
# PARTE 2: MODELO PARA FAMOSOS
# =====================================================================

class Famoso(models.Model):
    nombre = models.CharField(max_length=255)
    fecha_nacimiento_original = models.CharField(max_length=100)
    fecha_nacimiento_chile = models.CharField(max_length=50) # Soporta "a.C." como texto
    edad = models.IntegerField()
    es_cumpleanos = models.BooleanField(default=False)
    procesado_el = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


# =====================================================================
# PARTE 3: MODELOS PARA LUGARES (Estructura de 3 Tablas Estrictas)
# =====================================================================

class Lugar(models.Model):
    nombre_lugar = models.CharField(max_length=255)

    def __str__(self):
        return self.nombre_lugar

class Georeferencia(models.Model):
    lugar = models.OneToOneField(Lugar, on_delete=models.CASCADE, related_name='georeferencia')
    latitud = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)

class Direccion(models.Model):
    # Relación con Lugar
    lugar = models.OneToOneField(Lugar, on_delete=models.CASCADE, related_name='direccion')
    
    # Atributos
    nombre_calle = models.CharField(max_length=255, null=True, blank=True)
    numero_calle = models.CharField(max_length=50, null=True, blank=True)
    ciudad_estado_provincia = models.CharField(max_length=255, null=True, blank=True)
    pais = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.nombre_calle} {self.numero_calle}, {self.pais}"