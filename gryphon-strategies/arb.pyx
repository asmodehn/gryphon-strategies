from gryphon.execution.strategies.base import Strategy
from gryphon.lib import arbitrage as arb
from gryphon.lib.exchange.consts import Consts


class Arb(Strategy):

    def __init__(self, db, harness=None, strategy_configuration=None):
        self.done = False
        super(Arb, self).__init__(db, harness, strategy_configuration)

    def tick(self, open_orders):
        cross = arb.detect_cross(
            self.harness.kraken_btc_eur.get_orderbook(),
            self.harness.bitstamp_btc_eur.get_orderbook(),
        )

        if cross:
            executable_volume = arb.get_executable_volume(
                cross,
                cross.buy_exchange.get_balance(),
                cross.sell_exchange.get_balance(),
            )
            if executable_volume:
                cross.buy_exchange.market_order(executable_volume, Consts.BID)
                cross.sell_exchange.market_order(executable_volume, Consts.ASK)
            else:
                print("Executable Volume:" + str(executable_volume))
                self.done = True
        else:
            print("No Cross detected.")

    def is_complete(self):
        return self.done