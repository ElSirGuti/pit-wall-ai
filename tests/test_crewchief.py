import ctypes

from pit_wall_ai.telemetry.crewchief import CrewChiefFeeder, PageCrewChief, to_payload
from pit_wall_ai.telemetry.csp_bridge import BridgeReceiver


def page(cars):
    p = PageCrewChief()
    p.numVehicles = len(cars)
    p.focusVehicle = 0
    for i, (name, pos, spline, speed) in enumerate(cars):
        v = p.vehicleInfo[i]
        v.carId, v.isConnected = i, 1
        v.driverName = name.encode()
        v.carLeaderboardPosition = pos
        v.spLineLength = spline
        v.speedMS = speed / 3.6
        v.bestLapMS, v.lastLapTimeMS, v.currentLapTimeMS = 90000, 91000, 40000
    return p


class FakeView:
    def __init__(self, source):
        self.source, self.closed = source, False

    def read_into(self, dest):
        ctypes.memmove(ctypes.addressof(dest), ctypes.addressof(self.source), ctypes.sizeof(dest))

    def close(self):
        self.closed = True


class FakeBackend:
    def __init__(self, source=None):
        self.source = source

    def open_page(self, tag, size):
        from pit_wall_ai.telemetry.source import SourceUnavailable

        if self.source is None:
            raise SourceUnavailable("not there")
        assert tag == "acpmf_crewchief" and size == ctypes.sizeof(PageCrewChief)
        return FakeView(self.source)


def test_the_page_becomes_the_bridge_message():
    p = page([("Yo", 2, 0.5, 180.0), ("Delante", 1, 0.53, 180.0)])
    msg = to_payload(p, 12.0)
    assert msg["me"] == 0 and msg["src"] == "crewchief" and len(msg["cars"]) == 2
    assert msg["cars"][1]["n"] == "Delante" and msg["cars"][1]["p"] == 1
    assert round(msg["cars"][0]["v"]) == 180 and msg["cars"][0]["b"] == 90000


def test_unconnected_slots_are_skipped_and_an_empty_page_is_nothing():
    p = page([("Yo", 1, 0.5, 100.0)])
    p.numVehicles = 3  # two slots the game never filled
    assert len(to_payload(p, 1.0)["cars"]) == 1
    assert to_payload(PageCrewChief(), 1.0) is None


def test_the_feeder_gives_a_board_and_stays_out_of_the_way_of_the_bridge():
    rx = BridgeReceiver(max_age_s=3.0)
    t = [0.0]
    src = page([("Yo", 2, 0.5, 180.0), ("Delante", 1, 0.53, 180.0)])
    feeder = CrewChiefFeeder(rx, backend=FakeBackend(src), clock=lambda: t[0])
    for k in range(12):
        t[0] = k * 0.5
        for i in range(2):  # both cars lap in 100 s
            src.vehicleInfo[i].spLineLength = (0.5 + 0.03 * i + t[0] / 100.0) % 1.0
        assert feeder.tick()
    b = rx.board()
    assert b is not None and b.ahead().car.name == "Delante"
    # the CSP app starts sending: the page is no longer read
    rx.feed({"wear": [90, 90, 90, 90], "t": 10.0, "me": 0, "cars": [], "src": "bridge"})
    before = feeder.fed
    assert feeder.tick() is False and feeder.fed == before


def test_without_the_app_nothing_happens():
    rx = BridgeReceiver()
    feeder = CrewChiefFeeder(rx, backend=FakeBackend(None))
    assert feeder.tick() is False and rx.board() is None
