"""
This is a simple investing strategy to demonstrate use of the Gryphon
framework.
"""
import datetime
import random
import uuid
from collections import OrderedDict

from cdecimal import Decimal

from gryphon.execution.strategies.base import Strategy, positions
from gryphon.lib import market_making as mm
from gryphon.lib.money import Money
from gryphon.lib.exchange.consts import Consts
from gryphon.lib.metrics import midpoint as midpoint_lib

import logging
import logging.handlers

from datetime import datetime, timedelta


class MonoExchangeStrategy(Strategy):
    def __init__(self, db, harness, strategy_configuration):

        self.exchange = None
        self.primary_exchange = None

        self.volume_currency = 'BTC'

        # This calls configure...
        super(MonoExchangeStrategy, self).__init__(db, harness, strategy_configuration)

    def configure(self, strategy_configuration):
        super(MonoExchangeStrategy, self).configure(strategy_configuration)

        self.init_configurable('exchange', strategy_configuration)
        self.init_primary_exchange()

        self.init_configurable('volume_currency', strategy_configuration)

    def init_primary_exchange(self):
        self.primary_exchange = self.harness.exchange_from_key(self.exchange)

        # This causes us to always audit our primary exchange.
        self.target_exchanges = [self.primary_exchange.name]

    @property
    def actor(self):
        """
        The strategy's 'actor' is how orders and trades are associated with the
        strategy in the database. As a consequence, if two strategies have the same
        actor, they have the same trade history and position.
        """
        return self.__class__.__name__.upper() + "_" + self.primary_exchange.exchange_account.name
        # TODO : change this to get exchange name in there...


    # def pre_tick(self):
    #     super(MonoExchangeStrategy, self).pre_tick()
    #
    #     self.harness.log()

    # @property
    # def position(self):
    #     if self._position is not None:
    #         return self._position
    #
    #     # Currently we just default to assuming the class's actor is it's classname,
    #     # but we'll improve this shortly.
    #
    #     self._position = positions.fast_position(
    #         self.db,
    #         volume_currency=self.volume_currency,
    #         actor=self.actor,
    #     )
    #
    #     return self._position

    def retrieve_orders(self, current_orders):

        exchange_current_orders, exchange_eaten_order_ids = self.primary_exchange.consolidate_ledger()

        return



#
# class OrderStrategy(object):
#     """ a Clever way to pass orders.
#     We want to minimize unfilled order, and delay before filling
#     """
#
#     def __init__(self):
#
#     def tick(self, current_orders, eaten_order_ids):



class Order(object):
    def __init__(self, mode, id, volume):
        self.mode = mode  # Consts.ASK or Const.BID
        self.id = id
        self.volume = volume
        self.volume_filled = Money('0', currency=volume.currency)

    def fill(self, volume):
        """
        filling an order, partially or totally
        :param volume:
        :return: volume remaining after filling this order.
        """
        self.volume_filled += min(self.volume-self.volume_filled, volume)
        return volume - min(self.volume-self.volume_filled, volume)

    @property
    def filled(self):
        return self.volume == self.volume_filled

    def __str__(self):
        return str(self.id) + ": " + str(self.mode) + " " + str(self.volume)


class MarketOrder(Order):
    def __init__(self, mode, id, price, volume):
        self.price = price  # only because market order require it in gryphon (https://github.com/garethdmm/gryphon/issues/52)
        super(MarketOrder, self).__init__(mode, id, volume)

    def gryphon(self):
        return self  # TODO : gryphon format

    def __str__(self):
        return super(MarketOrder, self).__str__() + " @ " + str(self.price)


class LimitOrder(Order):
    def __init__(self, mode, id, price, volume):
        self.price = price
        super(LimitOrder, self).__init__(mode, id, volume)

    def gryphon(self):
        return self  # TODO : gryphon format

    def __str__(self):
        return super(LimitOrder, self).__str__() + " @ " + str(self.price)


