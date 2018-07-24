from zaifapi import *
from pprint import pprint
import time
# import pandas as pd
import json
import pybitflyer
import python_bitbankcc
import yaml
import gevent
import datetime
import logging
import os

logging.basicConfig(filename='trade.log', level=logging.INFO)

# globals
EXCHANGES = ['bb', 'zf']
JPY_MIN = 1000
BTC_MIN = 0.0021
BF_FEES = 0.0015
SIZE = 0.001
MARGIN = 0.0007
LOW_MARGIN = 0.000
MIN_MARGIN = -(MARGIN * 0.6)
LOW_RATIO = 4 # when are funds considered low
STABLE_VOL = 0.05 # when is volume considered stable
COLORS = ['blue', 'green', 'red', 'orange']
BTC_REF = 905000  # to filter out market fluctuation

# import credentials
with open('config.yml', 'r') as ymlfile:
    cfg = yaml.load(ymlfile)
auth = cfg['auth']

# instantiate clients
bf_client = pybitflyer.API(
    api_key=auth['bf']['key'], api_secret=auth['bf']['secret'])
bb_client_pte = python_bitbankcc.private(
    auth['bb']['key'], auth['bb']['secret'])
bb_client = python_bitbankcc.public()
zf_pclient = ZaifTradeApi(
    auth['zf']['key'], auth['zf']['secret'])
zf_client = ZaifPublicApi()


def bb_trade(direction, price, size=SIZE):
    bb_client_pte.order(
        'btc_jpy', '', size, direction.lower(), 'market'
    )


def bf_trade(direction, price, size=SIZE):
    bf_client.sendchildorder(
        product_code='BTC_JPY', child_order_type='MARKET',
        side=direction, size=size
    )


def zf_trade(direction, price, size=SIZE):
    action = 'bid' if direction == 'BUY' else 'ask'
    zf_pclient.trade(
        currency_pair='btc_jpy', action=action,
        amount=size, price=price
    )


def bb_price():
    res = bb_client.get_depth('btc_jpy')
    for ask, askv in res['asks']:
        if float(askv) > STABLE_VOL:
            break
    for bid, bidv in res['bids']:
        if float(bidv) > STABLE_VOL:
            break
    return int(ask), int(bid)


def bf_price():
    res = bf_client.ticker(product_code='BTC_JPY')
    return float(res['best_ask']*(1 + BF_FEES)), \
        float(res['best_bid']*(1 - BF_FEES))


def zf_price():
    res = zf_client.depth('btc_jpy')
    for ask, askv in res['asks']:
        if float(askv) > STABLE_VOL:
            break
    for bid, bidv in res['bids']:
        if float(bidv) > STABLE_VOL:
            break
    return int(ask), int(bid)


def bb_portfolio():
    assets = bb_client_pte.get_asset()['assets']
    for a in assets:
        if a['asset'] == 'jpy':
            bb_jpy = float(a['onhand_amount'])
        elif a['asset'] == 'btc':
            bb_btc = float(a['onhand_amount'])
    return bb_jpy, bb_btc


def bf_portfolio():
    for res in bf_client.getbalance():
        if res['currency_code'] == 'JPY':
            bf_jpy = res['amount']
        elif res['currency_code'] == 'BTC':
            bf_btc = res['amount']
    return bf_jpy, bf_btc


def zf_portfolio():
    res = zf_pclient.get_info()
    return res['funds']['jpy'], res['funds']['btc']


def portfolio_value():
    table = {}
    table['jpy'] = {}
    table['btc'] = {}
    status = {}  # status: 0=no trading, 1=low funds, 2=normal

    for e in EXCHANGES:
        status[e] = {'buy': 2, 'sell': 2}
        jpy, btc = globals()[e + '_portfolio']()

        if jpy < JPY_MIN:
            status[e]['buy'] = 0
        elif jpy < JPY_MIN * LOW_RATIO:
            status[e]['buy'] = 1

        if btc < BTC_MIN:
            status[e]['sell'] = 0
        elif btc < BTC_MIN * LOW_RATIO:
            status[e]['sell'] = 1

        table['jpy'][e] = jpy
        table['btc'][e] = btc

    table['jpy']['_total'] = sum(table['jpy'].values())
    table['btc']['_total'] = sum(table['btc'].values())
    return table, status


def estimated(table, buying, selling, bprice, sprice):
    table['jpy'][buying] -= bprice
    table['btc'][buying] += SIZE
    table['jpy'][selling] += sprice
    table['btc'][selling] -= SIZE
    return table


