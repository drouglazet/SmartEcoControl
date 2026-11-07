"""Client asynchrone pour la box locale Smart ECOcontrol.

Ce module ne dépend pas de Home Assistant : il peut être testé seul.

Ce que l'on sait de la box (déduit du JavaScript de ses pages web) :

* lecture  : fichiers XML sous /xml/ (status-piece.xml, liste-pieces.xml...)
* écriture : requêtes GET sur /cgi/*.cgi
* la box est « à état » : status-piece.xml décrit la pièce affichée en dernier
  via ``mon-logement.html?n_piece=N``, et consigne_piece.cgi / derog_piece.cgi
  s'appliquent à cette pièce-là (aucun identifiant de pièce dans leurs
  paramètres). Chaque lecture/écriture commence donc par sélectionner la pièce,
  et tous les accès sont sérialisés par un verrou.

JAMAIS appelé ici : cgi/maj_ok.cgi (déclenche une mise à jour du firmware).
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import aiohttp

_LOGGER = logging.getLogger(__name__)

# mode_piece
MODE_ECO = "0"
MODE_COMFORT_MINUS_2 = "1"
MODE_COMFORT_MINUS_1 = "2"
MODE_COMFORT = "3"
MODE_OFF = "4"
MODE_FROST = "5"
MODE_AUTO = "7"
MODE_INTERNAL_PROG = "8"
MODE_SHEDDING = "DELESTE"

# allure_piece / allure_derog
ALLURE_ECO = "0"
ALLURE_COMFORT = "3"
ALLURE_OFF = "4"
ALLURE_FROST = "5"

# Nom de chaque allure (allure_piece) : utilisé par le capteur, le thermostat et la carte
ALLURE_NAMES = {
    "0": "eco",
    "1": "comfort_minus_2",
    "2": "comfort_minus_1",
    "3": "comfort",
    "4": "off",
    "5": "frost_protection",
    "6": "comfort_plus_1",
}

# Dérogations possibles depuis l'interface web : clé -> allureDerog
DEROG_ALLURE = {"eco": "0", "comfort": "3", "frost": "5"}
ALLURE_TO_DEROG = {value: key for key, value in DEROG_ALLURE.items()}

NO_PLANNING_ID = "1"  # « Sans planning » : planning par défaut, non supprimable
QUARTERS_PER_DAY = 96
TEMP_MIN = 7.0
TEMP_MAX = 28.0
DEFAULT_MARGIN = 2.0

# Règles de l'éditeur de plannings de la box (mes-plannings.html)
DAYS_PER_WEEK = 7  # lundi = 0 ... dimanche = 6
MAX_RANGES_PER_DAY = 5  # plages de confort par jour
MIN_RANGE_QUARTERS = 4  # une plage de confort dure au moins 1 h
MIN_GAP_QUARTERS = 4  # au moins 1 h entre deux plages
MAX_PLANNINGS = 12  # « Sans planning » comprise
PLANNING_NAME_MAX = 25


class EcoControlError(Exception):
    """Erreur de base."""


class EcoControlConnectionError(EcoControlError):
    """La box est injoignable ou répond une erreur HTTP."""


class EcoControlProtocolError(EcoControlError):
    """La box a répondu quelque chose d'inattendu."""


class EcoControlCommandError(EcoControlError):
    """Commande refusée avant envoi (règles de l'interface web non respectées)."""

    def __init__(self, key: str, **placeholders: object) -> None:
        super().__init__(f"{key}: {placeholders}" if placeholders else key)
        self.key = key
        self.placeholders = {k: str(v) for k, v in placeholders.items()}


# ---------------------------------------------------------------- modèles


@dataclass(frozen=True, slots=True)
class General:
    first_connection: bool
    init_in_progress: bool
    wifi: str
    tic: str
    update_available: bool


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    name: str
    offline: bool


@dataclass(frozen=True, slots=True)
class Planning:
    id: str
    name: str
    rooms: str


Ranges = tuple[tuple[int, int], ...]  # plages de confort : (début, fin) en quarts d'heure


