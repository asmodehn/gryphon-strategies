from gryphon.execution.strategies.base import Strategy
from gryphon.lib import arbitrage as arb
from gryphon.lib.exchange.consts import Consts

import logging.handlers


class BTCArb(Strategy):

    def __init__(self, db, harness=None, strategy_configuration=None):
        self.done = False
        super(BTCArb, self).__init__(db, harness, strategy_configuration)

        self.logger = logging.getLogger(__name__)

        handler = logging.handlers.RotatingFileHandler('BTC_arb.log')
        formatter = logging.Formatter(
            '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

        self.logger.debug("--Strategy Init--")

    def tick(self, open_orders):

        self.logger.debug("--Strategy Tick--")
        self.logger.info("Current Orders: " + str(open_orders))

        btc_crosses = arb.detect_cross(
            self.harness.bitstamp_btc_usd.get_orderbook(),
             self.harness.bitstamp_btc_eur.get_orderbook()
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