"""
This is a simple market making strategy to demonstrate use of the Gryphon
framework. It follows the same tick-logic as SuperSimpleMarketMaking, but it's target
exchange, spread, and base volume, are all configurable.
"""
import datetime

from cdecimal import Decimal

from gryphon.execution.strategies.base import Strategy
from gryphon.lib import market_making as mm
from gryphon.lib.money import Money
from gryphon.lib.exchange.consts import Consts
from gryphon.lib.metrics import midpoint as midpoint_lib

import logging
import logging.handlers

from datetime import datetime



class TS(object):

    def __init__(self):
        self.series = []
        self.derivative = []

    def __add__(self, other):
        assert other is not None
        timestamp = datetime.now()

        if self.series:
            last_amount = self.series[-1][1]
            assert last_amount is not None
            self.derivative.append((timestamp, 2*(other - last_amount)/(other + last_amount)))
        self.series.append((timestamp, other))
        return self

    def deriv(self, last=1):
        """
        calculate derivative value for the n last values
        :param percentile:
        :return:
        """
        if self.derivative:
            return sum(t[1] for t in self.derivative[-last:]) / len(self.derivative[-last:])
        else:
            return None

    def last(self):
        return self.series[-1]


#TODO : test + pandas ...
class LocalMoneyTS(TS):

    def __init__(self, cur="EUR"):
        self.currency = cur
        super(LocalMoneyTS, self).__init__()

    def __add__(self, other):
        assert isinstance(other, Money) and other.currency == self.currency

        return super(LocalMoneyTS, self).__add__(other)

    def volatility(self, last=1):
        """
        calculate volatility distribution and returns volatility averaged over the derivative values
        :param percentile:
        :return:
        """
        return super(LocalMoneyTS, self).deriv(last)



