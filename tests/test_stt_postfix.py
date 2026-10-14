from pit_wall_ai.stt.postfix import fix


def test_known_mishearings_are_replaced_and_the_rest_is_untouched():
    assert fix("¿Cuál es la presión de los neumáticos sencaí?") == (
        "¿Cuál es la presión de los neumáticos psi?"
    )
    assert fix("dime la P S I del delantero") == "dime la psi del delantero"
    assert fix("espesor pesimista") == "espesor pesimista"  # inside other words: nothing
    assert fix("¿Cuánta gasolina me queda?") == "¿Cuánta gasolina me queda?"
