# Corpus legal piloto (`co`)

El gate legal no afirma una norma que no exista en `legal_rules`. Este directorio
es el punto de entrada para cargar el corpus oficial.

## Formato

Cada archivo `.json` contiene una lista de objetos o `{ "rules": [...] }`:

```json
[
  {
    "jurisdiction": "co",
    "source": "Código Civil",
    "identifier": "123",
    "title": "Artículo 123",
    "text": "Texto oficial de la norma.",
    "version_date": "1873-05-26",
    "official_url": "https://fuente-oficial.example/...",
    "checksum": "sha256 del texto"
  }
]
```

## Reglas de carga

1. Usar la fuente oficial publicada por el Estado colombiano.
2. No inventar ni completar artículos con memoria del modelo.
3. Si un artículo no está en el corpus, el gate bloquea la conclusión jurídica.
4. El `checksum` es obligatorio cuando se carga `text`.
5. El importador no descarga nada en runtime.

## Comando

```bash
python scripts/import_legal_rules.py --dir data/legal_rules/co --organization-id <uuid>
```

El corpus mínimo debe cubrir las normas que citan los procesos piloto:
Código Civil, Código General del Proceso (Ley 1564 de 2012) y las normas
sustantivas/procesales que aparezcan en los escritos. Ampliarlo es una tarea de
revisión legal, no de scraping automático.
