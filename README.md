## Cómo ejecutar

```bash/cmd
cd backend (ruta de carpeta)
pip install -r requirements.txt (instalar complementos)
python3 app.py (ejecucion)
```

Abrir `http://localhost:8000`. En el primer arranque, si no existe `sst.db`, la app ejecuta
automáticamente el ETL completo + el motor de riesgo (ver `bootstrap()` en `app.py`).

Para forzar una re-carga completa desde cero:

```bash/cmd
rm backend/sst.db
python3 backend/app.py
```

