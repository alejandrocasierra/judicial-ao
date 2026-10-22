# Corpus legal (`co`)

El gate legal no afirma una norma que no exista en `legal_rules`. Este directorio
es el punto de entrada para cargar el corpus desde fuentes oficiales.

## Estado actual

- **135 normas** por organización (`270` filas en las dos organizaciones locales).
- **21 fuentes**: CGP/Ley 1564, Código Civil/Ley 57, Código de Comercio/Decreto
  410, Ley 1579 de 2012, Decreto 2723 de 2014, Estatuto Tributario/Decreto 624,
  Ley 527/1999, Ley 2213/2022, Ley 1437/2011 (CPACA), Ley 1394/2010, Ley
  962/2005, Ley 590/2000, Ley 1581/2012, Ley 270/1996, Decreto Ley 019/2012,
  Decreto 525/2009, Decreto 1069/2015, Decreto 650/1996, Decreto 2150/1995,
  Decreto 806/2020 y Decreto 960/1970.
- Manifiesto reproducible: `checksums.json`.
- Extracción: `scripts/build_legal_corpus.py` desde `secretariasenado.gov.co`.

## Formato

Cada archivo `.json` contiene una lista de objetos o `{ "rules": [...] }`:

```json
[
  {
    "jurisdiction": "co",
    "source": "Código Civil - Ley 57 de 1887",
    "identifier": "769",
    "title": "ARTÍCULO 769 - Código Civil (Ley 57 de 1887)",
    "text": "Texto oficial de la norma.",
    "version_date": "2026-10-15",
    "official_url": "http://www.secretariasenado.gov.co/senado/basedoc/codigo_civil_pr023.html",
    "checksum": "sha256 del texto",
    "metadata": {
      "source_page_updated": "2026-10-15",
      "retrieved_at": "2026-10-15T00:00:00+00:00",
      "article_anchor": "art-769",
      "extraction": "secretariasenado-basedoc/v1"
    }
  }
]
```

## Archivos

| Archivo | Contenido |
|---|---|
| `cgp.json` | CGP piloto original (31 filas, incluye una fila de ley). |
| `codigo_civil.json` | Código Civil piloto original (4 artículos). |
| `codigo_comercio.json` | Código de Comercio (3 artículos). |
| `codigo_comercio_adicional.json` | Alias `784-5` del artículo 784, numeral 5 (mismo texto oficial). |
| `ley_1579_2012.json` | Ley 1579 de 2012: artículos 3, 4, 8, 10–16, 22, 28, 31, 33, 34 y 59. |
| `decreto_2723_2014.json` | Decreto 2723 de 2014: artículos 21 y 22. |
| `estatuto_tributario.json` | Estatuto Tributario: 630, 839-1, 840 y alias 839-01. |
| `codigo_civil_adicional.json` | Código Civil: 769, 1602, 1617, 2432, 2438, 2478, 2499, 2500, 2535. |
| `cgp_adicional.json` | CGP: 28 disposiciones citadas en el expediente. |
| `ley_527_1999.json` | Ley 527/1999: artículos 7, 12 y 20. |
| `ley_2213_2022.json` | Ley 2213/2022: artículos 3, 9 y 10. |
| `ley_1437_2011.json` | CPACA: artículos 67, 76, 77, 79 y 87. |
| `ley_1394_2010.json` | Ley 1394/2010: artículos 6, 7 y 8. |
| `ley_962_2005.json` | Ley 962/2005: artículo 19 (actos de registro). |
| `ley_590_2000.json` | Ley 590/2000: artículos 2 y 43 (Mipymes y parafiscales). |
| `ley_1581_2012.json` | Ley 1581/2012: artículos 4 y 9 (datos personales). |
| `ley_270_1996.json` | Ley 270/1996: artículo 16 (salas). |
| `decreto_ley_019_2012.json` | Decreto Ley 019/2012: artículos 15 y 18. |
| `decreto_525_2009.json` | Decreto 525/2009: artículos 1 a 8. |
| `decreto_1069_2015.json` | Decreto 1069/2015: artículo 2.2.6.1.2.4.1. |
| `decreto_650_1996.json` | Decreto 650/1996: artículo 15 (devoluciones). |
| `decreto_2150_1995.json` | Decreto 2150/1995: artículo 12 (firma mecánica). |
| `decreto_806_2020.json` | Decreto 806/2020: artículo 8 (notificaciones). |
| `decreto_960_1970.json` | Decreto 960/1970: artículos 68, 80 y 81. |
| `checksums.json` | Manifiesto agregado (no se importa como norma). |

## Fuentes y extracción

- `secretariasenado.gov.co/senado/basedoc`: leyes y decretos consolidados.
- `funcionpublica.gov.co/eva/gestornormativo`: Decreto 525/2009 y Decreto
  1069/2015. La descarga usa `curl` (CA del sistema) y, si no está disponible,
  un fallback documentado en el script.
- `normograma.dian.gov.co`: Decreto 650/1996.

Cada artículo conserva `official_url`, `version_date` y checksum SHA-256 del
texto normalizado.

## Reglas de carga

1. Usar la fuente oficial publicada por el Estado colombiano.
2. No inventar ni completar artículos con memoria del modelo.
3. Si un artículo no está en el corpus, el gate bloquea la conclusión jurídica.
4. El `checksum` es obligatorio cuando se carga `text`.
5. El importador no descarga nada en runtime.
6. El importador omite `checksums.json`.

## Alias de identificación

El expediente cita `artículo 839-01`; el artículo oficial es `839-1`. Se carga
una fila alias `839-01` con **el mismo texto y checksum** y metadata
`alias_of: "839-1"`. No es una norma nueva ni texto inventado. El gate preserva el identificador y lo resuelve contra la fila alias; también se
carga `784-5` como alias explícito del artículo 784.

## Comandos

```bash
# Reconstruir desde fuente oficial (idempotente mientras la fuente no cambie)
python scripts/build_legal_corpus.py

# Validar y regenerar el manifiesto
python scripts/build_checksums.py
python scripts/build_checksums.py --check

# Importar en una organización o en todas
python scripts/import_legal_rules.py --dir data/legal_rules/co --organization-id <uuid>
python scripts/import_legal_rules.py --dir data/legal_rules/co --all --dry-run
python scripts/import_legal_rules.py --dir data/legal_rules/co --all
```

Sin normas cargadas, el gate bloquea las conclusiones jurídicas en lugar de
inventarlas. El corpus no se fabrica con memoria del modelo.
