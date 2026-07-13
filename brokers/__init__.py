# ============================================================
#   brokers/__init__.py — Fábrica de conectores de bróker
#
#   crear_conector(config) devuelve el cliente adecuado según
#   config["broker"]:
#     · "iq"     -> IQ_Option (forex / OTC)         [por defecto]
#     · "deriv"  -> ConectorDeriv (índices sintéticos)
#
#   Ambos exponen la MISMA interfaz que el bot usa (connect,
#   check_connect, change_balance, get_balance, get_candles, buy,
#   check_win_v3, get_all_*), así que el resto del bot no cambia.
# ============================================================


def broker_actual(config):
    return (config.get("broker") or "iq").strip().lower()


def crear_conector(config):
    broker = broker_actual(config)

    if broker == "deriv":
        from .conector_deriv import ConectorDeriv
        return ConectorDeriv(
            token=config.get("deriv_token", ""),
            app_id=config.get("deriv_app_id", "1089"),
            modo=config.get("modo", "PRACTICE"),
            simbolos=config.get("pares_deriv") or None,
        )

    # Por defecto: IQ Option (comportamiento de siempre)
    from iqoptionapi.stable_api import IQ_Option
    return IQ_Option(config.get("usuario", ""), config.get("password", ""))
