"""Tests du client contre la fausse box. Lancer : python tests/test_api.py"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import date
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

spec = importlib.util.spec_from_file_location(
    "ecoapi", ROOT / "custom_components" / "smart_ecocontrol" / "api.py"
)
api = importlib.util.module_from_spec(spec)
sys.modules["ecoapi"] = api
spec.loader.exec_module(api)

from mock_box import MockBox  # noqa: E402


def calls(box: MockBox, path: str) -> list[dict[str, str]]:
    return [q for p, q in box.log if p == path]


async def expect_error(coro, key: str) -> None:
    try:
        await coro
    except api.EcoControlCommandError as err:
        assert err.key == key, f"clé attendue {key}, obtenue {err.key}"
        return
    raise AssertionError(f"EcoControlCommandError({key}) attendue")


def test_real_sample() -> None:
    """XML réel de la box (pièce 5) : enveloppe <reponse>, CDATA, fp6 vide."""
    raw = (ROOT / "tests" / "fixtures" / "status-piece-real.xml").read_bytes()
    status = api.parse_room_status(api.parse_xml(raw))
    assert (status.id, status.name, status.prog_id) == ("5", "CHAMBRE RDC", "2")
    assert (status.mode, status.allure) == (api.MODE_AUTO, api.ALLURE_ECO)
    assert (status.setpoint, status.preferred) == (19.0, 18.0)
    assert (status.margin_plus, status.margin_minus) == (2.0, 2.0)
    assert not status.fp6 and not status.derog_active and not status.window_open
    assert not status.can_set_setpoint  # allure Eco : pas de boutons +/- sur la box
    assert status.setpoint_bounds == (16.0, 20.0)
    # Signalé par l'utilisateur : la box affiche « Eco », pas 19 °C
    assert status.allure_name == "eco" and not status.shows_setpoint
    print("OK échantillon réel de la box")


def real(name: str):
    return api.parse_xml((ROOT / "tests" / "fixtures" / name).read_bytes())


def test_real_formats() -> None:
    """Réponses réelles de la box (relevées avec tools/probe.sh), toutes les pages."""
    general = api.parse_general(real("real-status-general.xml"))
    assert not general.update_available and not general.first_connection
    assert general.wifi == "2" and general.tic == "0"

    rooms = api.parse_rooms(real("real-liste-pieces.xml"))
    assert {k: v.name for k, v in rooms.items()} == {
        "2": "SALON", "3": "CHAMBRE BAS", "4": "COULOIR HAUT", "5": "CHAMBRE RDC",
    }  # pièces 6 à 14 « NULL » ignorées, <th_non_assignes> ignoré
    assert not any(room.offline for room in rooms.values())

    plannings = api.parse_plannings(real("real-liste-prog.xml"))
    assert list(plannings) == [str(i) for i in range(1, 12)]  # 12 à 14 « NULL »
    assert plannings["1"].name == "Sans planning" and plannings["1"].rooms == ""
    assert plannings["8"].name == "ch bas"  # espace final retiré
    assert plannings["11"] == api.Planning("11", "SALON VACANCES", "SALON")

    # date de retour restée à 2019 alors que les vacances sont inactives : ignorée
    assert api.parse_vacation(real("real-vacances.xml")) == api.Vacation(False, False, None)
    active = api.parse_xml(
        b"<response><hors_gel_permanent>0</hors_gel_permanent><status_vacances>"
        b"<vacancesActive>1</vacancesActive><vacancesAnnee>2026</vacancesAnnee>"
        b"<vacancesMois>12</vacancesMois><vacancesJour>24</vacancesJour>"
        b"</status_vacances></response>"
    )
    assert api.parse_vacation(active) == api.Vacation(False, True, date(2026, 12, 24))

    box_time = api.parse_box_time(real("real-status-date.xml"))  # racine = <Date>
    assert box_time.isoformat() == "2026-11-05T08:55:11"

    room3 = api.parse_room_status(real("real-status-piece-room3.xml"))
    assert (room3.id, room3.name, room3.prog_id) == ("3", "CHAMBRE BAS", "9")
    assert (room3.mode, room3.allure, room3.setpoint) == ("7", "3", 18.0)
    assert room3.presence_detection and not room3.window_open and not room3.fp6
    assert not room3.derog_active  # heures de dérogation résiduelles ignorées
    assert room3.can_set_setpoint and room3.setpoint_bounds == (16.0, 20.0)
    assert room3.allure_name == "comfort" and room3.shows_setpoint

    room2 = api.parse_room_status(real("real-status-piece-room2.xml"))
    assert (room2.id, room2.name) == ("2", "SALON")
    assert room2.prog_id == "11"  # « ; » décodé : défaut du micrologiciel
    assert plannings[room2.prog_id].rooms == room2.name
    assert room2.allure_name == "eco" and not room2.shows_setpoint

    # Ce que l'interface web affiche au centre du cercle (JavaScript de mon-logement.html)
    from dataclasses import replace

    def shows(status, **changes):
        return replace(status, **changes).shows_setpoint

    assert shows(room3)                                           # Auto + Confort : nombre
    assert not shows(room3, fp6=True)                             # fil pilote
    for allure in ("0", "1", "2", "4", "5", "6"):                 # Auto + autre allure : mot
        assert not shows(room3, allure=allure), allure
    assert shows(room3, mode="3", allure="0")                     # manuel : nombre + mode
    assert not shows(room3, mode="3", derog_active=True)          # dérogation : « Hors-Gel »
    assert not shows(room3, mode="4") and not shows(room3, mode="DELESTE")
    assert not shows(room3, window="2")                           # fenêtre ouverte = arrêt
    assert replace(room3, mode="DELESTE").allure_name == "shedding"
    assert replace(room3, window="2").allure_name == "off"
    assert {replace(room3, allure=a).allure_name for a in "0123456"} == set(
        api.ALLURE_NAMES.values()
    )

    assert [api.decode_prog_id(c) for c in ("9", ":", ";", "<", "", "x")] == [
        "9", "10", "11", "12", "", "x",
    ]
    print("OK formats réels (6 pages XML de la box)")


def test_real_schedules() -> None:
    """status-prog.xml réel (plannings 2, 4 et 11) : « NULL » = plage vide."""
    def load(pid: str):
        raw = (ROOT / "tests" / "fixtures" / f"real-status-prog-{pid}.xml").read_bytes()
        return api.parse_schedule(api.parse_xml(raw))

    s2, s4, s11 = load("2"), load("4"), load("11")
    assert (s2.planning_id, s4.planning_id, s11.planning_id) == ("2", "4", "11")
    assert s2.days == ((),) * 6 + (((32, 88),),)  # dimanche seulement : 08:00-22:00
    assert s4.days == (
        ((84, 94),), ((30, 36), (84, 94)), ((26, 30), (84, 94)), ((30, 36),), (), (), (),
    )
    assert s11.days == (((40, 88),),) * 7
    assert api.quarter_to_time(26) == "06:30" and api.quarter_to_time(94) == "23:30"
    print("OK relevés réels de status-prog.xml (3 plannings)")

    v = api.validate_day_ranges
    assert v([(68, 90), (28, 36)]) == ((28, 36), (68, 90))  # triées
    assert v([]) == () and v([(0, 4), (8, 96)]) == ((0, 4), (8, 96))
    bad = {
        "schedule_too_many_ranges": [(i * 16, i * 16 + 8) for i in range(6)],
        "schedule_range_too_short": [(10, 13)],
        "schedule_ranges_too_close": [(10, 20), (23, 30)],
        "schedule_range_invalid": [(10, 97)],
    }
    for key, ranges in bad.items():
        try:
            v(ranges)
        except api.EcoControlCommandError as err:
            assert err.key == key, (key, err.key)
        else:
            raise AssertionError(f"{key} attendue pour {ranges}")
    for ranges in ([(10, 20), (15, 30)], [(20, 10)], [(-4, 8)]):  # chevauchement, inversée, négative
        try:
            v(ranges)
        except api.EcoControlCommandError:
            pass
        else:
            raise AssertionError(f"plages {ranges} acceptées à tort")
    assert api.normalize_planning_name("  Séjour  été ") == "SEJOUR ETE"
    for name in ("", "NULL", "x" * 26, "a&b", "été!"):
        try:
            api.normalize_planning_name(name)
        except api.EcoControlCommandError as err:
            assert err.key == "planning_name_invalid"
        else:
            raise AssertionError(f"nom {name!r} accepté à tort")
    print("OK règles de validation des plages et des noms")


async def main() -> None:
    test_real_sample()
    test_real_schedules()
    test_real_formats()
    box = MockBox()
    runner = web.AppRunner(box.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async with aiohttp.ClientSession() as session:
        client = api.EcoControlClient(
            session, f"127.0.0.1:{port}", select_delay=0.01, apply_delay=0.01
        )

        # --- validation + lecture complète
        general = await client.validate()
        assert general.update_available is False and general.wifi == "1"
        snap = await client.fetch_all()
        assert list(snap.rooms) == ["1", "2", "3", "4"], snap.rooms  # « NULL » ignorée
        assert snap.rooms["1"].name == "Séjour"  # accents ISO-8859-1
        assert snap.rooms["3"].offline and not snap.rooms["1"].offline
        assert snap.plannings["3"].name == "TÉLÉTRAVAIL" and "4" not in snap.plannings
        assert snap.vacation == api.Vacation(False, False, None)
        for room_id, status in snap.statuses.items():
            assert status.id == room_id, (room_id, status.id)  # sélection correcte
        s1, s2, s3, s4 = (snap.statuses[k] for k in "1234")
        assert s1.setpoint == 19.5 and s1.can_set_setpoint
        assert s1.available_derogations == ("eco", "comfort", "frost")
        assert not s2.can_set_setpoint  # allure Eco
        assert s3.available_derogations == ("frost",)  # mode manuel
        assert s4.fp6 and not s4.can_set_setpoint
        assert s1.setpoint_bounds == (17.0, 21.0)
        print("OK lecture / sélection / décodage")

        # --- lecture qui retombe sur une autre pièce : nouvel essai
        box.flaky_reads = 1
        status = await client._select_and_read("2")
        assert status.id == "2"
        print("OK nouvel essai si la box décrit une autre pièce")

        # --- consigne : appliquée à la bonne pièce (la dernière sélectionnée était la 2)
        status = await client.set_setpoint("1", 20.5)
        assert status.setpoint == 20.5
        assert calls(box, "/cgi/consigne_piece.cgi") == [{"newConsignePiece": "205"}]
        assert box.rooms["1"]["setpoint"] == "20.5" and box.rooms["2"]["setpoint"] == "16.0"
        await expect_error(client.set_setpoint("1", 25.0), "setpoint_out_of_range")
        await expect_error(client.set_setpoint("2", 19.0), "setpoint_not_settable")
        await expect_error(client.set_setpoint("4", 19.0), "setpoint_not_settable")
        assert len(calls(box, "/cgi/consigne_piece.cgi")) == 1
        page = f"http://127.0.0.1:{port}/mon-logement.html?n_piece=1"
        assert ("/cgi/consigne_piece.cgi", page) in box.referers
        assert ("/xml/status-piece.xml", page) in box.referers
        print("OK consigne (en-tête Referer envoyé comme un navigateur)")

        # --- une page web ouverte ailleurs change la sélection entre deux requêtes
        box.status_reads = 0
        box.steal_after_reads, box.steal_to = 1, "2"  # juste après la 1re lecture
        status = await client.set_setpoint("1", 21.0)
        assert status.id == "1" and status.setpoint == 21.0
        assert box.rooms["1"]["setpoint"] == "21.0" and box.rooms["2"]["setpoint"] == "16.0"
        n_cgi = len(calls(box, "/cgi/consigne_piece.cgi"))
        box.status_reads = 0
        box.wrong_after_reads = 1  # la box ne vise plus la pièce : on n'agit pas
        await expect_error(client.set_setpoint("1", 20.0), "room_selection_lost")
        assert len(calls(box, "/cgi/consigne_piece.cgi")) == n_cgi
        assert box.rooms["1"]["setpoint"] == "21.0"
        box.wrong_after_reads = None
        print("OK sélection volée : commande revérifiée, refusée si la pièce n'est plus la bonne")

        # --- dérogations (heure de la box 10:07 -> quart d'heure 40)
        status = await client.set_derogation("1", "comfort", 2)
        assert status.derog_active and status.derog_allure == "3"
        assert (status.derog_start, status.derog_end) == (40, 48)
        assert box.rooms["2"]["derog"] == "0"
        await expect_error(client.set_derogation("3", "eco", 2), "derogation_unavailable")
        status = await client.set_derogation("3", "frost", 1)
        assert status.derog_allure == "5" and status.derog_end == 44
        status = await client.clear_derogation("1")
        assert not status.derog_active
        n_before = len(calls(box, "/cgi/derog_piece.cgi"))
        await client.clear_derogation("1")  # déjà sans dérogation : aucun appel
        assert len(calls(box, "/cgi/derog_piece.cgi")) == n_before
        box.box_time = (26, 11, 5, 23, 50, 0)  # fin de journée : plafonnée à 24h00
        status = await client.set_derogation("2", "eco", 5)
        assert (status.derog_start, status.derog_end) == (95, 96)
        print("OK dérogations")

        # --- planning
        status = await client.set_planning("2", "3")
        assert status.prog_id == "3"
        await expect_error(client.set_planning("2", "9"), "planning_unknown")
        print("OK planning")

        # --- vacances
        vacation = await client.set_vacation(date(2026, 11, 20))
        assert vacation == api.Vacation(False, True, date(2026, 11, 20))
        assert calls(box, "/cgi/vacances.cgi")[-1] == {
            "HGpermanent": "0", "vacancesActive": "1",
            "vacancesAnnee": "26", "vacancesMois": "11", "vacancesJour": "20",
        }
        vacation = await client.set_vacation(None, permanent_frost=True)
        assert vacation == api.Vacation(True, False, None)
        vacation = await client.set_vacation(None)
        assert vacation == api.Vacation(False, False, None)
        assert calls(box, "/cgi/vacances.cgi")[-1]["vacancesAnnee"] == "NULL"
        await expect_error(client.set_vacation(date(2026, 11, 5)), "vacation_date_invalid")
        await expect_error(client.set_vacation(date(2028, 1, 1)), "vacation_date_invalid")
        print("OK vacances")

        # --- plannings : lecture
        schedule = await client.get_schedule("2")
        assert schedule.planning_id == "2" and schedule.days[0] == ((28, 36), (68, 90))
        assert schedule.days[6] == ((32, 88),) and schedule.days[3] == ()
        page = f"http://127.0.0.1:{port}/mes-plannings.html?n_prog=2"
        assert ("/xml/status-prog.xml", page) in box.referers
        await expect_error(client.get_schedule("1"), "planning_no_schedule")
        await expect_error(client.get_schedule("9"), "planning_unknown")
        box.flaky_prog_reads = 1
        assert (await client.get_schedule("2")).planning_id == "2"
        print("OK plannings : lecture / sélection / nouvel essai")

        # --- plannings : écriture d'un jour (clear puis plages de la dernière à la première)
        box.log.clear()
        result = await client.set_schedule_days("2", {1: [(70, 90), (28, 36)]})
        assert result.days[1] == ((28, 36), (70, 90)) and result.days[0] == schedule.days[0]
        writes = [q for p, q in box.log if p == "/cgi/prog.cgi"]
        assert [(w["id_plage_cgi"], w["heureDebut_prog_cgi"], w["heureFin_prog_cgi"]) for w in writes] == [
            ("5", "0", "0"), ("1", "70", "90"), ("0", "28", "36"),
        ], writes
        assert all(w["id_prog_cgi"] == "2" and w["id_jour_cgi"] == "1" for w in writes)
        page = f"http://127.0.0.1:{port}/mes-plannings.html?n_prog=2"
        assert ("/cgi/prog.cgi", page) in box.referers
        # identique : aucune écriture
        box.log.clear()
        await client.set_schedule_days("2", {1: [(28, 36), (70, 90)]})
        assert [p for p, _ in box.log if p == "/cgi/prog.cgi"] == []
        # jour vidé : un seul appel « effacer »
        box.log.clear()
        result = await client.set_schedule_days("2", {6: []})
        assert result.days[6] == () and len([1 for p, _ in box.log if p == "/cgi/prog.cgi"]) == 1
        # plusieurs jours en une fois (copie)
        result = await client.set_schedule_days("3", {d: [(24, 40)] for d in (0, 2, 4)})
        assert [result.days[d] for d in range(5)] == [((24, 40),), (), ((24, 40),), (), ((24, 40),)]
        assert box.schedules["2"][2] == {}  # l'autre planning n'a pas bougé
        # règles de la box : rien n'est envoyé si une plage est invalide
        box.log.clear()
        for ranges, key in (
            ([(10, 13)], "schedule_range_too_short"),
            ([(10, 20), (23, 30)], "schedule_ranges_too_close"),
            ([(i * 16, i * 16 + 8) for i in range(6)], "schedule_too_many_ranges"),
        ):
            await expect_error(client.set_schedule_days("2", {2: ranges}), key)
        await expect_error(client.set_schedule_days("2", {7: []}), "schedule_day_invalid")
        await expect_error(client.set_schedule_days("9", {0: []}), "planning_unknown")
        await expect_error(client.set_schedule_days("1", {0: []}), "planning_no_schedule")
        assert [p for p, _ in box.log if p == "/cgi/prog.cgi"] == []
        # la box n'applique pas : détecté à la relecture
        box.ignore_prog_cgi = True
        await expect_error(client.set_schedule_days("2", {4: [(40, 60)]}), "schedule_not_applied")
        box.ignore_prog_cgi = False
        # sélection volée avant l'écriture : refusé
        box.flaky_prog_reads = 0
        try:
            box.flaky_prog_reads = 99
            await client._confirm_schedule_selected("2")
        except api.EcoControlCommandError as err:
            assert err.key == "planning_selection_lost"
        else:
            raise AssertionError("planning_selection_lost attendue")
        box.flaky_prog_reads = 0
        print("OK plannings : écriture / validation / relecture")

        # --- plannings : créer, renommer, supprimer
        created = await client.create_planning("  Séjour été ")
        assert (created.id, created.name) == ("4", "SEJOUR ETE"), created
        await expect_error(client.create_planning("sejour ete"), "planning_name_taken")
        await expect_error(client.create_planning("a&b"), "planning_name_invalid")
        renamed = await client.rename_planning("4", "Bureau")
        assert renamed.name == "BUREAU" and box.plannings["4"][0] == "BUREAU"
        await expect_error(client.rename_planning("4", "semaine"), "planning_name_taken")
        await expect_error(client.rename_planning("1", "Autre"), "planning_protected")
        await expect_error(client.delete_planning("1"), "planning_protected")
        await expect_error(client.delete_planning("2"), "planning_in_use")  # des pièces l'utilisent
        await expect_error(client.delete_planning("9"), "planning_unknown")
        await client.delete_planning("4")
        assert box.plannings["4"][0] == "NULL"
        for i in range(4, 13):  # remplit jusqu'à la limite (12 plannings)
            await client.create_planning(f"Test {i}")
        await expect_error(client.create_planning("Un de trop"), "planning_limit")
        for i in range(4, 13):
            await client.delete_planning(str(i))
        print("OK plannings : créer / renommer / supprimer / limites")

        # --- erreurs HTTP, fenêtre ouverte, jamais de mise à jour firmware
        box.status_code_for["/xml/status-general.xml"] = 404
        try:
            await client.validate()
        except api.EcoControlConnectionError:
            pass
        else:
            raise AssertionError("EcoControlConnectionError attendue")
        box.status_code_for.clear()
        box.rooms["1"]["window"] = "2"
        status = (await client.fetch_all()).statuses["1"]
        assert status.window_open and status.effective_mode == api.MODE_OFF
        assert not status.can_set_setpoint and status.available_derogations == ()
        assert calls(box, "/cgi/maj_ok.cgi") == []
        print("OK erreurs / fenêtre ouverte / pas de maj firmware")

    await runner.cleanup()
    print("\nTous les tests passent.")


asyncio.run(main())
