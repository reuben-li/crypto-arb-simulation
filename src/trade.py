from zaifapi import *
from pprint import pprint
from quoine.client import Quoinex
import time
import pandas as pd
import json
import pybitflyer
import python_bitbankcc
import yaml
import threading
from datetime import datetime, timedelta
import logging
import os
import queue
import tailer
# import matplotlib.pyplot as plt

logging.basicConfig(filename='trade.log', level=logging.WARNING)

# globals
PLOT = False
EXCHANGES = ['bb', 'qn']
BF_FEES = 0.0015
BTC_REF = 905000  # to filter out market fluctuation
SIZE = 0.003
BTC_MIN = SIZE * (len(EXCHANGES) + 0.2)
JPY_MIN = BTC_MIN * BTC_REF
MARGIN = 400  # JPY per BTC
LOW_MARGIN = 200
MIN_MARGIN = 0
LOW_RATIO = 3  # when are funds considered low
STABLE_VOL_FLOAT = 0.1
COLORS = ['blue', 'green', 'red', 'orange']

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
qn_client = Quoinex(auth['qn']['key'], auth['qn']['secret'])


def bb_trade(direction, price, size=SIZE):
    bb_client_pte.order(
        'btc_jpy', '', size, direction.lower(), 'market'
    )


def bf_trade(direction, price, size=SIZE):
    bf_client.sendchildorder(
        product_code='BTC_JPY', child_order_type='MARKET',
        side=direction, size=size
    )


def qn_trade(direction, price, size=SIZE):
    if direction == 'BUY':
        qn_client.create_market_buy(
            product_id=5,
            quantity=size
        )
    else:
        qn_client.create_market_sell(
            product_id=5,
            quantity=size
        )


def zf_trade(direction, price, size=SIZE):
    action = 'bid' if direction == 'BUY' else 'ask'
    zf_pclient.trade(
        currency_pair='btc_jpy', action=action,
        amount=size, price=int(price)
    )


def trade(ex, direction, price, size=SIZE):
    if ex == 'zf':
        zf_trade(direction, price, size=SIZE)
    elif ex == 'bb':
        bb_trade(direction, price, size=SIZE)
    elif ex == 'qn':
        qn_trade(direction, price, size=SIZE)
    elif ex == 'bf':
        bf_trade(direction, price, size=SIZE)


def bb_price(q, mxb, mna, status):
    res = bb_client.get_depth('btc_jpy')
    price_decision(q, res, 'asks', 'bids', mna, mxb, 'bb', status)


def qn_price(q, mxb, mna, status):
    res = qn_client.get_order_book(5, full=True)
    price_decision(q, res, 'sell_price_levels',
                   'buy_price_levels', mna, mxb, 'qn', status)


def zf_price(q, mxb, mna, status):
    res = zf_client.depth('btc_jpy')
    price_decision(q, res, 'asks', 'bids', mna, mxb, 'zf', status)


def price(ex, q, mxb, mna, status):
    if ex == 'zf':
        return zf_price(q, mxb, mna, status)
    elif ex == 'bb':
        return bb_price(q, mxb, mna, status)
    elif ex == 'qn':
        return qn_price(q, mxb, mna, status)
    # elif ex == 'bf':
    #     return bf_price(q, mxb, mna, status)


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


def qn_portfolio():
    try:
        for i in qn_client.get_account_balances():
            if i['currency'] == 'JPY':
                qn_jpy = float(i['balance'])
            elif i['currency'] == 'BTC':
                qn_btc = float(i['balance'])
        return qn_jpy, qn_btc
    except Exception as e:
        print('cannot get qn assets')
        time.sleep(5)
        return qn_portfolio()


def zf_portfolio():
    res = zf_pclient.get_info()
    return res['funds']['jpy'], res['funds']['btc']


def portfolio(ex):
    if ex == 'zf':
        return zf_portfolio()
    elif ex == 'bb':
        return bb_portfolio()
    elif ex == 'qn':
        return qn_portfolio()
    elif ex == 'bf':
        return bf_portfolio()


def portfolio_value():
    table = {}
    table['jpy'] = {}
    table['btc'] = {}
    status = {}  # status: 0=no trading, 1=low funds, 2=normal

    for e in EXCHANGES:
        status[e] = {'buy': 2, 'sell': 2}
        jpy, btc = portfolio(e)

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