class Desk(object):

    def __init__(self, harness, exchange, logger):
        self.harness = harness
        self.exchange = exchange
        self.logger = logger

        self.orders_passed = OrderedDict()

        self.min_order_size = self.exchange.exchange_wrapper.min_order_size

    def _place_order(self, callable_order_type, mode, volume, price):
        if volume > self.min_order_size:
            order = callable_order_type(mode, volume, price)
            if order:
                if callable_order_type == self.exchange.limit_order:
                    msg = "Limit Order"
                elif callable_order_type == self.exchange.market_order:
                    msg = "Market Order"
                else:
                    msg = "Unknown Order"

                if not order.get('success'):
                    self.logger.error(msg + " cannot be placed !")
                    return None
            else:
                assert not self.harness.execute
                pass  # dry-run

            # assign id, even if harness doesnt give us anything
            oid = order.get('order_id') if order else str(uuid.uuid4())[:8]

            # Store order for this run even if we do not execute, to be abel to test strategy over multiple ticks
            if callable_order_type == self.exchange.limit_order:
                order = LimitOrder(mode=mode, id=oid, price=price, volume=volume)
            elif callable_order_type == self.exchange.market_order:
                order = MarketOrder(mode=mode, id=oid, price=price, volume=volume)

            self.orders_passed[order.id] = order
            return order

        else:
            self.logger.warning("Volume " + str(volume) + " too low, CANNOT buy !")

    def limit_bid(self, bid_volume, bid_price):
        order = self._place_order(self.exchange.limit_order, Consts.BID, bid_volume, bid_price)
        return order

    def market_bid(self, bid_volume, bid_price):
        order = self._place_order(self.exchange.market_order, Consts.BID, bid_volume, bid_price)
        return order

    def limit_ask(self, ask_volume, ask_price):
        order = self._place_order(self.exchange.limit_order, Consts.ASK, ask_volume, ask_price)
        return order

    def market_ask(self, ask_volume, ask_price):
        order = self._place_order(self.exchange.market_order, Consts.ASK, ask_volume, ask_price)
        return order

    def cancel(self, order_id):
        # TODO : find order in passed list
        self.exchange.cancel_order(order_id=order_id)
        raise NotImplementedError

    @property
    def ephemeral_position(self):
        # compute position here
        calculated_position = {}
        for oid, o in self.orders_passed.items():
            if o.mode == Consts.BID:
                calculated_position.setdefault(o.price.currency, Money('0', currency=o.price.currency))
                calculated_position[o.price.currency] -= o.volume.amount * o.price

                if o.filled:
                    calculated_position.setdefault(o.volume_filled.currency, Money('0', currency=o.volume_filled.currency))
                    calculated_position[o.volume_filled.currency] += o.volume_filled

            elif o.mode == Consts.ASK:
                calculated_position.setdefault(o.price.currency, Money('0', currency=o.price.currency))
                calculated_position[o.volume.currency] -= o.volume

                if o.filled:
                    calculated_position.setdefault(o.volume_filled.currency, Money('0', currency=o.volume_filled.currency))
                    calculated_position[o.price.currency] += o.volume_filled.amount * o.price

            else:
                self.logger.error("unknown order mode")
        return calculated_position

    # useful question for simple order strategies
    def last_filled_order_is(self, mode= None):

        if not self.orders_passed:
            return False

        for oid, o in reversed(self.orders_passed.items()):
            if o.filled:
                return mode == o.mode

    # useful question for simple order strategies
    def last_unfilled_order_is(self, mode=None):
        if not self.orders_passed:
            return False

        for oid, o in reversed(self.orders_passed.items()):
            if not o.filled:
                return mode == o.mode

    def tick(self, current_orders, eaten_orders):
        # to keep track of order filling

        if not self.harness.execute:
            for oid, o in self.orders_passed.items():
                if not o.filled:
                    # random fill
                    if random.random() > 0.5:
                        self.logger.warning("SIMULATING ORDER FILL: " + str(o))
                        # TODO :  better filling mock logic... limit order might NEVER get filled.
                        o.fill(o.volume)  # simulating complete fill only for now
                        eaten_orders[oid] = o.gryphon()
                    else:
                        current_orders[oid] = o.gryphon()
                # Note : eaten order appear only on one loop, when they disappeared, not the following one AFAIK
        else:
            # actual filling, other way around...
            for i in eaten_orders:
                self.orders_passed[i].fill()

        # TODO : what happens on partial fills ?
        return current_orders, eaten_orders