@dataclass(frozen=True, slots=True)
class Schedule:
    """Plages de confort d'un planning : 7 jours (lundi = 0), le reste est en Éco."""

    planning_id: str
    days: tuple[Ranges, ...]


@dataclass(frozen=True, slots=True)
class Vacation:
    permanent_frost: bool
    active: bool
    until: date | None


@dataclass(frozen=True, slots=True)
class RoomStatus:
    id: str
    name: str
    prog_id: str
    mode: str
    allure: str
    setpoint: float | None
    preferred: float | None
    window: str
    presence_detection: bool
    keyboard_lock: bool
    fp6: bool
    derog_active: bool
    derog_allure: str
    derog_start: int | None
    derog_end: int | None
    margin_plus: float
    margin_minus: float

    @property
    def window_open(self) -> bool:
        return self.window == "2"

    @property
    def effective_mode(self) -> str:
        """Comme l'interface web : fenêtre ouverte = arrêt."""
        return MODE_OFF if self.window_open else self.mode

    @property
    def effective_allure(self) -> str:
        return ALLURE_OFF if self.window_open else self.allure

    @property
    def can_set_setpoint(self) -> bool:
        """Boutons +/- de l'interface : mode Auto, allure Confort, pas de fil pilote."""
        return (
            self.effective_mode == MODE_AUTO
            and self.effective_allure == ALLURE_COMFORT
            and not self.fp6
        )

    @property
    def allure_name(self) -> str:
        """Allure réellement appliquée (« shedding » en cas de délestage)."""
        if self.mode == MODE_SHEDDING:
            return "shedding"
        return ALLURE_NAMES.get(self.effective_allure, "unknown")

    @property
    def shows_setpoint(self) -> bool:
        """L'interface web de la box affiche-t-elle une température ?

        En mode Auto, seule l'allure Confort affiche un nombre ; Eco, Confort -1/-2/+1,
        Hors-gel et Arrêt affichent un mot. En mode manuel le nombre est affiché,
        sauf à l'arrêt, en délestage ou pendant une dérogation Hors-gel.
        """
        mode = self.effective_mode
        if self.fp6 or self.setpoint is None:
            return False
        if mode == MODE_AUTO:
            return self.effective_allure == ALLURE_COMFORT
        return mode not in (MODE_OFF, MODE_SHEDDING) and not self.derog_active

    @property
    def setpoint_bounds(self) -> tuple[float, float]:
        """Plage acceptée : température préférée +/- encadrement, entre 7 et 28 °C."""
        if self.preferred is None:
            return TEMP_MIN, TEMP_MAX
        return (
            max(TEMP_MIN, self.preferred - self.margin_minus),
            min(TEMP_MAX, self.preferred + self.margin_plus),
        )

    @property
    def available_derogations(self) -> tuple[str, ...]:
        """Dérogations proposées par l'interface web selon le mode de la pièce."""
        mode = self.effective_mode
        if mode == MODE_AUTO and self.prog_id != NO_PLANNING_ID:
            return ("eco", "comfort", "frost")
        if mode not in (MODE_OFF, MODE_FROST):
            return ("frost",)
        return ()


@dataclass(frozen=True, slots=True)
class BoxSnapshot:
    general: General
    rooms: dict[str, Room]
    statuses: dict[str, RoomStatus]
    plannings: dict[str, Planning]
    vacation: Vacation


# ---------------------------------------------------------------- parsing

_XML_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)


