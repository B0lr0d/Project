"""Monitoring et commande d'un fourgon aménagé.

Températures, niveaux, batterie et chauffage, sur écran tactile, en local.

Le programme est organisé en quatre couches à dépendance unidirectionnelle :

    ui  →  core  →  hal.interfaces
                 ↘  config

``core`` et ``ui`` ne connaissent aucun matériel : le choix entre le fourgon
réel et le fourgon simulé se fait dans ``hal.factory``, appelé uniquement par
``app``.
"""

__version__ = "0.2.0"        # étape 2 — mode simulation