class PositionTracker(object):

    def __init__(self, desk, exchange, logger, stake_currency, quote_currency ,targetted_profit_pct, acceptable_loss_pct):
        self.desk = desk
        self.logger = logger
        self.exchange = exchange
        self.stake_currency = stake_currency
        self.quote_currency = quote_currency
        self.targetted_profit_pct = targetted_profit_pct
        self.acceptable_loss_pct = acceptable_loss_pct

        self.min_order_size = self.exchange.exchange_wrapper.min_order_size

    def limit_enter(self, bid_volume, bid_price):
        return self.desk.limit_bid(bid_volume=bid_volume, bid_price=bid_price)

    def market_enter(self, bid_volume, bid_price):  # TODO : review point of price here ? isnt it midpoitn ?
        return self.desk.market_bid(bid_volume=bid_volume, bid_price=bid_price)

    @property
    def position(self):
        # Managing potential differences between positions...
        exchange_position = self.exchange.exchange_account.position
        ephemeral_position = self.desk.ephemeral_position

        #self.logger.info("Ephemeral position: " + str(ephemeral_position))
        #if ephemeral_position != exchange_position:
        #    self.logger.warning("Exchange position: " + str(exchange_position))

        return ephemeral_position

    def limit_exit(self, ask_price):
        total_volume = self.position.get(self.stake_currency)
        if total_volume:
            return self.desk.limit_ask(ask_volume= total_volume, ask_price=ask_price)
        else:
            self.logger.warning("Attempted Exit without position...")

    def market_exit(self, ask_price):  # TODO : review point of price here ? isnt it midpoitn ?
        total_volume = self.position.get(self.stake_currency)
        if total_volume:
            return self.desk.market_ask(ask_volume=total_volume, ask_price=ask_price)
        else:
            self.logger.warning("Attempted Exit without position...")

    def exit_loss_price(self, quote_currency, volume_currency):
        # extract information from current prices
        pos = self.position
        if quote_currency in pos and volume_currency in pos:
            price = -1 * self.position.get(quote_currency) / self.position.get(volume_currency).amount

            # TODO : FEES !

            return price * (1-self.acceptable_loss_pct)
        else:
            return None

    def exit_profit_price(self, quote_currency, volume_currency):
        # extract information from current prices
        pos = self.position
        if quote_currency in pos and volume_currency in pos:
            price = -1* self.position.get(quote_currency) / self.position.get(volume_currency).amount

            # TODO : FEES !

            return price * (1+self.targetted_profit_pct)
        else:
            return None

    # SOME TRADE LOGIC implied here...
    @property
    def exiting(self):
        return self.desk.last_unfilled_order_is(mode=Consts.ASK)

    @property
    def entering(self):
        return self.desk.last_unfilled_order_is(mode=Consts.BID)

    @property
    def entered(self):
        return self.desk.last_filled_order_is(mode=Consts.BID)

    @property
    def exited(self):
        return self.desk.last_filled_order_is(mode=Consts.ASK)
# class Trade(Position):
#
#     def __init__(self, exchange, logger, targetted_profit_pct, acceptable_loss_pct, timeout = timedelta(minutes=1)):
#
#         super(Trade, self).__init__(exchange=exchange, targetted_profit_pct=targetted_profit_pct, acceptable_loss_pct=acceptable_loss_pct, timeout=timeout)
#
#         self.entered_price = None
#         self.exited_price = None
#
#         self.enter_order_id = None
#         self.exit_order_id = None
#
#     def limit_enter(self, bid_volume, bid_price):
#
#         if bid_volume > self.min_order_size:
#             enter_order = self.exchange.limit_order(Consts.BID, bid_volume, bid_price)
#             if enter_order:
#                 self.enter_order_id = enter_order.get('order_id')
#                 if not enter_order.get('success'):
#                     self.logger.error("Limit Order cannot be placed !")
#                     return None
#                 else:
#                     pass  # dry-run
#
#             self.order_volume = bid_volume
#             self.entered_price = bid_price
#             return self.enter_order_id
#         else:
#             self.logger.warning("Volume too low, CANNOT buy !")
#
#     def market_enter(self, bid_volume, bid_price):  # TODO : review point of price here ? isnt it midpoitn ?
#
#         if bid_volume > self.min_order_size:
#             enter_order = self.exchange.market_order(Consts.BID, bid_volume, bid_price)
#             if enter_order:
#                 self.enter_order_id = enter_order.get('order_id')
#                 if not enter_order.get('success'):
#                     self.logger.error("Market Order cannot be placed !")
#                     return None
#                 else:
#                     pass  # dry-run
#
#             self.order_volume = bid_volume
#             self.entered_price = bid_price
#             return self.enter_order_id
#         else:
#             self.logger.warning("Volume too low, CANNOT buy !")
#
#
#     @property
#     def exit_loss_price(self):
#         return self.entered_price * (1-self.acceptable_loss_pct)
#
#     @property
#     def exit_profit_price(self):
#         return self.entered_price * (1+self.targetted_profit_pct)
#
#     def setup(self, bid_volume, bid_price):
#         # enter and setup exit at once when possible !
#         self.market_enter(bid_volume=bid_volume, bid_price=bid_price)
#         if self.balance.get(self.order_volume.currency) > self.order_volume:
#             # TODO : use some kind of continuation for retry ?
#             self.limit_exit(ask_price=self.exit_profit_price)
#
#     def tick(self, balance, midpoint, open_orders):
#         self.logger.info(str(self))
#
#         self.logger.info("Current Orders: " + str(open_orders))
#
#         # TODO : update our own order tracking...
#         enter_order_found = False
#         exit_order_found = False
#         for o in open_orders.get(self.exchange, []):
#             if self.exit_order_id and o.get('id') == self.exit_order_id:
#                 exit_order_found = True
#             if self.enter_order_id and o.get('id') == self.enter_order_id:
#                 enter_order_found = True
#
#         if self.enter_order_id and not enter_order_found:
#             self.logger.info("ENTER buy order has been consumed or cancelled.")
#             self.enter_order_id = None
#
#         if self.exit_order_id and not exit_order_found:
#             self.logger.info("EXIT order has been consumed or cancelled. ")  # TODO : profit report...
#             self.exit_order_id = None
#
#         # time to place exit order if still needed
#         if not self.exit_order_id and balance.get(self.order_volume.currency) > self.order_volume:
#             # we have the funds !
#             self.limit_exit(self.exit_profit_price)
#
#         elif self.timeout < datetime.now():
#             # TODO : maybe should put stop_loss early on as well ?
#             self.logger.info("Position timeout passed, giving up...")
#
#             if self.exit_order_id:  # timeout expired, sell quickly, even at a loss
#                 self.exchange.cancel_order(self.exit_order_id)
#
#             self.market_exit(midpoint)
#
#     def __str__(self):
#         return "Volume: " + str(self.order_volume) + " IN: " + str(self.entered_price) + " OUT+: " + str(self.exit_loss_price) + " OUT-: " + str(self.exit_loss_price) + "Timeout: " + str(self.timeout - datetime.now())


