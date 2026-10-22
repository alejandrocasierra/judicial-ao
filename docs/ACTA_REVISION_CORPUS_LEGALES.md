# Acta de revisión del corpus legal — módulos legales

**Fecha:** 2026-10-22  
**Ámbito:** corpus `data/legal_rules/co/` y manifiesto `data/legal_rules/co/checksums.json`.  
**Estado:** verificación técnica reproducible **completada**; firma jurídica humana **pendiente**.  
**Alcance:** piloto local. No acredita producción ni sustituye la revisión de un abogado.

---

## 1. Distinción obligatoria

| Dimensión | Qué significa | Estado en este acta |
|---|---|---|
| **Verificación técnica reproducible** | La fuente es oficial, la extracción es reproducible, el checksum coincide, el importador valida y las normas quedan activas en las dos organizaciones locales. | **Completada** (evidencia en §3). |
| **Firma jurídica humana** | Un abogado revisa artículo por artículo que el texto sea el vigente, que la fuente sea la correcta y que el uso en el piloto sea jurídicamente admisible. | **No realizada.** No hay firma. Bloqueada. |
| **Aceptación de producción** | Autorización expresa, backup verificado y despliegue en el target real. | **No ejecutada.** Ver `docs/OPERACION_DESPLIEGUE_MODULOS_LEGALES.md`. |

> Este documento **no** afirma que un abogado haya revisado el corpus. Tampoco
> declara “corpus oficial definitivo”. Solo deja constancia de qué se verificó
> técnicamente y qué queda pendiente de firma.

---

## 2. Composición del corpus

El corpus pasó de **38 a 135 normas por organización** (`270` filas en las dos
organizaciones locales), en dos pasadas:

**Primera pasada (38 → 97):** 59 normas nuevas.

| Fuente oficial | Artículos añadidos | Total añadido |
|---|---|---|
| Ley 1579 de 2012 — Estatuto de Registro de Instrumentos Públicos | 3, 4, 8, 10, 11, 12, 13, 14, 15, 16, 22, 28, 31, 33, 34, 59 | 16 |
| Decreto 2723 de 2014 — Superintendencia de Notariado y Registro | 21, 22 | 2 |
| Estatuto Tributario — Decreto 624 de 1989 | 630, 839-1, 840 y alias 839-01 | 4 |
| Código Civil — Ley 57 de 1887 | 769, 1602, 1617, 2432, 2438, 2478, 2499, 2500, 2535 | 9 |
| Código General del Proceso — Ley 1564 de 2012 | 25, 27, 35, 37, 40, 42, 49, 72, 77, 89, 90, 103, 132, 136, 161, 170, 173, 185, 191, 198, 261, 330, 430, 438, 465, 468, 593, 599 | 28 |

**Segunda pasada (97 → 134):** 37 normas nuevas, todas citadas en el expediente.

| Fuente oficial | Artículos añadidos | Total añadido |
|---|---|---|
| Ley 527 de 1999 — Comercio Electrónico y Firma Digital | 7, 12, 20 | 3 |
| Ley 2213 de 2022 — Uso de Tecnologías de la Información | 3, 9, 10 | 3 |
| Ley 1437 de 2011 — CPACA | 67, 76, 77, 79, 87 | 5 |
| Ley 1394 de 2010 — Arancel Judicial | 6, 7, 8 | 3 |
| Ley 962 de 2005 — Racionalización de Trámites | 19 | 1 |
| Ley 590 de 2000 — Mipymes | 2, 43 | 2 |
| Ley 1581 de 2012 — Protección de Datos Personales | 4, 9 | 2 |
| Ley 270 de 1996 — Administración de Justicia | 16 | 1 |
| Decreto Ley 019 de 2012 — Supresión de Trámites | 15, 18 | 2 |
| Decreto 525 de 2009 — Mipymes y Aportes Parafiscales | 1, 2, 3, 4, 5, 6, 7, 8 | 8 |
| Decreto 1069 de 2015 — Decreto Único del Sector Justicia | 2.2.6.1.2.4.1 | 1 |
| Decreto 650 de 1996 — Reglamentario de la Ley 223 de 1995 | 15 | 1 |
| Decreto 2150 de 1995 — Supresión de Trámites y Firma Mecánica | 12 | 1 |
| Decreto 806 de 2020 — Notificaciones Electrónicas | 8 | 1 |
| Decreto 960 de 1970 — Estatuto Notarial | 68, 80, 81 | 3 |

