"""Fausse box Smart ECOcontrol, écrite d'après le JavaScript des pages web.

Elle imite la box « à état » : status-piece.xml décrit la pièce sélectionnée par
mon-logement.html?n_piece=N, et les CGI de consigne / dérogation s'appliquent à
cette pièce. Les plannings fonctionnent pareil : status-prog.xml décrit celui
affiché en dernier via mes-plannings.html?n_prog=N. Les formats imitent des
relevés faits sur la vraie box.
"""

from __future__ import annotations

from aiohttp import web


def make_rooms() -> dict[str, dict]:
    base = {
        "window": "1",
        "fp6": "0",
        "derog": "0",
        "derog_allure": "0",
        "derog_start": "0",
        "derog_end": "0",
        "offline": "0",
    }
    return {
        "1": {**base, "id": "1", "name": "Séjour", "prog": "2", "mode": "7",
              "allure": "3", "setpoint": "19.5", "pref": "19.0"},
        "2": {**base, "id": "2", "name": "Chambre", "prog": "2", "mode": "7",
              "allure": "0", "setpoint": "16.0", "pref": "19.0"},
        "3": {**base, "id": "3", "name": "Salle d'eau", "prog": "1", "mode": "3",
              "allure": "3", "setpoint": "21.0", "pref": "21.0", "offline": "1"},
        "4": {**base, "id": "4", "name": "Bureau", "prog": "2", "mode": "7",
              "allure": "3", "setpoint": "19.0", "pref": "19.0", "fp6": "1"},
    }