from basic_ts import TS, LocalMoneyTS

class MarketObserver(object):
    """ A very basic market observer, when we dont have ohlcv and we have to do everything ourselves..."""
    def __init__(self, logger, bullcount_periods, bullcount_trend, bearcount_periods, bearcount_trend):

        self.logger= logger
        self.bullcount_periods = bullcount_periods
        self.bullcount_trend = bullcount_trend
        self.bearcount_periods = bearcount_periods
        self.bearcount_trend = bearcount_trend
        self.bearcount = 0
        self.bullcount = 0

        self.midpoint_derivative = TS()
        self.midpoints = LocalMoneyTS(cur='EUR')

    def tick(self, midpoint, on_bull_trend=None, on_bear_trend=None):
        """
        Process a tick, and return an order
        :param midpoint:
        :param pass_order:
        :return:
        """
        self.midpoints += midpoint
        md = self.midpoints.deriv(last=1)
        if md:
            self.midpoint_derivative += md

            bearcount = 0
            bullcount = 0
            for md in reversed(self.midpoint_derivative):
                bearcount += (1 if md[1] < 0 else 0)
                bullcount += (1 if md[1] > 0 else 0)
            if len(self.midpoint_derivative) > max(self.bearcount_periods, self.bullcount_periods):
                drop_by = len(self.midpoint_derivative) - int(max(self.bearcount_periods, self.bullcount_periods))
                self.midpoint_derivative.dropfirst(drop_by)
                self.midpoints.dropfirst(drop_by)
                assert len(self.midpoints) == len(self.midpoint_derivative) + 1  # just to be sure...

            # We check bull market first as it is easier to deal with this kind of strategy.
            if bullcount >= self.bullcount_trend:

                self.logger.info("BULL market trend detected ! " + str(self.midpoint_derivative))
                if on_bull_trend and callable(on_bull_trend):
                    on_bull_trend()
            elif bearcount >= self.bearcount_trend:

                self.logger.info("BEAR market trend detected ! " + str(self.midpoint_derivative))

                if on_bear_trend and callable(on_bear_trend):
                    on_bear_trend()
            else:
                self.logger.info(
                    "UNDECIDED market... BULL " + str(bullcount) + "/" + str(self.bullcount_trend) + " BEAR " + str(bearcount) + "/" + str(self.bearcount_trend))