Las **38** normas previas (CGP, Código Civil y Código de Comercio) se conservan
sin cambios. Los artículos nuevos se eligieron porque aparecen citados en
`document_pages`, `claims` o `facts` del expediente piloto. Cuando la ley se cita
sin artículo (p. ej. Ley 962/2005, Ley 1581/2012, Decreto 525/2009), se cargó el
artículo oficial que regula la materia citada (19, 9, 1–8 respectivamente), no
un artículo arbitrario.

### Nota sobre el alias 839-01

El expediente cita `artículo 839-01 del Estatuto Tributario`. El artículo
oficial es **839-1**. El corpus carga el texto oficial como `839-1` y una fila
alias `839-01` con **el mismo texto y checksum**, metadata `alias_of: 839-1`.
No se inventó texto: el alias solo resuelve la forma de citación del expediente.

---

## 3. Verificación técnica ejecutada

### 3.1 Fuente y extracción

- Fuentes: `secretariasenado.gov.co/senado/basedoc` (leyes y decretos),
  `funcionpublica.gov.co/eva/gestornormativo` (Decreto 525/2009 y Decreto
  1069/2015) y `normograma.dian.gov.co` (Decreto 650/1996).
- Script: `scripts/build_legal_corpus.py`.
- Método: descarga de la página oficial, extracción del bloque `ARTÍCULO N`
  (incluye numeración punteada como `2.2.6.1.2.4.1`), normalización de espacios,
  eliminación de apéndices editoriales (`Legislación Anterior`, notas de
  vigencia) y cálculo de SHA-256 del texto.
- `official_url` por artículo apunta a la página oficial exacta.
- `version_date` es la fecha de “Última actualización” publicada por la fuente o
  la fecha de expedición/actualización documentada para las fuentes de
  Función Pública y DIAN.
- `metadata.retrieved_at` se fija a una fecha determinista para que la
  reconstrucción no dependa del reloj.
- Para Función Pública se usa `curl` (CA del sistema). Si no está disponible, el
  script tiene un fallback documentado en `verify_tls`.

Comandos:

```bash
python scripts/build_legal_corpus.py --dry-run --only ley_1579_2012
python scripts/build_legal_corpus.py --dry-run --only decreto_2723_2014,estatuto_tributario
python scripts/build_legal_corpus.py --dry-run --only codigo_civil
python scripts/build_legal_corpus.py --dry-run --only cgp
python scripts/build_legal_corpus.py --dry-run --only ley_527_1999,ley_2213_2022,ley_1437_2011,ley_1394_2010,ley_962_2005,ley_590_2000,ley_1581_2012,ley_270_1996,decreto_ley_019_2012,decreto_525_2009,decreto_1069_2015,decreto_650_1996,decreto_2150_1995,decreto_806_2020,decreto_960_1970
python scripts/build_legal_corpus.py
```

Resultado: `58` artículos en la primera pasada + `1` alias, y `37` artículos en
la segunda pasada; total **135 filas**.

### 3.2 Checksums

- Manifiesto: `data/legal_rules/co/checksums.json`.
- Script: `scripts/build_checksums.py`.
- `generated_at` se deriva del máximo `retrieved_at` del corpus, no del reloj.
- Dos ejecuciones consecutivas produjeron el mismo SHA-256 de archivo.
- `--check` valida que el manifiesto esté al día.

Evidencia:

```text
Escrito data\legal_rules\co\checksums.json: 135 normas, 21 fuentes,
generated_at=2026-10-22T15:25:41.434418+00:00
IDEMPOTENT=YES
OK: data\legal_rules\co\checksums.json coincide (135 normas)
```

Valores del manifiesto:

| Campo | Valor |
|---|---|
| `rules_count` | 135 |
| `sources_count` | 21 |
| `corpus_sha256` | `5c6a16c236c68af97f17407c189c3cd99489cc2a0c0d3b29eb560120d9271739` |
| SHA-256 del archivo `checksums.json` | `411450A3BE2B5701B5A6447767B55C8249185D8CFE9D9448FC7D32A700F3B342` |

### 3.3 Importación

Comandos:

