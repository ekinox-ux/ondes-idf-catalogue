# Ondes IDF — catalogue des radios d'Île-de-France

Catalogue utilisé par l'application Android **Ondes IDF**. L'application télécharge
`stations.json` une fois par jour.

| Fichier | Rôle |
|---|---|
| `seed/stations.seed.json` | Liste source **éditée à la main** : nom, fréquence FM, bloc DAB+, genres, flux imposés |
| `stations.json` | Catalogue généré : flux vérifiés, codec, débit, logo. **Ne pas éditer à la main.** |
| `report.md` | Rapport de la dernière vérification |
| `scripts/build_catalog.py` | Reconstruit tout ou partie du catalogue (Radio France + radio-browser.info) |
| `scripts/check_streams.py` | Vérification nocturne, réparation automatique des flux morts. Un flux n'est retiré (`ok: false`) qu'après 3 nuits d'échec d'affilée (champ `fails`) |

## Ajouter ou corriger une radio

1. Modifier `seed/stations.seed.json` (une station par ligne).
   - `"streams": [{"url": "…"}]` impose des flux (prioritaires sur la recherche automatique).
   - `"q"` / `"rbNames"` aident la recherche radio-browser ; `"skipRb": true` la désactive.
   - `"logo"` impose un logo.
2. `python scripts/build_catalog.py <id>` pour régénérer cette station.
3. Valider et pousser : le workflow vérifie aussitôt les flux.

Aucune dépendance : Python 3.10+ standard suffit.

Les logos ne sont pas copiés dans ce dépôt : seules les URL d'origine sont référencées.
