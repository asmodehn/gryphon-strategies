from gryphon.execution.strategies.base import Strategy
from gryphon.lib import arbitrage as arb
from gryphon.lib.exchange.consts import Consts


class BTCArb(Strategy):

    def __init__(self, db, harness=None, strategy_configuration=None):
        self.done = False
        super(BTCArb, self).__init__(db, harness, strategy_configuration)

    def tick(self, open_orders):
        btc_crosses = arb.detect_crosses_between_many_orderbooks(
            [self.harness.kraken_btc_eur.get_orderbook(),
             self.harness.bitstamp_btc_usd.get_orderbook(),]
        )

        for cross in btc_crosses:
            print("Cross: " + str(cross))
            executable_volume = arb.get_executable_volume(
                cross,
                cross.buy_exchange.get_balance(),
                cross.sell_exchange.get_balance(),
            )
            print("Executable Volume:" + str(executable_volume))
            if executable_volume:
                cross.buy_exchange.market_order(executable_volume, Consts.BID)
                cross.sell_exchange.market_order(executable_volume, Consts.ASK)

    def is_complete(self):
        return self.done