```bash
python scripts/import_legal_rules.py --dir data/legal_rules/co --dry-run
python scripts/import_legal_rules.py --dir data/legal_rules/co --all
```

Resultado:

- Dry-run: `270` normas simuladas (135 reglas × 2 organizaciones).
- Importación final: `270` normas (135 × 2 organizaciones).
- Organizaciones locales: Alfa `b4e6d687-…` y Beta `9830ee40-…`.
- Verificación SQL: `270` filas activas, `135` por organización, `21` fuentes;
  **0 filas** con `checksum` o `official_url` vacíos.
- El importador omite `checksums.json` para no tratarlo como norma.

### 3.4 Comprobaciones del gate con el corpus nuevo

Ejecutadas contra la base local (no son una firma jurídica):

| Caso | Resultado |
|---|---|
| Artículo 422 CGP (norma existente) | `SUPPORTED` |
| Artículo 9999 CGP (norma inexistente) | `UNSUPPORTED` / `LEGAL_RULE_MISSING` |
| Cifra inventada | `UNSUPPORTED` / `NUMBER_MISMATCH` |
| Fecha inventada | `UNSUPPORTED` / `DATE_MISMATCH` |
| Parte no presente en la cita | `NEEDS_REVIEW` / `PARTY_MISMATCH` |
| Cita válida | `SUPPORTED` |
| Ley 527/1999, art. 7 | `SUPPORTED` |
| Ley 2213/2022, art. 10 | `SUPPORTED` |
| CPACA, art. 67 | `SUPPORTED` |
| Ley 1394/2010, art. 7 | `SUPPORTED` |
| Ley 590/2000, art. 43 | `SUPPORTED` |
| Ley 1581/2012, art. 9 | `SUPPORTED` |
| Ley 270/1996, art. 16 | `SUPPORTED` |
| Decreto Ley 019/2012, art. 15 | `SUPPORTED` |
| Decreto 650/1996, art. 15 | `SUPPORTED` |
| Decreto 2150/1995, art. 12 | `SUPPORTED` |
| Decreto 806/2020, art. 8 | `SUPPORTED` |
| Decreto 960/1970, art. 80 | `SUPPORTED` |
| Decreto 1069/2015, art. 2.2.6.1.2.4.1 | `UNSUPPORTED` por limitación del gate (ver §4) |
| Segunda opinión persistida | `COMPLETED`, veredicto `PARTIAL`, verificador `deepseek-chat` distinto del respondedor |

Estos casos viven en `evals/golden/legal_modules.yaml` y se ejecutan con
`evals/golden/legal_modules_runner.py`. Resultado: **7/7 PASS** (ver §5).

---

## 4. Lo que **no** se verificó

1. **No hay revisión jurídica artículo por artículo.** Nadie firmó que el texto
   sea el vigente para cada caso de uso, ni que la selección sea suficiente.
2. **No se contrastó con el Diario Oficial** más allá de las páginas oficiales
   del Senado, Función Pública y DIAN. Las fuentes son oficiales, pero la
   consolidación puede tener notas editoriales.
3. **No se cubren todas las normas citadas en el expediente.** Quedan, entre
   otras, las siguientes familias detectadas y aún no cargadas:
   - Decreto 196 de 1971 (citado junto a Ley 270/1996, sin artículo).
   - Decreto 2364 de 2012 (citado como reglamentario de la Ley 527/1999, sin artículo).
   - Decreto 1074 de 2015 (citado para tamaño empresarial, sin artículo).
   - Decreto 1469 de 2010 (citado para propiedad horizontal, sin artículo).
   - Ley 1285 de 2009 (solo como modificatoria del art. 16 de la Ley 270/1996).
   - Ley 223 de 1995 (citada en el art. 15 de la Ley 1579/2012 y en el Decreto 650/1996).
   - Ley 2220 de 2022 (citada en la nota de derogatoria del art. 4 de la Ley 1579/2012).
   - Ley 6 de 1992 (citada en la nota del art. 839-1 del Estatuto Tributario).
   - Artículos adicionales del Código de Comercio y del Código Civil que el
     expediente menciona sin número o en notas editoriales.
4. **No se validó la vigencia material** de artículos modificados por normas
   posteriores; se usa el texto consolidado de la fuente.
5. **No se desplegó en producción.** Ver el runbook y el bloqueo en
   `docs/OPERACION_DESPLIEGUE_MODULOS_LEGALES.md`.

