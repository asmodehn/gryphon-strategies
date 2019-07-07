"""
This is a simple market making strategy to demonstrate use of the Gryphon
framework. It follows the same tick-logic as SuperSimpleMarketMaking, but it's target
exchange, spread, and base volume, are all configurable.
"""

from cdecimal import Decimal

from gryphon.execution.strategies.base import Strategy
from gryphon.lib import market_making as mm
from gryphon.lib.money import Money
from gryphon.lib.exchange.consts import Consts
from gryphon.lib.metrics import midpoint as midpoint_lib


class DynamicMarketMaking(Strategy):
    def __init__(self, db, harness, strategy_configuration):
        super(DynamicMarketMaking, self).__init__(db, harness)

        # Configurable properties with defaults.
        self.spread = Decimal('0.01')
        self.base_volume = Money('0.005', 'BTC')
        self.spread_adjust_coef = Decimal('1')
        self.spread_coef_on_loss = Decimal('2')
        self.base_volume_adjust_coef = Decimal('1')
        self.exchange = None

        self.configure(strategy_configuration)

    def configure(self, strategy_configuration):
        super(DynamicMarketMaking, self).configure(strategy_configuration)

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

        print(current_orders)
        # Question : Can we detect fulfilled orders ?
        # upon fulfilled order we can increase spread base on volatility
        # otherwise we should probably decrease spread to get order fulfilled....

        ob = self.primary_exchange.get_orderbook()
        #print(ob)
        if hasattr(self, 'midpoint'):
            self.last_midpoint = self.midpoint
        else:
            self.midpoint = midpoint_lib.get_midpoint_from_orderbook(ob)
            return

        self.midpoint = midpoint_lib.get_midpoint_from_orderbook(ob)

        # SAFETY
        if (hasattr(self, 'last_ask_price') and self.last_ask_price < self.midpoint) or (hasattr(self, 'last_bid_price') and self.last_bid_price > self.midpoint):
            # spread was not high enough ! We likely lost money here -> correct quickly
            print("HIGH VOLATILITY encountered -> adjusting spread")
            self.spread *= self.spread_coef_on_loss
            #TODO : maybe terminate instead, with advice to change spread ? by some amount ?

        # TODO : calculate local volatility... (or reuse some indicator ??? -> see TA-lib)
        # Then based on volatility adjust spread | base_volume | tick_sleep, within exchange/user acceptable limits...
        # since we cancel and reopen order, we only need local volatility.

        # Note we probably do not want to slow down tick_sleep to not miss trend/volatility changes.
        # -> We should play on spread + base_volume only

        # OPTIMIZATION
        if hasattr(self, 'volat'):
            self.last_volat = self.volat
        else:
            # No volat right now but get it for next tick
            self.volat = abs(self.midpoint - self.last_midpoint)/self.last_midpoint
            return

        self.volat = abs(self.midpoint - self.last_midpoint)/self.last_midpoint

        # for constant tick period, we need increased spread, and reduced base_volume
        self.spread = self.spread_adjust_coef * (self.volat - self.last_volat) + self.spread  # adjusting spread relative to volatility

        print("Volatility: " + str(self.volat))
        print("Spread: " + str(self.spread))

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
        print("balance: " + str(self.primary_exchange.get_balance()))
        print("bid volume: " + str(bid_volume) + " price: " + str(bid_price))
        print("ask volume: " + str(ask_volume) + " price: " + str(ask_price))

        # TODO : take fees into account to remain profitable
        placeable_bid = self.primary_exchange.get_balance().get(bid_price.currency).amount > bid_price.amount * bid_volume.amount
        placeable_ask = self.primary_exchange.get_balance().get(ask_volume.currency).amount > ask_volume.amount

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