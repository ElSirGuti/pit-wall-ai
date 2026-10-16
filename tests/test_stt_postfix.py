from pit_wall_ai.stt.postfix import fix


def test_known_mishearings_are_replaced_and_the_rest_is_untouched():
    assert fix("¿Cuál es la presión de los neumáticos sencaí?") == (
        "¿Cuál es la presión de los neumáticos psi?"
    )
    assert fix("dime la P S I del delantero") == "dime la psi del delantero"
    assert fix("espesor pesimista") == "espesor pesimista"  # inside other words: nothing
    assert fix("¿Cuánta gasolina me queda?") == "¿Cuánta gasolina me queda?"


def test_sai_is_heard_for_psi():
    assert fix("cuántos sai tengo en el delantero") == "cuántos psi tengo en el delantero"


def test_pizza_is_heard_for_pits():
    assert fix("debería entrar a pizza ahora") == "debería entrar a pits ahora"


def test_circuit_names_misheard():
    assert fix("las curvas de mayones") == "las curvas de magione"


def test_desgaste_misheard():
    assert fix("dejaste de los neumáticos") == "desgaste de los neumáticos"
    assert fix("el circuito es maguione") == "el circuito es magione"
