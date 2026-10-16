from pit_wall_ai.analysis.wear_estimate import WearEstimator, wear_text

WHEELS = ("delantero izquierdo", "delantero derecho", "trasero izquierdo", "trasero derecho")


def trained():
    est = WearEstimator()
    est.add("soft", 10, (95, 94, 85, 83))  # 0.5, 0.6, 1.5, 1.7 points per lap
    est.add("soft", 20, (90, 88, 70, 66))  # 0.5, 0.6, 1.5, 1.7 again
    return est


def test_the_rate_is_the_loss_per_lap_of_the_stints_read():
    r = trained().rate("soft")
    assert [round(x, 2) for x in r] == [0.5, 0.6, 1.5, 1.7]
    assert trained().rate("hard") is None


def test_a_new_set_is_estimated_from_the_rate_with_a_range():
    e = trained().estimate("soft", 8)
    assert e.source == "estimate" and round(e.life[3]) == round(100 - 1.7 * 8)
    assert e.low[3] < e.life[3] < e.high[3]


def test_after_a_reading_only_the_laps_since_count():
    est = trained()
    e = est.estimate("soft", 14, last_reading=(92, 91, 78, 75), laps_at_reading=12)
    assert e.laps_since_reading == 2 and round(e.life[3], 1) == round(75 - 1.7 * 2, 1)
    at_the_stop = est.estimate("soft", 12, last_reading=(92, 91, 78, 75), laps_at_reading=12)
    assert at_the_stop.source == "pit" and at_the_stop.life == (92, 91, 78, 75)


def test_without_data_nothing_is_invented():
    assert WearEstimator().estimate("soft", 5) is None


def test_the_range_widens_with_the_laps_since_the_reading():
    est = trained()
    near = est.estimate("soft", 13, last_reading=(92, 91, 78, 75), laps_at_reading=12)
    far = est.estimate("soft", 22, last_reading=(92, 91, 78, 75), laps_at_reading=12)
    assert (far.high[3] - far.low[3]) > (near.high[3] - near.low[3])


def test_the_words_say_it_is_an_estimate_and_storage_roundtrips():
    est = WearEstimator.from_dict(trained().to_dict())
    e = est.estimate("soft", 14, last_reading=(92, 91, 78, 75), laps_at_reading=12)
    t = wear_text("es", e, WHEELS)
    assert "estimación" in t and "trasero derecho" in t
    stop = est.estimate("soft", 12, last_reading=(92, 91, 78, 75), laps_at_reading=12)
    assert "última parada" in wear_text("es", stop, WHEELS)
