# Smart ECOcontrol pour Home Assistant

Intégration personnalisée (HACS) pour la box locale **Smart ECOcontrol**
(radiateurs connectés). Elle parle directement à l'interface web de la box,
sans cloud.

> **État : version 0.2.** Les formats des pages de la box ont été relevés sur une vraie
> box et la sélection des pièces a été vérifiée en lecture ; les commandes
> (consigne, dérogation) restent à essayer. Voir « Avant le premier essai ».

## Ce que vous obtenez

Pour chaque pièce (un appareil Home Assistant par pièce) :

| Entité | Rôle |
|---|---|
| `climate` | Consigne (± l'encadrement autorisé par la box), préréglages Éco / Confort / Hors-gel = dérogation, retour au planning |
| `select` Planning de chauffe | Change le planning de la pièce |
| `sensor` Allure | Allure réellement appliquée (Éco, Confort -1/-2/+1, Arrêt, Hors-gel) |
| `binary_sensor` Fenêtre ouverte | Détection de fenêtre ouverte de la box |
| `binary_sensor` Problème de communication | Un thermostat de la pièce ne répond plus |

Deux **cartes**, fournies avec l'intégration : `custom:smart-ecocontrol-card` colore
chaque pièce selon son allure (voir « Carte colorée ») et
`custom:smart-ecocontrol-planning-card` édite les plannings de chauffe (voir
« Éditeur de planning »).

Pour la box : `switch` **Mode vacances** (activé = hors-gel permanent),
`sensor` **Remise en chauffe** (date), `binary_sensor` **Mise à jour disponible**
(information seulement).

Services : `smart_ecocontrol.set_derogation` (allure + durée),
`smart_ecocontrol.cancel_derogation`, `smart_ecocontrol.set_vacation` (jusqu'à une date),
et pour les plannings `get_planning`, `set_planning_day`, `copy_planning_day`,
`create_planning`, `rename_planning`, `delete_planning` (voir « Éditeur de planning »).

## Ce que la box ne permet pas à distance

D'après son interface web, **le mode lui-même (Auto, Arrêt, Éco manuel…) se change
uniquement sur l'appareil avec la touche Smart**. L'intégration ne propose donc
que ce que l'interface web propose :

* consigne : seulement en mode **Auto** sur l'allure **Confort**, dans la plage
  « température préférée ± encadrement » (7 à 28 °C) ; pas de consigne en fil pilote ;
* dérogation : Éco / Confort / Hors-gel en mode Auto avec un planning ; seulement
  Hors-gel dans les autres modes ; aucune en mode Arrêt ou Hors-gel ;
* une dérogation dure au plus jusqu'à minuit ;
* comme sur la box, l'entité `climate` n'affiche une température que lorsque la box en
  affiche une (mode Auto + allure Confort, ou mode manuel). En Éco, Hors-gel,
  Confort -1/-2/+1 ou à l'arrêt la box n'affiche qu'un mot, et l'entité n'a alors
  ni température cible ni boutons +/- (la consigne mémorisée reste visible dans
  l'attribut `setpoint`) ;
* aucune température mesurée n'apparaît dans les pages analysées : l'entité
  `climate` n'a donc pas de température courante.

## Installation

1. Copiez le dossier `custom_components/smart_ecocontrol` dans `/config/custom_components/`
   de Home Assistant (ou publiez ce dépôt sur GitHub et ajoutez-le dans HACS >
   Dépôts personnalisés > catégorie *Intégration*).
2. Redémarrez Home Assistant.
3. Paramètres > Appareils et services > Ajouter une intégration > **Smart ECOcontrol**.
4. Saisissez l'adresse de la box. **Home Assistant doit pouvoir la joindre** : si la
   box est derrière le routeur Xiaomi (réseau 192.168.31.x), voir l'option A du
   fil de discussion (Home Assistant connecté en Wi-Fi à ce réseau) ou une route
   statique / un mode point d'accès.

Options (roue dentée de l'intégration) : intervalle d'actualisation (60 s par
défaut) et durée par défaut d'une dérogation (2 h).

Ajout d'une pièce sur la box : rechargez l'intégration.

## Carte colorée

Après l'installation et un redémarrage de Home Assistant, la carte est chargée
automatiquement (si elle n'apparaît pas : rechargez la page en vidant le cache,
Ctrl+F5, ou videz le cache de l'application mobile). Ajoutez-la à un tableau de
bord (Modifier > Ajouter une carte > « Smart ECOcontrol », ou en YAML) :

```yaml
type: grid
columns: 2
square: false
cards:
  - type: custom:smart-ecocontrol-card
    entity: climate.salon
  - type: custom:smart-ecocontrol-card
    entity: climate.chambre_bas
  - type: custom:smart-ecocontrol-card
    entity: climate.couloir_haut
  - type: custom:smart-ecocontrol-card
    entity: climate.chambre_rdc
```

(Les identifiants `climate.…` sont à vérifier dans Outils de développement > États.)

| Allure | Couleur |
|---|---|
| Éco | vert |
| Confort −2 / −1 / Confort / +1 | jaune / ambre / orange / rouge orangé |
| Hors-gel | bleu |
| Arrêt (et fenêtre ouverte) | gris |
| Délestage | violet |

La carte affiche le mode (Auto / Manuel / Arrêt), la température au centre quand la
box en affiche une (sinon le nom de l'allure), l'heure de fin d'une dérogation et le
planning. Les boutons **Éco / Confort / Hors-gel** lancent une dérogation (durée par
défaut des options) ; un second clic sur la dérogation active revient au planning.
Les boutons **− / +** (pas de 0,5 °C) n'existent que lorsque la box autorise la
consigne ; l'envoi se fait une seconde après le dernier clic, et la box met quelques
secondes à répondre. Options de la carte :

```yaml
type: custom:smart-ecocontrol-card
entity: climate.salon
name: Séjour          # nom affiché (par défaut : celui de la pièce)
step: 0.5             # pas des boutons − / +
colors:               # couleurs personnalisées (toutes facultatives)
  eco: "#2e7d32"
  comfort: "#ff9800"
  frost_protection: "#0288d1"
```

Les clés de `colors` sont : `eco`, `comfort_minus_2`, `comfort_minus_1`, `comfort`,
`comfort_plus_1`, `frost_protection`, `off`, `shedding`. Toutes ces options sont aussi
dans l'éditeur visuel de la carte (pièce, nom, pas, couleurs) ; une couleur laissée vide
garde sa valeur par défaut.

## Éditeur de planning

C'est l'équivalent de la page « Mes plannings de chauffe » de la box : pour chaque
jour de la semaine, des **plages de confort** (le reste de la journée est en Éco).
Règles de la box, vérifiées aussi par l'intégration : 5 plages au plus par jour, 1 heure
au moins par plage, 1 heure au moins entre deux plages, pas de plage après minuit.

```yaml
type: custom:smart-ecocontrol-planning-card
planning: SALON       # facultatif : nom ou numéro du planning ouvert au départ
title: Planning       # facultatif
colors:               # facultatif : eco, comfort
  comfort: "#fb8c00"
```

Titre, planning de départ et couleurs sont aussi dans l'éditeur visuel de la carte.

* Choisissez le planning dans la liste (« Sans planning » est réservé par la box et
  n'est pas modifiable). La ligne « Pièces » indique quelles pièces l'utilisent :
  **modifier un planning change donc le chauffage de ces pièces.**
* Touchez une zone verte (Éco) pour **ajouter** une plage de confort, une zone orange
  pour la **modifier ou la supprimer**. Les heures proposées respectent déjà les règles
  ci-dessus.
* Le bouton ⧉ en bout de ligne **copie** un jour vers d'autres jours, ou remet le jour
  entièrement en Éco.
* Le menu ⋮ crée, renomme ou supprime un planning (12 au plus, « Sans planning »
  compris ; un planning encore attribué à une pièce ne peut pas être supprimé ; le nom
  ne garde que des lettres majuscules, des chiffres et des espaces, 25 caractères au plus).
* Le bouton ↻ relit le planning depuis la box. Sur un écran étroit, les heures
  s'écrivent sous chaque barre.

Chaque écriture est **relue dans la box** : si le planning relu ne correspond pas à ce
qui a été demandé, l'intégration affiche une erreur au lieu de laisser croire que
c'est fait. Les mêmes actions existent en services (utilisables dans des scripts et
des automatisations) :

```yaml
action: smart_ecocontrol.set_planning_day
data:
  planning: SALON        # nom ou numéro
  days: [mon, tue, wed, thu, fri]
  ranges:                # liste vide = toute la journée en Éco
    - { start: "06:30", end: "08:30" }
    - { start: "17:00", end: "22:00" }
```

`copy_planning_day` (`source_day`, `target_days`), `create_planning` (`name`),
`rename_planning` (`planning`, `name`), `delete_planning` (`planning`) et
`get_planning` (renvoie les plages, sans `planning` la liste des plannings) suivent
le même principe. Les heures sont au quart d'heure, `24:00` est accepté en fin de plage.

**Attention, première utilisation.** La lecture des plannings a été validée sur des
relevés réels de la box, mais **l'écriture n'a été testée que sur une fausse box**.
Faites donc le premier essai sur un planning de test (créez-en un depuis le menu ⋮,
sans pièce attribuée), vérifiez le résultat dans l'interface d'origine, puis seulement
après sur un vrai planning. Fermez les pages de la box avant d'éditer : comme pour les
pièces, la sélection du planning est partagée et l'intégration refuse d'écrire plutôt
que de risquer de modifier le mauvais planning.

## Avant le premier essai : valider la sélection des pièces

Les formats des pages XML de la box ont été relevés sur une vraie box (pièces,
plannings, vacances, date, état d'une pièce) et sont couverts par les tests. Un
défaut du micrologiciel est géré : à partir du planning n° 10, `status-piece.xml`
écrit le numéro comme un seul caractère (10 → `:`, 11 → `;`…).

Reste une hypothèse à confirmer sur la box : la box est un petit serveur embarqué
**à état**. `consigne_piece.cgi` et `derog_piece.cgi` n'ont pas de paramètre
« pièce » ; ils s'appliquent à la dernière pièce affichée via
`mon-logement.html?n_piece=N`. L'intégration suppose que charger cette page
« sélectionne » la pièce dans `xml/status-piece.xml`.

**Fermez d'abord toutes les pages de la box** (navigateurs, téléphones, onglets
oubliés) : une page ouverte recharge sa propre pièce dès qu'elle en voit une autre
et fausse le test. Puis :

```sh
sh tools/probe.sh 192.168.31.150 2 3 4 5 2
```

(`probe.sh` ne fait que des lectures.) Après la sélection `n_piece=2`, 3, 4, 5 puis 2,
`status-piece.xml` doit contenir `<id_piece>` 2, 3, 4, 5 puis 2. Si ce n'est pas le
cas, envoyez la sortie : seul `api.py` est concerné.

Deuxième point à vérifier au premier essai d'une dérogation : les heures envoyées
à la box sont des index de quart d'heure (0 à 96) calculés depuis l'heure de la
box (`xml/status-date.xml`). La fonction `retourneHeure15` de `util.js`, que je
n'ai pas vue, est supposée valoir `heure × 4 + minute ÷ 15`. Après une dérogation
de 2 h, l'attribut `derogation_until` de l'entité `climate` doit afficher
l'heure de la box + 2 h.

## Précautions

* Ne laissez pas l'interface web de la box ouverte pendant que Home Assistant
  travaille : la sélection de pièce (et de planning) est partagée, l'intégration refait
  alors des essais, voire marque une pièce indisponible quelques instants.
* L'intégration n'appelle **jamais** `cgi/maj_ok.cgi` (mise à jour du firmware,
  12 h sans couper l'alimentation). La mise à jour reste une action volontaire dans
  l'interface de la box.
* Aucune authentification n'est requise par la box en local : ne l'exposez pas
  sur internet.

## Tests

```sh
pip install aiohttp
python tests/test_api.py
```

Les tests utilisent une fausse box (`tests/mock_box.py`) qui imite le
comportement déduit du JavaScript, avec les relevés réels dans `tests/fixtures/` pour
les formats ; ils ne prouvent donc pas que l'écriture se comporte de la même façon sur la
vraie box (voir « Éditeur de planning »).

## Publier sur GitHub / HACS

Remplacez `VOTRE_COMPTE` dans `custom_components/smart_ecocontrol/manifest.json`, publiez
le dépôt avec `hacs.json` à la racine, puis créez des *releases* (`v0.3.0`…).