def parse_xml(raw: bytes) -> ET.Element:
    """Décode (UTF-8, sinon ISO-8859-1 comme les pages de la box) puis parse."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("iso-8859-1")
    text = _XML_DECLARATION.sub("", text, count=1)
    try:
        return ET.fromstring(text)
    except ET.ParseError as err:
        raise EcoControlProtocolError(f"XML invalide : {err}") from err


def _text(element: ET.Element, tag: str, default: str = "") -> str:
    child = element.find(f".//{tag}")
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _float(value: str, default: float | None = None) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return default


def _int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def decode_prog_id(raw: str) -> str:
    """Numéro de planning tel que l'écrit status-piece.xml.

    Défaut du micrologiciel observé sur la vraie box : à partir du planning 10, le
    numéro est écrit comme UN caractère, « '0' + numéro » (10 -> « : », 11 -> « ; »,
    12 -> « < »…). Pour la pièce SALON, « ; » correspond bien au planning 11
    « SALON VACANCES » dont liste-prog.xml cite justement le SALON.
    """
    if len(raw) == 1 and not raw.isdigit():
        number = ord(raw) - ord("0")
        if 10 <= number <= 40:
            return str(number)
    return raw


def quarter_to_time(quarter: int) -> str:
    """Index de quart d'heure (0-96) -> « HH:MM »."""
    return f"{quarter // 4:02d}:{quarter % 4 * 15:02d}"


def parse_general(root: ET.Element) -> General:
    return General(
        first_connection=_text(root, "premiere_connexion") == "1",
        init_in_progress=_text(root, "init_en_cours", "0") != "0",
        wifi=_text(root, "etat_wifi"),
        tic=_text(root, "etat_tic"),
        update_available=_text(root, "maj_ok", "1") == "0",
    )


def parse_rooms(root: ET.Element) -> dict[str, Room]:
    rooms: dict[str, Room] = {}
    for piece in root.iter("piece"):
        room_id = piece.get("id")
        name = _text(piece, "nom_piece")
        if room_id is None or not name or name == "NULL":
            continue
        offline = any(th.get("offline") == "1" for th in piece.iter("th"))
        rooms[room_id] = Room(room_id, name, offline)
    return rooms


def parse_plannings(root: ET.Element) -> dict[str, Planning]:
    plannings: dict[str, Planning] = {}
    for prog in root.iter("prog"):
        prog_id = prog.get("id")
        name = _text(prog, "nom_prog")
        if prog_id is None or not name or name == "NULL":
            continue
        rooms = _text(prog, "liste_pieces_prog")
        plannings[prog_id] = Planning(prog_id, name, "" if rooms == "NULL" else rooms)
    return plannings


def parse_schedule(root: ET.Element) -> Schedule:
    """status-prog.xml : <prog><id_prog/><jour_prog id="0-6"><point_prog id="0-4">...

    Une plage vide est notée « NULL » (début et fin). Les heures sont des quarts
    d'heure, de 0 à 96.
    """
    planning_id = decode_prog_id(_text(root, "id_prog"))
    if not planning_id:
        raise EcoControlProtocolError("status-prog.xml sans <id_prog>")
    days: list[Ranges] = [() for _ in range(DAYS_PER_WEEK)]
    for day_element in root.iter("jour_prog"):
        day = _int(day_element.get("id") or "")
        if day is None or not 0 <= day < DAYS_PER_WEEK:
            continue
        ranges = []
        for point in day_element.iter("point_prog"):
            start = _int(_text(point, "heureDebut"))
            end = _int(_text(point, "heureFin"))
            if start is None or end is None or not 0 <= start < end <= QUARTERS_PER_DAY:
                continue
            ranges.append((start, end))
        days[day] = tuple(sorted(ranges))
    return Schedule(planning_id, tuple(days))


def validate_day_ranges(ranges: Sequence[Sequence[int]]) -> Ranges:
    """Mêmes règles que l'éditeur de la box ; renvoie les plages triées."""
    ordered = tuple(sorted((int(start), int(end)) for start, end in ranges))
    if len(ordered) > MAX_RANGES_PER_DAY:
        raise EcoControlCommandError(
            "schedule_too_many_ranges", maximum=MAX_RANGES_PER_DAY
        )
    previous_end: int | None = None
    for start, end in ordered:
        span = f"{quarter_to_time(start)}-{quarter_to_time(end)}"
        if not 0 <= start < end <= QUARTERS_PER_DAY:
            raise EcoControlCommandError("schedule_range_invalid", range=span)
        if end - start < MIN_RANGE_QUARTERS:
            raise EcoControlCommandError("schedule_range_too_short", range=span)
        if previous_end is not None and start - previous_end < MIN_GAP_QUARTERS:
            raise EcoControlCommandError("schedule_ranges_too_close", range=span)
        previous_end = end
    return ordered