class InvestSingle(MonoExchangeStrategy):

    def __init__(self, db, harness, strategy_configuration):

        self.logger = logging.getLogger(__name__)

        handler = logging.handlers.RotatingFileHandler('market_hodl.' + datetime.now().strftime("%Y%m%d-%H%M%S") + '.log')
        formatter = logging.Formatter(
            '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

        self.logger.debug("-- Mono Exchange Investing Strategy Init--")

        #default values
        self.volume_currency = 'BTC'
        self.quote_currency = 'EUR'

        self.bullcount_periods = 5
        self.bullcount_trend = 4
        self.bearcount_periods =5
        self.bearcount_trend = 4

        self.hodl_until_profit_base = 0.005
        self.hodl_until_loss_base = 0.002
        #self.hodl_timeout_base = 60

        self.base_volume = Money('0.005', currency='BTC')
        self.market_observer = None  # not yet... wait for configuration

        # WARNING : this calls configure...
        super(InvestSingle, self).__init__(db, harness, strategy_configuration)

        self.desk = Desk(harness= self.harness, exchange=self.primary_exchange, logger=self.logger)
        self.position_tracker = PositionTracker(desk = self.desk, exchange=self.primary_exchange, logger=self.logger,
                                                quote_currency=self.quote_currency,
                                                stake_currency=self.volume_currency,
                                                targetted_profit_pct=self.hodl_until_profit_base, acceptable_loss_pct=self.hodl_until_loss_base)

    def configure(self, strategy_configuration):
        super(InvestSingle, self).configure(strategy_configuration)

        self.init_configurable('volume_currency', strategy_configuration)
        self.init_configurable('quote_currency', strategy_configuration)

        self.init_configurable('bullcount_periods', strategy_configuration)
        self.init_configurable('bullcount_trend', strategy_configuration)
        self.init_configurable('bearcount_periods', strategy_configuration)
        self.init_configurable('bearcount_trend', strategy_configuration)

        self.init_configurable('hodl_until_profit_base', strategy_configuration)
        self.init_configurable('hodl_until_loss_base', strategy_configuration)

        #self.init_configurable('hodl_timeout_base', strategy_configuration)

        self.market_observer = MarketObserver(self.logger,
                                              bullcount_periods=self.bullcount_periods,
                                              bullcount_trend=self.bullcount_trend,
                                              bearcount_periods=self.bearcount_periods,
                                              bearcount_trend=self.bearcount_trend)

        self.init_configurable('base_volume', strategy_configuration)

    def tick(self, current_orders, eaten_order_ids):

        self.logger.debug("--Strategy Tick--")

        # Desk can be our runtime mock, when operating without exchange...
        current_orders, eaten_order_ids = self.desk.tick(current_orders=current_orders, eaten_orders=eaten_order_ids)

        # NOTE : we currently minimize the number of simultaneous trades, to avoid unintended tricky behavior...

        balance = self.primary_exchange.get_balance()
        ob = self.primary_exchange.get_orderbook()

        midpoint = midpoint_lib.get_midpoint_from_orderbook(ob)
        #self.midpoints += midpoint

        self.logger.info("Current midpoint of orderbook : " + str(midpoint))

        self.logger.info("Ephemeral position: " + str(self.position_tracker.position))
        if self.position_tracker.position != self.primary_exchange.exchange_account.position:
            self.logger.warning("Exchange position: " + str(self.primary_exchange.exchange_account.position))

        # Preparing to enter BET...
        def on_bear_trend():
            # Ultimately make decision
            #self.logger.info("Attempting to enter a new position...")
            #self.position_tracker.market_enter(bid_price=midpoint, bid_volume=self.base_volume)
            pass
            # TODO : reverse trend ? or only useful for mixed currency bag as quote ??

        def on_bull_trend():
            if not self.position_tracker.position or (self.position_tracker.exited and not self.position_tracker.entering):
                # Ultimately make decision
                self.logger.info("Attempting to enter at market price...")
                self.position_tracker.market_enter(bid_price=midpoint, bid_volume=self.base_volume)

        self.market_observer.tick(midpoint,
                                  on_bull_trend=on_bull_trend,
                                  on_bear_trend=on_bear_trend)


        # If we currently track a position (from this run)
        if self.position_tracker.position and self.position_tracker.entered and not self.position_tracker.exiting:
            # we can attempt an exit
            exit_profit_price = self.position_tracker.exit_profit_price(quote_currency=self.quote_currency, volume_currency=self.volume_currency)
            self.logger.info("Targetted profit price: " + str(exit_profit_price))
            if exit_profit_price:
                # TODO
                # if timeout:
                # else:
                if midpoint > exit_profit_price:
                    #if midpoint is already above, exit at market !
                    self.logger.info("Attempting to exit at targetted profit price...")
                    self.position_tracker.market_exit(midpoint)
                else:
                    # else exit limit
                    self.logger.info("Attempting to exit at market price...")
                    self.position_tracker.limit_exit(exit_profit_price)
            else:
                self.logger.error("Cannot calculate exit profit price")



