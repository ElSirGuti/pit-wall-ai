from pit_wall_ai.chat.smalltalk import reply


def test_thanks_greetings_and_radio_checks_get_a_local_answer():
    for q in ("Gracias", "Muy buen trabajo, muchas gracias por la ayuda.", "Hola", "¿Me copias?"):
        assert reply(q, "es"), q
    assert reply("thank you", "en") and reply("danke", "de")


def test_anything_that_also_asks_for_something_is_left_alone():
    assert reply("gracias, cuánta gasolina me queda", "es") is None
    assert reply("hola, cómo van los neumáticos", "es") is None
    assert reply("cuánta gasolina", "es") is None