def normalize_planning_name(name: str) -> str:
    """Nom accepté par la box : lettres, chiffres et espaces, en majuscules, 25 max."""
    cleaned = " ".join(name.split())
    ascii_name = (
        unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode().upper()
    )
    if (
        not ascii_name
        or ascii_name == "NULL"
        or len(ascii_name) > PLANNING_NAME_MAX
        or not re.fullmatch(r"[A-Z0-9 ]+", ascii_name)
    ):
        raise EcoControlCommandError("planning_name_invalid", maximum=PLANNING_NAME_MAX)
    return ascii_name


def parse_vacation(root: ET.Element) -> Vacation:
    permanent = _text(root, "hors_gel_permanent", "0") == "1"
    active = _text(root, "vacancesActive", "0") == "1"
    until = None
    if active:
        year = _int(_text(root, "vacancesAnnee"))
        month = _int(_text(root, "vacancesMois"))
        day = _int(_text(root, "vacancesJour"))
        if year is not None and month is not None and day is not None:
            if year < 100:
                year += 2000
            try:
                until = date(year, month, day)
            except ValueError:
                until = None
    return Vacation(permanent, active, until)


def parse_box_time(root: ET.Element) -> datetime:
    element = next(root.iter("Date"), None)
    if element is None:
        raise EcoControlProtocolError("status-date.xml : balise Date absente")
    values = {
        key: _int(_text(element, key))
        for key in (
            "Date_Annee",
            "Date_Mois",
            "Date_Jour",
            "Date_Heure",
            "Date_Minute",
            "Date_Seconde",
        )
    }
    if any(value is None for value in values.values()):
        raise EcoControlProtocolError("status-date.xml : date illisible")
    try:
        return datetime(
            2000 + values["Date_Annee"],
            values["Date_Mois"],
            values["Date_Jour"],
            values["Date_Heure"],
            values["Date_Minute"],
            values["Date_Seconde"],
        )
    except ValueError as err:
        raise EcoControlProtocolError(f"status-date.xml : {err}") from err


def parse_room_status(root: ET.Element) -> RoomStatus:
    element = next(root.iter("reponse_piece"), None)
    if element is None:
        raise EcoControlProtocolError("status-piece.xml : balise reponse_piece absente")
    return RoomStatus(
        id=_text(element, "id_piece"),
        name=_text(element, "nom_piece"),
        prog_id=decode_prog_id(_text(element, "prog_piece")),
        mode=_text(element, "mode_piece"),
        allure=_text(element, "allure_piece"),
        setpoint=_float(_text(element, "consigne_piece")),
        preferred=_float(_text(element, "consigne_preferee")),
        window=_text(element, "fenetre_piece", "0"),
        presence_detection=_text(element, "presence_piece", "0") != "0",
        keyboard_lock=_text(element, "verrou_piece", "0") != "0",
        fp6=_text(element, "fp6_piece", "0") == "1",
        derog_active=_text(element, "derog_piece", "0") == "1",
        derog_allure=_text(element, "allure_derog"),
        derog_start=_int(_text(element, "heure_debut_derog")),
        derog_end=_int(_text(element, "heure_fin_derog")),
        # (sic) : « encardrement » est la graphie de la box
        margin_plus=_float(_text(element, "encardrement_temp_plus"), DEFAULT_MARGIN),
        margin_minus=_float(_text(element, "encardrement_temp_moins"), DEFAULT_MARGIN),
    )


# ---------------------------------------------------------------- client


