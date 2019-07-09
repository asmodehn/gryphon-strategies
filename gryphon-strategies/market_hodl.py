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

from datetime import datetime, timedelta


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

    def dropfirst(self, number):  # number should be int
        self.series = self.series[number:]
        self.derivative = self.derivative[number:]

    def __reversed__(self):
        return reversed(self.series)

    def __len__(self):
        return len(self.series)


#TODO : test + pandas ...
class LocalMoneyTS(TS):

    def __init__(self, cur="EUR"):
        self.currency = cur
        super(LocalMoneyTS, self).__init__()

    def __add__(self, other):
        assert isinstance(other, Money) and other.currency == self.currency

        return super(LocalMoneyTS, self).__add__(other)



class Position(object):

    def __init__(self, exchange, logger, targetted_profit_pct, acceptable_loss_pct, timeout = timedelta(minutes=1)):
        self.logger = logger
        self.exchange = exchange
        self.targetted_profit_pct = targetted_profit_pct
        self.acceptable_loss_pct = acceptable_loss_pct
        self.timeout = datetime.now() + timeout
        self.min_order_size = self.exchange.exchange_wrapper.min_order_size

        # self.order_volume = None
        # self.entered_price = None
        # self.exited_price = None

        # self.enter_order_id = None
        # self.exit_order_id = None

    def limit_exit(self, ask_price):
        ask_volume = self.order_volume
        # setup order to sell as soon as we do not loose (possibly making profit...)
        if ask_volume > self.min_order_size:
            self.exited_price = ask_price
            exit_order = self.exchange.limit_order(Consts.ASK, ask_volume, ask_price)
            if exit_order:
                self.exit_order_id = exit_order.get('order_id')
                if not exit_order.get('success'):
                    self.logger.error("Limit Order cannot be placed !")
                    return None
                else:
                    pass  # dry-run

            return self.exit_order_id
        else:
            self.logger.warning("Volume too low, CANNOT sell !")

    def market_exit(self, ask_price):  # TODO : review point of price here ? isnt it midpoitn ?
        ask_volume = self.order_volume
        if ask_volume > self.min_order_size:
            self.exited_price = ask_price
            exit_order = self.exchange.market_order(Consts.ASK, ask_volume, ask_price)
            if exit_order:
                self.exit_order_id = exit_order.get('order_id')
                if not exit_order.get('success'):
                    self.logger.error("Market Order cannot be placed !")
                    return None
                else:
                    pass  # dry-run

            return self.exit_order_id
        else:
            self.logger.warning("Volume too low, CANNOT sell !")


