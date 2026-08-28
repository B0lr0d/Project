"""Interface graphique.

Ne détient aucune référence vers le matériel : elle lit un instantané déjà
calculé et dépose ses commandes dans une file. C'est ce qui garantit qu'aucune
entrée-sortie matérielle ne peut avoir lieu dans le thread graphique.

Une seule exception, explicite et documentée : ``sim_panel`` manipule l'état du
fourgon **simulé** (``hal.sim.sim_state``), puisque c'est précisément son rôle.
Le test ``tests/test_imports.py`` l'autorise nommément et interdit à tous les
autres modules d'en faire autant.
"""