def dynamic_margins(margin, ask_e, ask, bid_e, bid, status):
    if margin > MARGIN:
        if status[ask_e]['buy'] > 0 and status[bid_e]['sell'] > 0:
            simul_orders(ask_e, bid_e, ask, bid, 2)
    elif margin > LOW_MARGIN:
        if (status[ask_e]['sell'] < 2 and status[bid_e]['buy'] > 0) or \
                (status[bid_e]['buy'] < 2 and status[bid_e]['sell'] > 0):
            simul_orders(ask_e, bid_e, ask, bid, 1)
    elif margin > MIN_MARGIN:
        if (status[ask_e]['sell'] == 0 and status[bid_e]['buy'] > 0) or \
                (status[bid_e]['buy'] == 0 and status[ask_e]['sell'] > 0):
            simul_orders(ask_e, bid_e, ask, bid, 0)


def price_decision(q, res, ask_key, bid_key, mna, mxb, ex, status):
    for ask, askv in res[ask_key]:
        if float(askv) > STABLE_VOL_FLOAT:
            ask_e, min_ask = mna.get()
            bid_e, max_bid = mxb.get()
            ask = float(ask)
            if ask < min_ask:
                mna.put((ex, ask))
            else:
                mna.put((ask_e, min_ask))
            margin_a = max_bid - ask
            dynamic_margins(margin_a, ex, ask, bid_e, max_bid, status)
            for bid, bidv in res[bid_key]:
                if float(bidv) > STABLE_VOL_FLOAT:
                    bid = float(bid)
                    if bid > max_bid:
                        mxb.put((ex, bid))
                    else:
                        mxb.put((bid_e, max_bid))
                    margin_b = bid - min_ask
                    dynamic_margins(margin_b, ask_e,
                                    min_ask, ex, bid, status)
                    q.put({ex + '_ask': ask})
                    q.put({ex + '_bid': bid})
                    return


def simul_orders(bx, sx, bprice, sprice, level):
    t1 = threading.Thread(
        target=trade, args=(bx, 'BUY', bprice))
    t2 = threading.Thread(
        target=trade, args=(sx, 'SELL', sprice))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    logging.warning(
        str(datetime.now() + timedelta(hours=9)) +
        ' buy(' + str(level) + ') ' + bx + ':' +
        str(bprice) + ' sell ' + sx + ':' + str(sprice)
    )


def trade_data(table, status):
    mxb = queue.Queue()
    mxb.put(('xx', 1))
    mna = queue.Queue()
    mna.put(('xx', 5000000))
    q = queue.Queue()

    threads = []
    for e in EXCHANGES:
        threads.append(
            threading.Thread(
                target=price, args=(e, q, mxb, mna, status)
            )
        )
    [t.start() for t in threads]
    [t.join() for t in threads]

    data = {}
    [data.update(q.get()) for i in range(len(EXCHANGES)*2)]

    ask_e, min_ask = mna.get()
    bid_e, max_bid = mxb.get()

    return data, (max_bid - min_ask)


def main(plot):
    if plot:
        d = []
        plt.ion()
        fig = plt.figure()
        ax = fig.add_subplot(111)
    old_total = 0
    print('------------\nSTART!\n-----------')

    i = 0
    with open('last_table.json') as data_file:
        old_table = json.load(data_file)
    start = time.time()

    while True:
        internal = time.time()
        i += 1
        table, status = portfolio_value()
        data, current_margin = trade_data(table, status)

        if i % 3 == 0 or i == 2:
            total_value = (table['btc']['_total'] *
                           BTC_REF) + \
                           table['jpy']['_total']
            table['total_value'] = total_value
            os.system('clear')
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
            print('TRADE STATUS')
            print('-------')
            pprint(status)
            d = sorted(data.items(), key=lambda kv: kv[1], reverse=True)
            for k, v in d:
                print(k, v)
            print('best margin: ', round(current_margin, 5))
            print('-------')
            print('RECENT TRADES')
            print('-------')
            for line in tailer.tail(open('trade.log'), 6):
                print(line.replace('WARNING:root:', ''))
            print('-------')
            print('TIME')
            print('-------')
            print('Elapsed:', round((time.time() - start)/60, 1), 'mins')
            print('Loop time:', round((time.time() - internal), 4), 's')

            with open('last_table.json', 'w') as outfile:
                json.dump(table, outfile)

        if plot:
            d.append(data)
            if i > 30:
                d.pop(0)
            df = pd.DataFrame(data=d)
            rows = -30
            ax.clear()
            e_color = 0

            for e in EXCHANGES:
                ax.plot(df.index[rows:], df[e+'_ask'][rows:],
                        label=e+'_ask', color=COLORS[e_color])
                ax.plot(df.index[rows:], df[e+'_bid'][rows:],
                        label=e+'_bid', linestyle='dashed',
                        color=COLORS[e_color])
                e_color += 1
            ax.legend()
            fig.canvas.draw()
            fig.canvas.flush_events()
        time.sleep(2)


if __name__ == "__main__":
    try:
        main(PLOT)
    except KeyboardInterrupt:
        print('exiting')
        quit()
    except Exception as e:
        time.sleep(10)
        main(PLOT)
