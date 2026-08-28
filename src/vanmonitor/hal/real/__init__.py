"""Pilotes du matériel réel.

MATERIEL À INTEGRER PLUS TARD pour la plupart d'entre eux.

Ces modules existent dès maintenant pour que la fabrique ait quelque chose à
instancier et que la structure du projet soit figée. Tant que le matériel n'est
pas choisi, ils lèvent proprement ``NotImplementedError`` avec un message
explicite : ils ne font jamais semblant de fonctionner, et ne font jamais
tomber le programme sur une erreur obscure.

Les dépendances matérielles (``pyserial``, pilote du convertisseur, GPIO) sont
importées **à l'intérieur** de ces modules et non au chargement du paquet :
leur absence sur un PC de développement ne doit rien empêcher.
"""