def trade_data(table, status):
    data = {}
    min_ask = 2000000
    max_bid = 1
    ask_e = ''
    bid_e = ''

    def simul_orders(bx, sx, bprice, sprice, level):
        orders = [
            gevent.spawn(globals()[bx + '_trade'], bprice, 'BUY'),
            gevent.spawn(globals()[sx + '_trade'], sprice, 'SELL')
        ]
        gevent.joinall(orders)
        logging.info(
            str(datetime.datetime.now()) + ' buy(' + str(level) + ') ' +
            bx + ':' + str(bprice) + ' sell ' + sx + ':' + str(sprice)
        )

    for e in EXCHANGES:
        ask, bid = globals()[e + '_price']()
        margin_a = (max_bid - ask) / max_bid
        margin_b = (bid - min_ask) / bid

        if margin_a > MARGIN:
            if status[e]['buy'] > 0 and status[bid_e]['sell'] > 0:
                simul_orders(e, bid_e, ask, max_bid, 2)
        elif margin_a > LOW_MARGIN:
            if status[e]['sell'] < 2 or status[bid_e]['buy'] < 2:
                simul_orders(e, bid_e, ask, max_bid, 1)
        elif margin_a > MIN_MARGIN:
            if status[e]['sell'] == 0 or status[bid_e]['buy'] == 0:
                simul_orders(e, bid_e, ask, max_bid, 0)

        if margin_b > MARGIN:
            if status[ask_e]['buy'] > 0 and status[e]['sell'] > 0:
                simul_orders(ask_e, e, min_ask, bid, 2)
        elif margin_b > LOW_MARGIN:
            if status[ask_e]['sell'] < 2 or status[e]['buy'] < 2:
                simul_orders(ask_e, e, min_ask, bid, 1)
        elif margin_b > MIN_MARGIN:
            if status[ask_e]['sell'] == 0 or status[e]['buy'] == 0:
                simul_orders(ask_e, e, min_ask, bid, 0)

        min_ask, ask_e = (ask, e) if ask < min_ask else (min_ask, ask_e)
        max_bid, bid_e = (bid, e) if bid > max_bid else (max_bid, bid_e)
        data[e + '_ask'] = ask
        data[e + '_bid'] = bid

    return data, (max_bid - min_ask) / max_bid


def main():
    #d = []
    #plt.ion()
    #fig = plt.figure()
    #ax = fig.add_subplot(111)
    old_total = 0
    print('------------\nSTART!\n-----------')

    i = 0
    with open('last_table.json') as data_file:
        old_table = json.load(data_file)
        start = time.time()

    while True:
        i += 1
        table, status = portfolio_value()
        data, current_margin = trade_data(table, status)

        if i % 5 == 0 or i == 2:
            total_value = (table['btc']['_total'] *
                           BTC_REF) + \
                           table['jpy']['_total']
            table['total_value'] = total_value
            total_diff = total_value - old_total
            old_total = total_value
            os.system('cls||clear')
            print('-------')
            print('PORTFOLIO')
            print('-------')
            pprint(table)
            print('-------')
            print('GROWTH')
            print('-------')
            new_jpy = table['jpy']['_total']
            old_jpy = old_table['jpy']['_total']
            jpy_net = (new_jpy - old_jpy) / old_jpy

            new_btc = table['btc']['_total']
            old_btc = old_table['btc']['_total']
            btc_net = (new_btc - old_btc) / old_btc

            new_total = table['total_value']
            old_total = old_table['total_value']
            growth = new_total - old_total
            growth_per = growth / old_total

            print('jpy growth(%)', round(jpy_net * 100, 3))
            print('btc growth(%)', round(btc_net * 100, 3))
            print('net growth(%)', round(growth_per * 100, 3))
            print('net growth(JPY)', round(growth, 3))
            print('-------')
            print('STATUS')
            print('-------')
            pprint(status)
            print('current margin: ', round(current_margin, 5))
            print('-------')
            print('ELAPSED TIME (mins)')
            print('-------')
            print(round((time.time() - start)/60, 1))

            with open('last_table.json', 'w') as outfile:
                json.dump(table, outfile)

        #d.append(data)
        #if i > 30:
        #    d.pop(0)
        #df = pd.DataFrame(data=d)
        #rows = -30
        #ax.clear()
        #e_color = 0

        #for e in EXCHANGES:
        #    ax.plot(df.index[rows:], df[e+'_ask'][rows:],
        #            label=e+'_ask', color=COLORS[e_color])
        #    ax.plot(df.index[rows:], df[e+'_bid'][rows:],
        #            label=e+'_bid', linestyle='dashed', color=COLORS[e_color])
        #    e_color += 1
        #ax.legend()
        #fig.canvas.draw()
        #fig.canvas.flush_events()
        time.sleep(1.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print('exiting')
        quit()
    except Exception as e:
        time.sleep(10)
        main()