class DynamicMarketMaking(Strategy):

    @property
    def actor(self):
        """
        The strategy's 'actor' is how orders and trades are associated with the
        strategy in the database. As a consequence, if two strategies have the same
        actor, they have the same trade history and position.
        """
        return self.__class__.__name__.upper()
        # TODO : change this to get exchange name in there...

    def __init__(self, db, harness, strategy_configuration):
        super(DynamicMarketMaking, self).__init__(db, harness)

        self.logger = logging.getLogger(__name__)

        handler = logging.handlers.RotatingFileHandler('dynamic_market_making.' + datetime.now().strftime("%Y%m%d-%H%M%S") + '.log')
        formatter = logging.Formatter(
            '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

        self.logger.debug("--Strategy Init--")

        self.volat = TS()
        self.midpoints = LocalMoneyTS(cur='EUR')

        # Configurable properties with defaults.
        self.spread = Decimal('0.01')  # how much spread should we start with around orderbook midpoint for ask/bid
        self.base_volume = Money('0.005', 'BTC')  # volume to stake with (constant)

        self.exchange = None

        self.spread_adjust_coef = Decimal('1')
        self.spread_coef_on_loss = Decimal('2')
        self.base_volume_adjust_coef = Decimal('1')

        self.configure(strategy_configuration)

    def configure(self, strategy_configuration):
        super(DynamicMarketMaking, self).configure(strategy_configuration)

        self.logger.debug("--Strategy Configure--")
        self.init_configurable('spread', strategy_configuration)
        self.init_configurable('base_volume', strategy_configuration)

        self.init_configurable('exchange', strategy_configuration)

        self.init_configurable('spread_adjust_coef', strategy_configuration)
        self.init_configurable('spread_coef_on_loss', strategy_configuration)
        self.init_configurable('base_volume_adjust_coef', strategy_configuration)

        assert self.spread_coef_on_loss > self.spread_adjust_coef

        self.init_primary_exchange()

    def init_primary_exchange(self):
        self.primary_exchange = self.harness.exchange_from_key(self.exchange)

        # This causes us to always audit our primary exchange.
        self.target_exchanges = [self.primary_exchange.name]

    def tick(self, current_orders):

        self.logger.debug("--Strategy Tick--")
        self.logger.info("Current Orders: " + str(current_orders))
        # Question : Can we detect fulfilled orders ?
        # upon fulfilled order we can increase spread base on volatility
        # otherwise we should probably decrease spread to get order fulfilled....

        ob = self.primary_exchange.get_orderbook()

        self.midpoints += midpoint_lib.get_midpoint_from_orderbook(ob)

        # SAFETY
        # if (hasattr(self, 'last_ask_price') and self.last_ask_price < self.midpoint) or (hasattr(self, 'last_bid_price') and self.last_bid_price > self.midpoint):
        #     # spread was not high enough ! We likely lost money here -> correct quickly
        #     self.logger.warning("HIGH VOLATILITY encountered -> adjusting spread")
        #     self.spread *= self.spread_coef_on_loss
        #     #TODO : maybe terminate instead, with advice to change spread ? by some amount ?

        # TODO : calculate local volatility... (or reuse some indicator ??? -> see TA-lib)
        # Then based on volatility adjust spread | base_volume | tick_sleep, within exchange/user acceptable limits...
        # since we cancel and reopen order, we only need local volatility.

        # Note we probably do not want to slow down tick_sleep to not miss trend/volatility changes.
        # -> We should play on spread + base_volume only

        # OPTIMIZATION
        if self.midpoints.volatility(last=1):
            self.volat += self.midpoints.volatility(last=1)  # TODO : since last successful order...

            if self.volat.last()[1] < 0:
                self.logger.info("Price went down. skipping this tick...")
                # TODO : manage bear markets with shorts and leverage...

            elif self.volat.last()[1] > 0:  # we only want bull micro market for now
                # We bet on the volatility to come to be the same as the one past,
                print("Volatility derivative :" + str(self.volat.deriv()))
                # TODO : define what value derivative of volatility should be

                #  and set the spread based on that.
                self.spread = self.volat.last()[1] / 2
                # TODO : reduce spread if expectation failed (order not passed), to maximize likelyhood to pass order...
                # TODO: increase spread if successful order passed, trying to maximize profit on volatile markets


                # for constant tick period, we need increased spread, and reduced base_volume
                #self.spread = self.spread_adjust_coef * (self.volat - self.last_volat) + self.spread  # adjusting spread relative to volatility

                self.logger.info("Volatility: " + str(self.volat))
                self.logger.info("Spread: " + str(self.spread))

                # TODO : take fees into account to remain profitable
                exchange_fees = self.primary_exchange.exchange_wrapper.fee
                if self.spread < exchange_fees * self.base_volume:

                    self.logger.info("Fees would be too high : " + str(exchange_fees * self.base_volume) + " vs spread of " + str(self.spread))

                else:
                    self.logger.info(
                        "Spread : " + str(self.spread) + " is larger than expected fees : " + str(exchange_fees * self.base_volume))

                    # Base volume increase means increased risk (maybe more than spread decrease).
                    # We want to increase base volume when volatility doesnt change 'much'...
                    # TODO : self.relative_volat_change = abs(self.volat - self.last_volat) / self.last_volat
                    # relative change of base_volume
                    # TODO : self.base_volume = self.base_volume_adjust_coef * (1-self.relative_volat_change) * self.base_volume + self.base_volume
                    # TODO : have a minimum volume to exchange + slowly limit to that when volatility detected.
                    # TODO : increase volume on non volatility/'less volatile than expected'... + profit ? how to measure that here ?

                    # TODO:  use profit measurement to adjust the adjust_coefs (machine learning stuff ?)...

                    # #Note : the speed of adjustment is critical to be in front of order fullfilment.
                    # -> Check control theory for a converging / asymptotic optimisation of control (yet easily reversible if needed...)

                    bid_price, ask_price = mm.midpoint_centered_fixed_spread(ob, self.spread)
                    # TODO : improve by doing everything based on ohlcv since last check...

                    bid_volume, ask_volume = mm.simple_position_responsive_sizing(
                        self.base_volume,
                        self.position,
                    )
                    self.logger.info("balance: " + str(self.primary_exchange.get_balance()))
                    self.logger.info("bid volume: " + str(bid_volume) + " price: " + str(bid_price))
                    self.logger.info("ask volume: " + str(ask_volume) + " price: " + str(ask_price))

                    placeable_bid = self.primary_exchange.get_balance().get(bid_price.currency).amount >= bid_price.amount * bid_volume.amount
                    placeable_ask = self.primary_exchange.get_balance().get(ask_volume.currency).amount >= ask_volume.amount

                    # TODO : maybe we do not need to cancel everything everytime ?
                    self.primary_exchange.cancel_all_open_orders()

                    # Place order only if we can...
                    if placeable_bid:
                        self.primary_exchange.limit_order(Consts.BID, bid_volume, bid_price)

                    # Place order only if we can...
                    if placeable_ask:
                        self.primary_exchange.limit_order(Consts.ASK, ask_volume, ask_price)

                    self.last_bid_price = bid_price
                    self.last_ask_price = ask_price