class EcoControlClient:
    """Accès HTTP à la box. Tous les accès sont sérialisés (box à état)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        timeout: float = 10.0,
        select_delay: float = 0.6,
        apply_delay: float = 0.8,
    ) -> None:
        self._session = session
        self._base = f"http://{host}"
        self._timeout = timeout
        self._select_delay = select_delay
        self._apply_delay = apply_delay
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ bas niveau

    def _room_page(self, room_id: str) -> str:
        return f"{self._base}/mon-logement.html?n_piece={room_id}"

    async def _get(
        self,
        path: str,
        params: dict[str, object] | None = None,
        referer: str | None = None,
    ) -> bytes:
        """GET uniquement : la box répond 501 aux requêtes HEAD.

        Les pages de la box sont statiques (le numéro de pièce n'y est lu que par
        le JavaScript) : on envoie donc les mêmes en-têtes qu'un navigateur
        (X-Requested-With comme jQuery, Referer = page de la pièce) au cas où la
        box s'en servirait pour savoir de quelle pièce on parle.
        """
        url = f"{self._base}/{path}"
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if referer:
            headers["Referer"] = referer
        last_error: BaseException | None = None
        for attempt in range(2):
            try:
                async with asyncio.timeout(self._timeout):
                    async with self._session.get(
                        url, params=params, headers=headers
                    ) as response:
                        if response.status != 200:
                            raise EcoControlConnectionError(
                                f"HTTP {response.status} sur /{path}"
                            )
                        return await response.read()
            except EcoControlConnectionError:
                raise
            except (aiohttp.ClientError, TimeoutError) as err:
                last_error = err
                if attempt == 0:
                    await asyncio.sleep(0.5)
        raise EcoControlConnectionError(
            f"/{path} injoignable ({last_error!r})"
        ) from last_error

    async def _get_xml(self, path: str, referer: str | None = None) -> ET.Element:
        return parse_xml(await self._get(path, referer=referer))

    # ------------------------------------------------------- lectures (sans verrou)

    async def _read_general(self) -> General:
        return parse_general(await self._get_xml("xml/status-general.xml"))

    async def _read_rooms(self) -> dict[str, Room]:
        return parse_rooms(await self._get_xml("xml/liste-pieces.xml"))

    async def _read_plannings(self) -> dict[str, Planning]:
        return parse_plannings(await self._get_xml("xml/liste-prog.xml"))

    async def _read_vacation(self) -> Vacation:
        return parse_vacation(await self._get_xml("xml/vacances.xml"))

    async def _read_box_time(self) -> datetime:
        return parse_box_time(await self._get_xml("xml/status-date.xml"))

    async def _select_and_read(self, room_id: str) -> RoomStatus:
        """Sélectionne la pièce (comme l'interface web) puis lit son état."""
        last_seen = ""
        for _ in range(3):
            await self._get("mon-logement.html", {"n_piece": room_id})
            await asyncio.sleep(self._select_delay)
            status = parse_room_status(
                await self._get_xml(
                    "xml/status-piece.xml", referer=self._room_page(room_id)
                )
            )
            if status.id == room_id:
                return status
            last_seen = status.id
            _LOGGER.debug(
                "status-piece.xml décrit la pièce %s au lieu de %s, nouvel essai",
                status.id,
                room_id,
            )
            await asyncio.sleep(self._select_delay)
        raise EcoControlProtocolError(
            f"La box décrit la pièce {last_seen!r} au lieu de {room_id!r} "
            "(interface web ouverte en parallèle ?)"
        )

    def _schedule_page(self, planning_id: str) -> str:
        return f"{self._base}/mes-plannings.html?n_prog={planning_id}"

    async def _select_and_read_schedule(self, planning_id: str) -> Schedule:
        """Sélectionne le planning (page mes-plannings.html) puis lit status-prog.xml."""
        if planning_id == NO_PLANNING_ID:
            raise EcoControlCommandError("planning_no_schedule")
        last_seen = ""
        for _ in range(3):
            await self._get("mes-plannings.html", {"n_prog": planning_id})
            await asyncio.sleep(self._select_delay)
            schedule = parse_schedule(
                await self._get_xml(
                    "xml/status-prog.xml", referer=self._schedule_page(planning_id)
                )
            )
            if schedule.planning_id == planning_id:
                return schedule
            last_seen = schedule.planning_id
            await asyncio.sleep(self._select_delay)
        raise EcoControlProtocolError(
            f"La box décrit le planning {last_seen!r} au lieu de {planning_id!r} "
            "(interface web ouverte en parallèle ?)"
        )

    async def _confirm_schedule_selected(self, planning_id: str) -> None:
        """Juste avant d'écrire : la box décrit-elle toujours ce planning ?"""
        for attempt in range(3):
            schedule = parse_schedule(
                await self._get_xml(
                    "xml/status-prog.xml", referer=self._schedule_page(planning_id)
                )
            )
            if schedule.planning_id == planning_id:
                return
            if attempt < 2:
                await self._get("mes-plannings.html", {"n_prog": planning_id})
                await asyncio.sleep(self._select_delay)
        raise EcoControlCommandError("planning_selection_lost")

    async def _prog_cgi(
        self, planning_id: str, slot: int, day: int, start: int, end: int
    ) -> None:
        await self._get(
            "cgi/prog.cgi",
            {
                "id_prog_cgi": planning_id,
                "id_plage_cgi": slot,
                "id_jour_cgi": day,
                "heureDebut_prog_cgi": start,
                "heureFin_prog_cgi": end,
            },
            referer=self._schedule_page(planning_id),
        )
        await asyncio.sleep(min(self._apply_delay, 0.25))

    async def _write_day(self, planning_id: str, day: int, ranges: Ranges) -> None:
        """Remplace les plages d'un jour, comme la copie de jour de la box :
        « 5 » efface le jour, puis une écriture par plage, de la dernière à la première.
        """
        await self._prog_cgi(planning_id, 5, day, 0, 0)
        for slot in reversed(range(len(ranges))):
            start, end = ranges[slot]
            await self._prog_cgi(planning_id, slot, day, start, end)

    async def _confirm_selected(self, room_id: str) -> None:
        """Juste avant une commande : la box vise-t-elle toujours cette pièce ?

        Les CGI de consigne et de dérogation n'ont pas de paramètre « pièce » : ils
        s'appliquent à la dernière pièce sélectionnée. Une page de l'interface web
        ouverte ailleurs peut changer cette sélection entre-temps (elle recharge sa
        propre pièce dès qu'elle en voit une autre), donc on revérifie, et on
        refuse d'agir plutôt que de risquer de régler la mauvaise pièce.
        """
        for attempt in range(3):
            status = parse_room_status(
                await self._get_xml(
                    "xml/status-piece.xml", referer=self._room_page(room_id)
                )
            )
            if status.id == room_id:
                return
            if attempt < 2:
                await self._get("mon-logement.html", {"n_piece": room_id})
                await asyncio.sleep(self._select_delay)
        raise EcoControlCommandError("room_selection_lost")

    # ------------------------------------------------------------ API publique

    async def validate(self) -> General:
        """Vérifie que l'adresse est bien celle d'une box Smart ECOcontrol."""
        async with self._lock:
            general = await self._read_general()
            await self._read_rooms()
        return general

    async def fetch_all(
        self, previous: dict[str, RoomStatus] | None = None
    ) -> BoxSnapshot:
        async with self._lock:
            general = await self._read_general()
            rooms = await self._read_rooms()
            plannings = await self._read_plannings()
            vacation = await self._read_vacation()
            statuses: dict[str, RoomStatus] = {}
            for room_id in rooms:
                try:
                    statuses[room_id] = await self._select_and_read(room_id)
                except EcoControlProtocolError as err:
                    _LOGGER.warning("Pièce %s illisible : %s", room_id, err)
                    if previous and room_id in previous:
                        statuses[room_id] = previous[room_id]
            if rooms and not statuses:
                raise EcoControlProtocolError("Aucune pièce lisible")
        return BoxSnapshot(general, rooms, statuses, plannings, vacation)

    async def set_setpoint(self, room_id: str, temperature: float) -> RoomStatus:
        async with self._lock:
            status = await self._select_and_read(room_id)
            if not status.can_set_setpoint:
                raise EcoControlCommandError("setpoint_not_settable")
            low, high = status.setpoint_bounds
            if not low - 1e-6 <= temperature <= high + 1e-6:
                raise EcoControlCommandError(
                    "setpoint_out_of_range",
                    requested=temperature,
                    minimum=low,
                    maximum=high,
                )
            await self._confirm_selected(room_id)
            await self._get(
                "cgi/consigne_piece.cgi",
                {"newConsignePiece": int(round(temperature * 10))},
                referer=self._room_page(room_id),
            )
            await asyncio.sleep(self._apply_delay)
            return await self._select_and_read(room_id)

    async def set_derogation(
        self, room_id: str, kind: str, duration_hours: float
    ) -> RoomStatus:
        """Dérogation (eco / comfort / frost) jusqu'à maintenant + durée, au plus minuit."""
        if kind not in DEROG_ALLURE:
            raise EcoControlCommandError("derogation_unknown", kind=kind)
        async with self._lock:
            status = await self._select_and_read(room_id)
            if kind not in status.available_derogations:
                raise EcoControlCommandError(
                    "derogation_unavailable", kind=kind, mode=status.effective_mode
                )
            now = await self._read_box_time()
            start = now.hour * 4 + now.minute // 15
            end = min(start + max(1, round(duration_hours * 4)), QUARTERS_PER_DAY)
            await self._confirm_selected(room_id)
            await self._get(
                "cgi/derog_piece.cgi",
                {
                    "pieceDerog": "1",
                    "allureDerog": DEROG_ALLURE[kind],
                    "heureDebutDerog": start,
                    "heureFinDerog": end,
                },
                referer=self._room_page(room_id),
            )
            await asyncio.sleep(self._apply_delay)
            return await self._select_and_read(room_id)

    async def clear_derogation(self, room_id: str) -> RoomStatus:
        async with self._lock:
            status = await self._select_and_read(room_id)
            if not status.derog_active:
                return status
            await self._confirm_selected(room_id)
            await self._get(
                "cgi/derog_piece.cgi",
                {
                    "pieceDerog": "0",
                    "allureDerog": "0",
                    "heureDebutDerog": "0",
                    "heureFinDerog": "0",
                },
                referer=self._room_page(room_id),
            )
            await asyncio.sleep(self._apply_delay)
            return await self._select_and_read(room_id)

    async def set_planning(self, room_id: str, planning_id: str) -> RoomStatus:
        async with self._lock:
            plannings = await self._read_plannings()
            if planning_id not in plannings:
                raise EcoControlCommandError("planning_unknown", planning=planning_id)
            await self._get(
                "cgi/deplace_piece.cgi",
                {"nPiece": room_id, "nZone": planning_id},
                referer=self._room_page(room_id),
            )
            await asyncio.sleep(self._apply_delay)
            return await self._select_and_read(room_id)

    # ---------------------------------------------------- plannings de chauffe

    async def list_plannings(self) -> dict[str, Planning]:
        async with self._lock:
            return await self._read_plannings()

    async def get_schedule(self, planning_id: str) -> Schedule:
        """Plages de confort d'un planning, relues sur la box."""
        async with self._lock:
            if planning_id not in await self._read_plannings():
                raise EcoControlCommandError("planning_unknown", planning=planning_id)
            return await self._select_and_read_schedule(planning_id)

    async def set_schedule_days(
        self, planning_id: str, changes: Mapping[int, Sequence[Sequence[int]]]
    ) -> Schedule:
        """Remplace les plages de confort de certains jours, puis relit pour vérifier."""
        validated: dict[int, Ranges] = {}
        for day, ranges in changes.items():
            if not 0 <= day < DAYS_PER_WEEK:
                raise EcoControlCommandError("schedule_day_invalid", day=day)
            validated[day] = validate_day_ranges(ranges)
        async with self._lock:
            if planning_id not in await self._read_plannings():
                raise EcoControlCommandError("planning_unknown", planning=planning_id)
            current = await self._select_and_read_schedule(planning_id)
            todo = {d: r for d, r in validated.items() if current.days[d] != r}
            if not todo:
                return current
            await self._confirm_schedule_selected(planning_id)
            for day, ranges in todo.items():
                await self._write_day(planning_id, day, ranges)
            await asyncio.sleep(self._apply_delay)
            result = await self._select_and_read_schedule(planning_id)
        for day, ranges in todo.items():
            if result.days[day] != ranges:
                raise EcoControlCommandError(
                    "schedule_not_applied",
                    day=day,
                    expected=ranges,
                    found=result.days[day],
                )
        return result

    async def create_planning(self, name: str) -> Planning:
        clean = normalize_planning_name(name)
        async with self._lock:
            before = await self._read_plannings()
            if len(before) >= MAX_PLANNINGS:
                raise EcoControlCommandError("planning_limit", maximum=MAX_PLANNINGS)
            if any(p.name.upper() == clean for p in before.values()):
                raise EcoControlCommandError("planning_name_taken", name=clean)
            await self._get(
                "cgi/planning.cgi",
                {"id_prog_cgi": "0", "nom_prog_cgi": clean, "suppr_prog_cgi": "NULL"},
            )
            await asyncio.sleep(self._apply_delay)
            after = await self._read_plannings()
        created = [p for pid, p in after.items() if pid not in before]
        if len(created) != 1 or created[0].name.upper() != clean:
            raise EcoControlCommandError("planning_not_applied")
        return created[0]

    async def rename_planning(self, planning_id: str, name: str) -> Planning:
        clean = normalize_planning_name(name)
        if planning_id == NO_PLANNING_ID:
            raise EcoControlCommandError("planning_protected")
        async with self._lock:
            plannings = await self._read_plannings()
            if planning_id not in plannings:
                raise EcoControlCommandError("planning_unknown", planning=planning_id)
            if any(
                p.name.upper() == clean and pid != planning_id
                for pid, p in plannings.items()
            ):
                raise EcoControlCommandError("planning_name_taken", name=clean)
            await self._get(
                "cgi/planning.cgi",
                {
                    "id_prog_cgi": planning_id,
                    "nom_prog_cgi": clean,
                    "suppr_prog_cgi": "NULL",
                },
            )
            await asyncio.sleep(self._apply_delay)
            after = await self._read_plannings()
        renamed = after.get(planning_id)
        if renamed is None or renamed.name.upper() != clean:
            raise EcoControlCommandError("planning_not_applied")
        return renamed

    async def delete_planning(self, planning_id: str) -> None:
        if planning_id == NO_PLANNING_ID:
            raise EcoControlCommandError("planning_protected")
        async with self._lock:
            plannings = await self._read_plannings()
            planning = plannings.get(planning_id)
            if planning is None:
                raise EcoControlCommandError("planning_unknown", planning=planning_id)
            if planning.rooms:  # la box l'autorise, mais on évite de laisser des pièces sans planning
                raise EcoControlCommandError(
                    "planning_in_use", planning=planning.name, rooms=planning.rooms
                )
            await self._get(
                "cgi/planning.cgi",
                {"id_prog_cgi": planning_id, "nom_prog_cgi": "NULL", "suppr_prog_cgi": "1"},
            )
            await asyncio.sleep(self._apply_delay)
            after = await self._read_plannings()
        if planning_id in after:
            raise EcoControlCommandError("planning_not_applied")

    async def set_vacation(
        self, until: date | None, permanent_frost: bool = False
    ) -> Vacation:
        """Vacances jusqu'à une date, hors-gel permanent, ou tout désactiver."""
        async with self._lock:
            if until is not None:
                today = (await self._read_box_time()).date()
                if not today < until <= today + timedelta(days=366):
                    raise EcoControlCommandError(
                        "vacation_date_invalid", date=until.isoformat()
                    )
            await self._get(
                "cgi/vacances.cgi",
                {
                    "HGpermanent": "1" if permanent_frost else "0",
                    "vacancesActive": "1" if until else "0",
                    "vacancesAnnee": f"{until.year % 100:02d}" if until else "NULL",
                    "vacancesMois": str(until.month) if until else "NULL",
                    "vacancesJour": str(until.day) if until else "NULL",
                },
            )
            await asyncio.sleep(self._apply_delay)
            return await self._read_vacation()