class Trade(Position):

    def __init__(self, exchange, logger, targetted_profit_pct, acceptable_loss_pct, timeout = timedelta(minutes=1)):
        self.logger = logger
        self.exchange = exchange
        self.targetted_profit_pct = targetted_profit_pct
        self.acceptable_loss_pct = acceptable_loss_pct
        self.timeout = datetime.now() + timeout
        self.min_order_size = self.exchange.exchange_wrapper.min_order_size

        self.order_volume = None
        self.entered_price = None
        self.exited_price = None

        self.enter_order_id = None
        self.exit_order_id = None

    def limit_enter(self, bid_volume, bid_price):

        if bid_volume > self.min_order_size:
            enter_order = self.exchange.limit_order(Consts.BID, bid_volume, bid_price)
            if enter_order:
                self.enter_order_id = enter_order.get('order_id')
                if not enter_order.get('success'):
                    self.logger.error("Limit Order cannot be placed !")
                    return None
                else:
                    pass  # dry-run

            self.order_volume = bid_volume
            self.entered_price = bid_price
            return self.enter_order_id
        else:
            self.logger.warning("Volume too low, CANNOT buy !")

    def market_enter(self, bid_volume, bid_price):  # TODO : review point of price here ? isnt it midpoitn ?

        if bid_volume > self.min_order_size:
            enter_order = self.exchange.market_order(Consts.BID, bid_volume, bid_price)
            if enter_order:
                self.enter_order_id = enter_order.get('order_id')
                if not enter_order.get('success'):
                    self.logger.error("Market Order cannot be placed !")
                    return None
                else:
                    pass  # dry-run

            self.order_volume = bid_volume
            self.entered_price = bid_price
            return self.enter_order_id
        else:
            self.logger.warning("Volume too low, CANNOT buy !")

    @property
    def exit_loss_price(self):
        return self.entered_price * (1-self.acceptable_loss_pct)

    @property
    def exit_profit_price(self):
        return self.entered_price * (1+self.targetted_profit_pct)

    def setup(self, bid_volume, bid_price):
        # enter and setup exit at once when possible !
        self.market_enter(bid_volume=bid_volume, bid_price=bid_price)
        if self.balance.get(self.order_volume.currency) > self.order_volume:
            # TODO : use some kind of continuation for retry ?
            self.limit_exit(ask_price=self.exit_profit_price)

    def tick(self, balance, midpoint, open_orders):
        self.logger.info(str(self))

        self.logger.info("Current Orders: " + str(open_orders))

        # TODO : update our own order tracking...
        enter_order_found = False
        exit_order_found = False
        for o in open_orders.get(self.exchange, []):
            if self.exit_order_id and o.get('id') == self.exit_order_id:
                exit_order_found = True
            if self.enter_order_id and o.get('id') == self.enter_order_id:
                enter_order_found = True

        if self.enter_order_id and not enter_order_found:
            self.logger.info("ENTER buy order has been consumed or cancelled.")
            self.enter_order_id = None

        if self.exit_order_id and not exit_order_found:
            self.logger.info("EXIT order has been consumed or cancelled. ")  # TODO : profit report...
            self.exit_order_id = None

        # time to place exit order if still needed
        if not self.exit_order_id and balance.get(self.order_volume.currency) > self.order_volume:
            # we have the funds !
            self.limit_exit(self.exit_profit_price)

        elif self.timeout < datetime.now():
            # TODO : maybe should put stop_loss early on as well ?
            self.logger.info("Position timeout passed, giving up...")

            if self.exit_order_id:  # timeout expired, sell quickly, even at a loss
                self.exchange.cancel_order(self.exit_order_id)

            self.market_exit(midpoint)

    def __str__(self):
        return "Volume: " + str(self.order_volume) + " IN: " + str(self.entered_price) + " OUT+: " + str(self.exit_loss_price) + " OUT-: " + str(self.exit_loss_price) + "Timeout: " + str(self.timeout - datetime.now())