class MockBox:
    def __init__(self) -> None:
        self.rooms = make_rooms()
        self.current: str | None = None
        self.current_prog: str | None = None
        # id -> [nom, pièces] ; « NULL » = emplacement libre (la box en compte 14)
        self.plannings: dict[str, list[str]] = {
            "1": ["SANS PLANNING", "NULL"],
            "2": ["SEMAINE", "Séjour, Chambre"],
            "3": ["TÉLÉTRAVAIL", "NULL"],
            **{str(i): ["NULL", "NULL"] for i in range(4, 15)},
        }
        # id -> 7 jours (lundi = 0) -> {emplacement: (début, fin)} en quarts d'heure
        self.schedules: dict[str, list[dict[int, tuple[int, int]]]] = {
            pid: [{} for _ in range(7)] for pid in self.plannings
        }
        self.schedules["2"][0] = {0: (28, 36), 1: (68, 90)}
        self.schedules["2"][6] = {0: (32, 88)}
        self.flaky_prog_reads = 0  # lectures qui renverront un autre planning
        self.ignore_prog_cgi = False  # la box « oublie » d'appliquer prog.cgi
        self.log: list[tuple[str, dict[str, str]]] = []
        self.referers: list[tuple[str, str | None]] = []
        self.flaky_reads = 0  # nombre de lectures qui renverront la mauvaise pièce
        # Simule une page web ouverte ailleurs qui change la pièce sélectionnée :
        #  - steal_after_reads = N : après la N-ième lecture, la sélection bascule
        #    une fois sur steal_to (un rechargement de la page l'efface) ;
        #  - wrong_after_reads = N : à partir de la (N+1)-ième lecture, status-piece
        #    décrit toujours une autre pièce, quoi qu'on sélectionne.
        self.status_reads = 0
        self.steal_after_reads: int | None = None
        self.steal_to: str | None = None
        self.wrong_after_reads: int | None = None
        self.box_time = (26, 11, 5, 10, 7, 0)
        self.vacation = {"hg": "0", "active": "0", "y": "", "m": "", "d": ""}
        self.status_code_for: dict[str, int] = {}

    # --------------------------------------------------------------- XML

    def room_xml(self, room: dict) -> bytes:
        # Forme observée sur la vraie box : enveloppe <reponse>, nom en CDATA,
        # <fp6_piece/> vide quand il n'y a pas de fil pilote, pas de déclaration XML.
        fp6 = "<fp6_piece>1</fp6_piece>" if room["fp6"] == "1" else "<fp6_piece/>"
        text = f"""<reponse>
<reponse_piece>
<id_piece>{room['id']}</id_piece>
<nom_piece>
<![CDATA[ {room['name']} ]]>
</nom_piece>
<prog_piece>{room['prog']}</prog_piece>
<allure_piece>{room['allure']}</allure_piece>
<mode_piece>{room['mode']}</mode_piece>
<consigne_piece>{room['setpoint']}</consigne_piece>
<consigne_preferee>{room['pref']}</consigne_preferee>
<fenetre_piece>{room['window']}</fenetre_piece>
<presence_piece>0</presence_piece>
<verrou_piece>0</verrou_piece>
{fp6}
<derog_piece>{room['derog']}</derog_piece>
<allure_derog>{room['derog_allure']}</allure_derog>
<heure_debut_derog>{room['derog_start']}</heure_debut_derog>
<heure_fin_derog>{room['derog_end']}</heure_fin_derog>
<encardrement_temp_plus>2</encardrement_temp_plus>
<encardrement_temp_moins>2</encardrement_temp_moins>
</reponse_piece>
</reponse>"""
        return text.encode("iso-8859-1")

    def rooms_xml(self) -> bytes:
        parts = ['<?xml version="1.0" encoding="ISO-8859-1"?><liste_pieces>']
        for room in self.rooms.values():
            parts.append(
                f'<piece id="{room["id"]}"><nom_piece>{room["name"]}</nom_piece>'
                f'<thermostats><th offline="{room["offline"]}"/></thermostats></piece>'
            )
        parts.append('<piece id="5"><nom_piece>NULL</nom_piece></piece>')
        parts.append("</liste_pieces>")
        return "".join(parts).encode("iso-8859-1")

    def progs_xml(self) -> bytes:
        parts = ['<?xml version="1.0" encoding="ISO-8859-1"?><liste_prog>']
        for pid, (name, rooms) in self.plannings.items():
            parts.append(
                f'<prog id="{pid}"><nom_prog>{name}</nom_prog>'
                f"<liste_pieces_prog>{rooms}</liste_pieces_prog></prog>"
            )
        parts.append("</liste_prog>")
        return "".join(parts).encode("iso-8859-1")

    def schedule_xml(self, pid: str) -> bytes:
        parts = [f'<?xml version="1.0" encoding="ISO-8859-1" ?>\n<response>\n\t<prog>\n\t\t<id_prog>{pid}</id_prog>\n']
        for day, slots in enumerate(self.schedules[pid]):
            parts.append(f'\t\t<jour_prog id="{day}">\n')
            for slot in range(5):
                start, end = slots.get(slot, ("NULL", "NULL"))
                parts.append(
                    f'\t\t\t<point_prog id="{slot}">\n\t\t\t\t<heureDebut>{start}</heureDebut>\n'
                    f"\t\t\t\t<heureFin>{end}</heureFin>\n\t\t\t</point_prog>\n"
                )
            parts.append("\t\t</jour_prog>\n")
        parts.append("\t</prog>\n</response>")
        return "".join(parts).encode("iso-8859-1")

    def vacation_xml(self) -> bytes:
        v = self.vacation
        return (
            '<?xml version="1.0" encoding="ISO-8859-1"?><vacances>'
            f"<hors_gel_permanent>{v['hg']}</hors_gel_permanent>"
            f"<status_vacances><vacancesActive>{v['active']}</vacancesActive>"
            f"<vacancesAnnee>{v['y']}</vacancesAnnee><vacancesMois>{v['m']}</vacancesMois>"
            f"<vacancesJour>{v['d']}</vacancesJour></status_vacances></vacances>"
        ).encode("iso-8859-1")

    def date_xml(self) -> bytes:
        y, mo, d, h, mi, s = self.box_time
        return (
            '<?xml version="1.0" encoding="ISO-8859-1"?><status_date><Date>'
            f"<Date_Annee>{y}</Date_Annee><Date_Mois>{mo}</Date_Mois><Date_Jour>{d}</Date_Jour>"
            f"<Date_Heure>{h}</Date_Heure><Date_Minute>{mi}</Date_Minute><Date_Seconde>{s}</Date_Seconde>"
            "</Date></status_date>"
        ).encode("iso-8859-1")

    # ----------------------------------------------------------- routage

    async def handle(self, request: web.Request) -> web.Response:
        path = request.path
        query = dict(request.query)
        self.log.append((path, query))
        self.referers.append((path, request.headers.get("Referer")))

        if path in self.status_code_for:
            return web.Response(status=self.status_code_for[path])

        if path == "/mon-logement.html":
            self.current = query.get("n_piece")
            return web.Response(body=b"<html></html>", content_type="text/html")
        if path == "/mes-plannings.html":
            self.current_prog = query.get("n_prog")
            return web.Response(body=b"<html></html>", content_type="text/html")
        if path == "/xml/status-prog.xml":
            if self.current_prog not in self.schedules:
                return web.Response(status=404)
            pid = self.current_prog
            if self.flaky_prog_reads > 0:
                self.flaky_prog_reads -= 1
                pid = next(i for i in self.schedules if i not in ("1", pid))
            return web.Response(body=self.schedule_xml(pid))
        if path == "/xml/status-general.xml":
            return web.Response(
                body=b"<?xml version=\"1.0\"?><g><premiere_connexion>0</premiere_connexion>"
                b"<init_en_cours>0</init_en_cours><etat_wifi>1</etat_wifi><etat_tic>1</etat_tic>"
                b"<tarif_avec_pointe>0</tarif_avec_pointe><maj_ok>1</maj_ok></g>"
            )
        if path == "/xml/liste-pieces.xml":
            return web.Response(body=self.rooms_xml())
        if path == "/xml/liste-prog.xml":
            return web.Response(body=self.progs_xml())
        if path == "/xml/vacances.xml":
            return web.Response(body=self.vacation_xml())
        if path == "/xml/status-date.xml":
            return web.Response(body=self.date_xml())
        if path == "/xml/status-piece.xml":
            if self.current is None:
                return web.Response(status=404)
            room = self.rooms[self.current]
            self.status_reads += 1
            body = self.room_xml(room)
            wrong = (
                self.wrong_after_reads is not None
                and self.status_reads > self.wrong_after_reads
            )
            if self.flaky_reads > 0 or wrong:
                self.flaky_reads = max(0, self.flaky_reads - 1)
                other = next(r for r in self.rooms.values() if r["id"] != self.current)
                body = self.room_xml(other)
            if self.steal_after_reads == self.status_reads:
                self.current = self.steal_to
                self.steal_after_reads = None
            return web.Response(body=body)

        if path == "/cgi/consigne_piece.cgi":
            room = self.rooms[self.current]
            room["setpoint"] = str(int(query["newConsignePiece"]) / 10)
            return web.Response(text="ok")
        if path == "/cgi/derog_piece.cgi":
            room = self.rooms[self.current]
            if query["pieceDerog"] == "1":
                room.update(derog="1", derog_allure=query["allureDerog"],
                            derog_start=query["heureDebutDerog"], derog_end=query["heureFinDerog"])
            else:
                room.update(derog="0", derog_allure="0", derog_start="0", derog_end="0")
            return web.Response(text="ok")
        if path == "/cgi/deplace_piece.cgi":
            self.rooms[query["nPiece"]]["prog"] = query["nZone"]
            return web.Response(text="ok")
        if path == "/cgi/vacances.cgi":
            self.vacation = {
                "hg": query["HGpermanent"],
                "active": query["vacancesActive"],
                "y": "" if query["vacancesAnnee"] == "NULL" else query["vacancesAnnee"],
                "m": "" if query["vacancesMois"] == "NULL" else query["vacancesMois"],
                "d": "" if query["vacancesJour"] == "NULL" else query["vacancesJour"],
            }
            return web.Response(text="ok")
        if path == "/cgi/prog.cgi":
            if self.ignore_prog_cgi:
                return web.Response(text="ok")
            day = self.schedules[query["id_prog_cgi"]][int(query["id_jour_cgi"])]
            slot = query["id_plage_cgi"]
            start, end = int(query["heureDebut_prog_cgi"]), int(query["heureFin_prog_cgi"])
            if slot == "5":  # efface le jour
                day.clear()
            elif slot == "6":  # nouvelle plage : premier emplacement libre
                day[next(i for i in range(5) if i not in day)] = (start, end)
            elif start == end == 0:
                day.pop(int(slot), None)
            else:
                day[int(slot)] = (start, end)
            return web.Response(text="ok")
        if path == "/cgi/planning.cgi":
            pid, name = query["id_prog_cgi"], query["nom_prog_cgi"]
            if query["suppr_prog_cgi"] == "1":
                self.plannings[pid] = ["NULL", "NULL"]
                self.schedules[pid] = [{} for _ in range(7)]
            elif pid == "0":  # création : premier emplacement libre
                free = next(i for i, (n, _) in self.plannings.items() if n == "NULL")
                self.plannings[free] = [name, "NULL"]
            else:
                self.plannings[pid][0] = name
            return web.Response(text="ok")
        if path == "/cgi/maj_ok.cgi":
            raise AssertionError("maj_ok.cgi ne doit JAMAIS être appelé")
        return web.Response(status=404)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("GET", "/{tail:.*}", self.handle)
        return app