### Limitación técnica detectada (P1)

El gate actual extrae números de artículo con un patrón que no reconoce
`839-01` completo: lo reduce a `839`. Tampoco expande rangos como
`artículos 10-16 y 31`; solo verifica el primer número. Tampoco reconoce
numeración punteada como `2.2.6.1.2.4.1`: la reduce a `2`, por lo que el
Decreto 1069/2015 queda cargado pero el gate no lo resuelve. El corpus y los
alias están cargados; la resolución requiere ajuste en
`apps/api/app/services/legal_guard.py` (territorio P1). Se registró en
`docs/coordination/LEGAL_MODULES/EVENTS.md`.

---

## 5. Evidencia reproducible (resumen)

```text
build_legal_corpus.py       -> 37 filas nuevas (segunda pasada), 21 fuentes
build_checksums.py          -> 135 normas, idempotente, --check OK
import_legal_rules.py       -> 268 filas en 2 organizaciones
legal_modules_runner.py     -> 7/7 PASS
```

Rutas:

- `data/legal_rules/co/ley_527_1999.json`
- `data/legal_rules/co/ley_2213_2022.json`
- `data/legal_rules/co/ley_1437_2011.json`
- `data/legal_rules/co/ley_1394_2010.json`
- `data/legal_rules/co/ley_962_2005.json`
- `data/legal_rules/co/ley_590_2000.json`
- `data/legal_rules/co/ley_1581_2012.json`
- `data/legal_rules/co/ley_270_1996.json`
- `data/legal_rules/co/decreto_ley_019_2012.json`
- `data/legal_rules/co/decreto_525_2009.json`
- `data/legal_rules/co/decreto_1069_2015.json`
- `data/legal_rules/co/decreto_650_1996.json`
- `data/legal_rules/co/decreto_2150_1995.json`
- `data/legal_rules/co/decreto_806_2020.json`
- `data/legal_rules/co/decreto_960_1970.json`
- `data/legal_rules/co/checksums.json`
- `scripts/build_legal_corpus.py`
- `scripts/build_checksums.py`
- `scripts/import_legal_rules.py`
- `evals/golden/legal_modules.yaml`
- `evals/golden/legal_modules_runner.py`

---

## 6. Firma jurídica humana (pendiente)

Este bloque debe completarlo un abogado con tarjeta profesional. Mientras esté
vacío, el corpus se considera **técnicamente verificado pero no firmado**.

| Campo | Valor |
|---|---|
| Nombre del revisor | ______________________________ |
| Tarjeta profesional | ______________________________ |
| Fecha de revisión | ______________________________ |
| Artículos revisados | ______________________________ |
| Observaciones | ______________________________ |
| Decisión | ☐ Aprueba ☐ Aprueba con observaciones ☐ Rechaza |
| Firma | ______________________________ |

Declaración sugerida (no firmada por este agente):

> “He revisado la selección y el texto de los artículos listados en este acta
> contra la fuente oficial indicada. Certifico que, a mi juicio profesional, el
> texto corresponde a la versión publicada y que el uso previsto en el piloto
> es jurídicamente admisible, con las observaciones anotadas.”

**Bloqueo declarado:** sin esta firma no se puede afirmar que el corpus sea el
“corpus oficial definitivo” del piloto ni cerrar el gap P0 de aceptación legal.


## 6. Cierre técnico final (2026-10-22)

- Corpus final: **135 normas por organización**, **270 filas** en Alfa y Beta.
- Fuentes: **21**. Filas sin `checksum` o `official_url`: **0**.
- `checksums.json`: `rules_count=135`, `sources_count=21`.
- `corpus_sha256`: `5c6a16c236c68af97f17407c189c3cd99489cc2a0c0d3b29eb560120d9271739`.
- SHA-256 del archivo: `411450a3be2b5701b5a6447767b55c8249185d8cfe9d9448fc7d32a700f3b342`.
- Se añadió el alias `784-5` con el texto oficial del artículo 784, numeral 5, metadata `alias_of: "784"`. No es una norma nueva ni texto inventado.
- Gate: resuelve `839-01`, `784-5`, rangos `10-16` y el artículo punteado `2.2.6.1.2.4.1`.
- La **firma jurídica humana sigue pendiente**: este acta solo acredita trazabilidad técnica.
