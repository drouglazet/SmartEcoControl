#!/bin/sh
# Sonde la box Smart ECOcontrol avec des requêtes de LECTURE uniquement
# (aucun appel .cgi : rien n'est modifié, rien n'est mis à jour).
#
# À lancer depuis le terminal de Home Assistant (la box doit être joignable) :
#   sh probe.sh 192.168.31.150 2 3 4 5 2   (FERMEZ d'abord les pages de la box ouvertes ailleurs)
# Les nombres après l'adresse sont des numéros de pièce : le script sélectionne
# chaque pièce comme le fait l'interface web puis lit xml/status-piece.xml.
# Copiez toute la sortie dans la conversation.

HOST="${1:-192.168.31.150}"
[ $# -gt 0 ] && shift

show() {
  echo
  echo "=== $1 ==="
  curl -s -m 10 "http://$HOST/$1"
  echo
}

for page in xml/status-general.xml xml/liste-pieces.xml xml/liste-prog.xml \
            xml/vacances.xml xml/status-date.xml; do
  show "$page"
done

for id in "$@"; do
  echo
  echo "##### Sélection de la pièce $id (mon-logement.html?n_piece=$id)"
  curl -s -m 10 -o /dev/null "http://$HOST/mon-logement.html?n_piece=$id"
  sleep 1
  show xml/status-piece.xml
done
