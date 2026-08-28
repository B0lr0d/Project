# Monitoring Fourgon

Système de surveillance et de commande pour fourgon aménagé, sur Raspberry Pi 4 :
températures, niveaux, batterie auxiliaire et circuits de chauffage, sur écran
tactile, **en local, sans Internet**.

État : **étape 2 livrée — mode simulation.**
L'architecture complète est décrite dans [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Lancer sans matériel

Tout se teste sur un PC, sans Raspberry ni capteur.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

PYTHONPATH=src python -m vanmonitor --sim
```

Une fenêtre s'ouvre en deux volets :

* à gauche, **le fourgon simulé** — températures, niveaux, batterie, clapets, et
  surtout les pannes : capteur absent, erreur de lecture, lecture lente, lecture
  bloquée, liaison SmartShunt coupée, actionneur en défaut ;
* à droite, **ce que le logiciel en perçoit** — valeurs, âge, statut, santé des
  threads d'acquisition.

Sans interface graphique (par exemple sur un serveur) :

```bash
PYTHONPATH=src python -m vanmonitor --sim --headless --duration 30
```

Options utiles : `--config FICHIER`, `--windowed`, `--log-level DEBUG`.

## Tests

```bash
pip install pytest
QT_QPA_PLATFORM=offscreen python -m pytest
```

Les tests d'interface sont ignorés si PyQt5 est absent ; tout le reste doit
passer sans lui.

## Deux choses à essayer en priorité

1. **Débrancher une sonde** (« Absent » sur une ligne de température) puis en
   mettre une autre en « Erreur de lecture ». L'une affiche `--`, l'autre
   « Erreur capteur », et *tout le reste continue de fonctionner*.
2. **Décocher « retour de position disponible »** sur un circuit, puis
   l'ouvrir. Le logiciel affiche `OUVERT`, mais avec la mention
   `commandé — aucun retour de position` : il ne présentera jamais un état
   physique comme certain sans confirmation du matériel.

Le mode « Bloqué (chien de garde) » est le plus instructif : il reproduit un
pilote qui ne rend jamais la main. Le thread concerné est déclaré bloqué,
abandonné et remplacé, une alerte technique est levée — et les autres
acquisitions ne s'arrêtent pas une seconde.

## Organisation

```
ui  →  core  →  hal.interfaces
             ↘  config
```

`core/` et `ui/` ne connaissent aucun matériel. Le choix entre le fourgon réel
et le fourgon simulé se fait dans `hal/factory.py`, appelé uniquement par
`app.py` — un test automatique vérifie que cette règle n'est jamais contournée.

## Matériel

Confirmé : sondes DS18B20, Victron SmartShunt en VE.Direct filaire via une
interface VE.Direct/USB, réservoir de gasoil de 105 L.

Non encore choisi, et donc **jamais supposé** dans le code : capteurs de niveau,
convertisseur analogique-numérique, actionneurs des clapets, modèle d'écran.
Les modules correspondants existent sous `hal/real/`, portent la mention
`MATERIEL À INTEGRER PLUS TARD` et lèvent proprement `NotImplementedError`.

## Sur Raspberry Pi

```bash
sudo apt install python3-pyqt5
pip install -r requirements-pi.txt
```

PyQt5 s'installe par `apt`, pas par `pip` : le paquet système est compilé pour
l'ARM du Raspberry et testé avec la distribution. Le démarrage automatique et le
plein écran sont prévus à l'étape 12.