class MarketHodl(Strategy):
    """
    A simple hodl-until-profit strategy ( with a timeout )
    Only made for bull markets...
    Should be balanced with a bear market strategy that uses shorts and leverage...
    """

    def __init__(self, db, harness, strategy_configuration):
        super(MarketHodl, self).__init__(db, harness)

        self.logger = logging.getLogger(__name__)

        handler = logging.handlers.RotatingFileHandler('market_hodl.' + datetime.now().strftime("%Y%m%d-%H%M%S") + '.log')
        formatter = logging.Formatter(
            '%(asctime)s %(name)-12s %(levelname)-8s %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

        self.logger.debug("--Strategy Init--")

        self.exchange = None
        self.position_timeout = None
        self.enter_order = None
        self.pos_entered_midpoint = None
        self.profit_sell_order = None
        self.loss_sell_order = None

        self.trades = []

        self.midpoint_derivative = TS()
        self.midpoints = LocalMoneyTS(cur='EUR')

        # Defaults values for settings
        self.stake_currency = u'BTC'
        self.quote_currency = u'EUR'
        self.bullcount_periods = Decimal('5')
        self.bullcount_trend = Decimal('4')
        self.bearcount_periods = Decimal('5')
        self.bearcount_trend = Decimal('4')

        self.base_volume = Money('0.005', 'BTC')
        self.hodl_until_profit_base = Decimal('0.02')
        self.hodl_until_loss_base = Decimal('0.003')
        self.hodl_timeout_base = Decimal('30')

        self.configure(strategy_configuration)

    def configure(self, strategy_configuration):
        super(MarketHodl, self).configure(strategy_configuration)

        self.logger.debug("--Strategy Configure--")
        self.init_configurable('exchange', strategy_configuration)

        self.init_configurable('stake_currency', strategy_configuration)
        self.init_configurable('quote_currency', strategy_configuration)
        self.init_configurable('bullcount_periods', strategy_configuration)
        self.init_configurable('bullcount_trend', strategy_configuration)
        self.init_configurable('bearcount_periods', strategy_configuration)
        self.init_configurable('bearcount_trend', strategy_configuration)
        self.init_configurable('base_volume', strategy_configuration)
        self.init_configurable('hodl_until_profit_base', strategy_configuration)
        self.init_configurable('hodl_until_loss_base', strategy_configuration)
        self.init_configurable('hodl_timeout_base', strategy_configuration)

        self.init_primary_exchange()

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


    def tick(self, current_orders):

        self.logger.debug("--Strategy Tick--")
        # NOTE : we currently minimize the number of simultaneous trades, to avoid unintended tricky behavior...

        balance = self.primary_exchange.get_balance()
        ob = self.primary_exchange.get_orderbook()

        midpoint = midpoint_lib.get_midpoint_from_orderbook(ob)
        self.midpoints += midpoint

        self.logger.info("Current midpoint of orderbook : " + str(midpoint))

        # TODO : complex tech analysis...
        md = self.midpoints.deriv(last=1)
        if md:
            self.midpoint_derivative += md

        if self.position and self.position > self.primary_exchange.exchange_wrapper.min_order_size:
            self.logger.info("Already in a position!")

            primary_position = self.primary_exchange.exchange_account.position
            sell_fees_estimation = primary_position[self.stake_currency].amount *midpoint * self.primary_exchange.exchange_wrapper.fee
            self.logger.info("Exchange Position Fees estimated: " + str(sell_fees_estimation))

            if md and not self.trades:
                self.logger.warning("Exchange position without ongoing trades !")
                primary_position = self.primary_exchange.exchange_account.position
                self.logger.info(("Exchange position" + str(primary_position)))

                self.logger.info("Thinking about recovering from current position...")
                # think about entering a position on this exchange+pair

                bearcount = 0
                for md in reversed(self.midpoint_derivative):
                    bearcount += (1 if md[1] > 0 else 0)
                if len(self.midpoint_derivative) > self.bearcount_periods:
                    self.midpoint_derivative.dropfirst(len(self.midpoint_derivative) - int(self.bearcount_periods))

                if bearcount >= self.bearcount_trend:

                    self.logger.info("BEAR market detected !")

                    # Ultimately make decision
                    self.logger.info("Attempting to enter a new position...")

                    new_trade = TradePosition(exchange=self.primary_exchange, logger=self.logger,
                                              targetted_profit_pct=self.hodl_until_profit_base,
                                              acceptable_loss_pct=self.hodl_until_loss_base,
                                              timeout=timedelta(seconds=int(self.hodl_timeout_base)))

                    if new_trade.market_enter(bid_volume=self.base_volume, bid_price=midpoint):
                        self.trades.append(new_trade)
                    else:
                        pass  # just forget about it...
                else:
                    self.logger.info(
                        "UNSURE market... BULL indicator " + str(bearcount) + "/" + str(self.bearcount_trend))

            # Note : currently try to keep only one trade at a time
            else:
                for t in self.trades:
                    # profitable trades should be done now, and non profitable trades should wait... until timeout
                    t.tick(balance, midpoint, current_orders)

        # Note : currently try to keep only one trade at a time
        else:
            self.logger.info("Current position too weak. Attempting new one...")

            if md and not self.trades:
                self.logger.info("Thinking about entering a new position...")
                # think about entering a position on this exchange+pair


                bullcount = 0
                for md in reversed(self.midpoint_derivative):
                    bullcount += (1 if md[1] > 0 else 0)
                if len(self.midpoint_derivative) > self.bullcount_periods:
                    self.midpoint_derivative.dropfirst(len(self.midpoint_derivative) - int(self.bullcount_periods))

                if bullcount >= self.bullcount_trend:

                    self.logger.info("BULL market detected !")

                    # Ultimately make decision
                    self.logger.info("Attempting to enter a new position...")

                    new_trade = TradePosition(exchange=self.primary_exchange, logger= self.logger, targetted_profit_pct=self.hodl_until_profit_base,
                                              acceptable_loss_pct=self.hodl_until_loss_base, timeout = timedelta(seconds=int(self.hodl_timeout_base)))
                    if new_trade.market_enter(bid_volume=self.base_volume, bid_price=midpoint):
                        self.trades.append(new_trade)
                    else:
                        pass  # just forget about it...
                else:
                    self.logger.info("UNSURE market... BULL indicator " + str(bullcount) + "/" + str(self.bullcount_trend))

            # Note : currently try to keep only one trade at a time
            else:
                for t in self.trades:
                    # profitable trades should be done now, and non profitable trades should wait... until timeout
                    t.tick(balance, midpoint, current_orders)




        #
        #
        #
        # ob = self.primary_exchange.get_orderbook()
        #
        # midpoint = midpoint_lib.get_midpoint_from_orderbook(ob)
        # self.midpoints += midpoint
        #
        # self.logger.info("Current midpoint of orderbook : " + str(midpoint))
        #
        # if self.position:
        #     self.logger.info("Already in a position !")
        #
        #     primary_position = self.primary_exchange.exchange_account.position
        #
        #     # Calculate how we can make a profit...
        #
        #     if self.pos_entered_midpoint:
        #         position_entered_price = Money(-primary_position[self.quote_currency].amount / primary_position[self.stake_currency].amount, currency = self.quote_currency) # TODO : better management of units ? (see pint)
        #         self.logger.info("Position " + str(primary_position) + " Entered at price : " + str(position_entered_price))
        #
        #
        #     sell_fees_estimation = primary_position[self.stake_currency].amount * self.primary_exchange.exchange_wrapper.fee * midpoint
        #     self.logger.info("SELL Fees estimated: " + str(sell_fees_estimation))
        #
        #     #position_entered_price = Money(-primary_position[self.quote_currency].amount / primary_position[self.stake_currency].amount, currency = self.quote_currency) # TODO : better management of units ? (see pint)
        #     #self.logger.info("Position " + str(primary_position) + " Entered at price : " + str(position_entered_price))
        #     # Note : the position_entered_price should already take buying fees into account -> we dont bother with it...
        #
        #     profit_sell_price_minimum = Money(primary_position[self.quote_currency].amount / primary_position[self.stake_currency].amount, currency = self.quote_currency)
        #
        #     #profit_sell_price_minimum = Money(sell_fees_estimation.amount - primary_position[self.quote_currency].amount / (
        #     #            primary_position[self.stake_currency].amount), currency= self.quote_currency)
        #     self.logger.info("MINIMUM sell price to prevent loss: " + str(profit_sell_price_minimum))
        #
        #     profit_sell_price_optimum = profit_sell_price_minimum * (1+self.hodl_until_profit_base)
        #     self.logger.info("PROFIT sell price: " + str(profit_sell_price_optimum))
        #
        #     loss_sell_price = profit_sell_price_minimum * (1-self.hodl_until_loss_base)
        #     self.logger.info("LOSS sell price: " + str(loss_sell_price))
        #
        #     # by default we sell the whole position
        #     ask_volume = self.position
        #
        #     if not self.position_timeout:
        #         # we got position from an old run of this strategy
        #         if not self.profit_sell_order:
        #             # Lost track of strategy timeout & target -> sell ASAP (without loss)
        #
        #             self.logger.info("Position not from this run, attempting to sell at minimum loss price...")
        #
        #             profit_sell_price = max(profit_sell_price_minimum, midpoint)
        #
        #             # setup order to sell as soon as we do not loose (possibly making profit...)
        #             if ask_volume > self.primary_exchange.exchange_wrapper.min_order_size:
        #                 self.profit_sell_order = self.primary_exchange.limit_order(Consts.ASK, ask_volume, profit_sell_price)
        #                 if self.profit_sell_order and not self.profit_sell_order.get('success'):
        #                     self.logger.error("Limit Order cannot be placed !")
        #             else:
        #                 self.logger.warning("Volume too low, CANNOT sell !")
        #
        #             # DO NOT ENTER A NEW POSITION BEFORE CLEANING UP PREVIOUS ONE, since we didnt track some details.
        #             # Except if current position is not enough to trade....
        #             # TODO : careful with blockage because of this...
        #
        #         else:  # position not from this run, but profit sell order already in place...
        #             pass  # just do nothing...
        #
        #     elif self.position_timeout > datetime.now() and not self.loss_sell_order:  # timeout expired, sell quickly, even at a loss
        #             # TODO : maybe should put stop_loss early on instead ?
        #
        #         self.logger.info("Position timeout passed, giving up...")
        #
        #         if ask_volume > self.primary_exchange.exchange_wrapper.min_order_size:
        #             if self.profit_sell_order:
        #                 self.primary_exchange.cancel_order(self.profit_sell_order.get('order_id'))
        #
        #                 self.loss_sell_order = self.primary_exchange.market_order(Consts.ASK, ask_volume, midpoint)
        #                 if self.loss_sell_order and not self.loss_sell_order.get('success'):
        #                     self.logger.error("Market Order cannot be placed !")
        #
        #         else:
        #             self.logger.warning("Volume too low, CANNOT sell !")
        #     # Make decision based on difference between when we entered the position and now...
        #
        #     if profit_sell_price_optimum < midpoint:
        #         if not self.profit_sell_order:  # if we made profit already
        #             self.logger.info("Position PROFIT reached ! Selling...")
        #             # sell it !
        #
        #             # limit order to sell at guaranteed price (even if market changed under us... we hope it will come back !)
        #
        #             if ask_volume > self.primary_exchange.exchange_wrapper.min_order_size:
        #                 self.profit_sell_order = self.primary_exchange.limit_order(Consts.ASK, ask_volume, midpoint)
        #                 if self.profit_sell_order and not self.profit_sell_order.get('success'):
        #                     self.logger.error("Limit Order cannot be placed !")
        #
        #             else:
        #                 self.logger.warning("Volume too low, CANNOT sell !")
        #             # TODO : improve this : let it bull, but follow closely....
        #         else:
        #             #TODO : eventually cancel and pass new one again ?? maybe not useful ?
        #
        #             self.logger.info("PROFIT Sell order is already in place, but hasnt been consumed...")
        #             pass
        #
        #     elif loss_sell_price > midpoint:
        #         if not self.loss_sell_order:  # if we lost already
        #             self.logger.info("Position loss limit reached ! Selling at a LOSS...")
        #             # setup trailing stop
        #
        #             if ask_volume > self.primary_exchange.exchange_wrapper.min_order_size:
        #                 if self.profit_sell_order:
        #                     self.primary_exchange.cancel_order(self.profit_sell_order.get('order_id'))
        #
        #                 # market order to sell asap and avoid further loss...
        #                 self.loss_sell_order = self.primary_exchange.market_order(Consts.ASK, ask_volume, midpoint)
        #                 if self.loss_sell_order and not self.loss_sell_order.get('success'):
        #                     self.logger.error("Market Order cannot be placed !")
        #
        #             else:
        #                 self.logger.warning("Volume too low, CANNOT sell !")
        #         else:
        #             # TODO : eventually cancel and pass new one again ?? maybe not useful ?
        #             self.logger.info("LOSS Sell order is already in place, but hasnt been consumed...")
        #             pass
        #
        #     else:  # we didnt profit enough BUT we didnt loose yet...
        #         self.logger.info("Position still undecided...")
        #         pass
        #
        # if not self.position or self.position < self.primary_exchange.exchange_wrapper.min_order_size:  # one position at a time for now. except when position is not enough for passing an order
        #
        #     if not self.enter_order:
        #         self.logger.info("Thinking about entering a new position...")
        #         # think about entering a position on this exchange+pair
        #
        #         # TODO : complex tech analysis...
        #         md = self.midpoints.deriv(last=1)
        #         if md:
        #             self.midpoint_derivative += md
        #
        #             bullcount = 0
        #             for md in reversed(self.midpoint_derivative):
        #                 bullcount += (1 if md[1] > 0 else 0)
        #             if len(self.midpoint_derivative) > self.bullcount_periods:
        #                 self.midpoint_derivative.dropfirst(len(self.midpoint_derivative) - int(self.bullcount_periods))
        #
        #             if bullcount >= self.bullcount_trend:
        #
        #                 self.logger.info("BULL market detected !")
        #
        #                 # Ultimately make decision
        #                 self.logger.info("Attempting to enter a new position...")
        #                 bid_volume = self.base_volume
        #
        #                 self.position_timeout = datetime.now() + timedelta(minutes=1)
        #
        #                 if bid_volume > self.primary_exchange.exchange_wrapper.min_order_size:
        #                     self.pos_entered_midpoint = midpoint
        #                     self.enter_order = self.primary_exchange.market_order(Consts.BID, bid_volume, midpoint)
        #                     if self.enter_order and not self.enter_order.get('success'):
        #                         self.logger.error("Market Order cannot be placed !")
        #             else:
        #                 self.logger.info("UNSURE market... BULL indicator " + str(bullcount) + "/" + str(self.bullcount_trend))

