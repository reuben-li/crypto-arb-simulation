import time
import binance
import pandas as pd
from pprint import pprint
import matplotlib.pyplot as plt
from quoine.client import Quoinex
import json
import pybitflyer
import python_bitbankcc
import os
import yaml
from zaifapi import *
from IPython.display import clear_output
import gevent
import logging
import datetime
logging.basicConfig(filename='trade.log',level=logging.INFO)

BTCJPY = 814865
JPY_MIN = 1000
BTC_MIN = 0.0011
BF_FEES = 0.0015
SIZE = 0.001
EXCHANGES = ['bb', 'zf']
MARGIN = 0.0012
LOW_MARGIN = 0.000
# balance internally if better than cross exchange margins
INTRA = 0.9999
DEPTH_RANK = 1
COLORS = ['blue', 'green', 'red', 'orange']

## import credentials
with open("config.yml", 'r') as ymlfile:
    cfg = yaml.load(ymlfile)

auth = cfg['auth']

## instantiate clients
# qn_client = Quoinex(auth['qn']['key'], auth['qn']['secret'])
bf_client = pybitflyer.API(api_key=auth['bf']['key'], api_secret=auth['bf']['secret'])
# binance.set(auth['bn']['key'], auth['bn']['secret'])
bb_client_pte = python_bitbankcc.private(auth['bb']['key'], auth['bb']['secret'])
bb_client = python_bitbankcc.public()
zf_pclient = ZaifTradeApi(auth['zf']['key'], auth['zf']['secret'])
zf_client = ZaifPublicApi()

def bf_trade(direction, size=SIZE):
    bf_client.sendchildorder(
        product_code="BTC_JPY",
        child_order_type="MARKET",
        side=direction,
        size=size
    )
    
def bf_price():
    res = bf_client.ticker(product_code="BTC_JPY")
    return float(res['best_ask']*(1 + BF_FEES)), float(res['best_bid']*(1 - BF_FEES))

def bf_portfolio():
    for i in bf_client.getbalance():
        if i['currency_code'] == 'JPY':
            bf_jpy = i['amount']
        elif i ['currency_code'] == 'BTC':
            bf_btc = i['amount']
    return bf_jpy, bf_btc
    
def qn_trade(direction, size=SIZE):
    if direction == 'BUY':
        qn_client.create_market_buy(
            product_id=5,
            quantity=size
        )
    elif direction == 'SELL':
        qn_client.create_market_buy(
            product_id=5,
            quantity=size
        )
    else:
        print('indicate BUY or SELL')
        
def qn_portfolio():
    for i in qn_client.get_account_balances():
        if i['currency'] == 'JPY':
            qn_jpy = float(i['balance'])
        elif i ['currency'] == 'BTC':
            qn_btc = float(i['balance'])
    return qn_jpy, qn_btc
        
def bb_trade(direction, size=SIZE):
    bb_client_pte.order(
        'btc_jpy',
        '',
        size,
        direction.lower(),
        'market'
    )
    
def bb_price():
    res = bb_client.get_depth('btc_jpy')
    # res = bb_client.get_ticker('btc_jpy')
    return int(res['asks'][DEPTH_RANK][0]), int(res['bids'][DEPTH_RANK][0])

def bb_portfolio():
    assets = bb_client_pte.get_asset()['assets']
    for a in assets:
        if a['asset'] == 'jpy':
            bb_jpy = float(a['onhand_amount'])
        elif a['asset'] == 'btc':
            bb_btc = float(a['onhand_amount'])
    return bb_jpy, bb_btc

def zf_trade(direction, size=SIZE):
    ask, bid = zf_price()
    if direction == 'BUY':
        action = 'bid'
        price = ask
    else:
        action = 'ask'
        price = bid
    zf_pclient.trade(
        currency_pair='btc_jpy',
        action=action,
        amount=size,
        price=price
    )

def zf_price():
    # res = zf_client.ticker('btc_jpy')
    res = zf_client.depth('btc_jpy')
    return int(res['asks'][DEPTH_RANK][0]), int(res['bids'][DEPTH_RANK][0])

def zf_portfolio():
    a = zf_pclient.get_info()
    return a['funds']['jpy'], a['funds']['btc']

def portfolio_value():
    table = {}
    table['jpy'] = {}
    table['btc'] = {}
    status = {}
    
    for e in EXCHANGES:
        status[e] = {'buy':True, 'sell':True}
        jpy, btc = globals()[e + '_portfolio']()
        if jpy < JPY_MIN:
            status[e]['buy'] = False
        elif btc < BTC_MIN:
            status[e]['sell'] = False
        table['jpy'][e] = jpy
        table['btc'][e] = btc
    
    table['jpy']['_total'] = sum(table['jpy'].values())
    table['btc']['_total'] = sum(table['btc'].values())
    table['total_value'] = (table['btc']['_total'] * BTCJPY) + table['jpy']['_total']
    return table, status

def estimated(table, buying, selling, bprice, sprice):
    table['jpy'][buying] -= bprice
    table['btc'][buying] += SIZE
    table['jpy'][selling] += sprice
    table['btc'][selling] -= SIZE
    return table

def balancer(status):
    def yen_shortfall(e):
        if not status[e]['buy']:
            ask, bid = globals()[e + '_price']()
            if bid/ask > INTRA:
                globals()[e + '_trade']('SELL')
                print("BALANCE - selling in " + e, bid/ask, bid)
                
    def btc_shortfall(e):
        if not status[e]['sell']:
            ask, bid = globals()[e + '_price']()
            if bid/ask > INTRA:
                globals()[e + '_trade']('BUY')    
                print("BALANCE - buying in " + e, bid/ask, ask)

    orders = [gevent.spawn(yen_shortfall, e) for e in EXCHANGES]
    orders.extend([gevent.spawn(btc_shortfall, e) for e in EXCHANGES])
    gevent.joinall(orders)
                      

def trade_data(table, status):
    data = {}
    min_ask = 2000000
    max_bid = 1
    
    # best ask and bid exchanges
    ask_e = ''
    bid_e = ''

    for e in EXCHANGES:
        ask, bid = globals()[e + '_price']()
        if (max_bid - ask) / max_bid > MARGIN:
            if status[e]["buy"] and status[bid_e]['sell']:
                orders = [
                    gevent.spawn(globals()[e + '_trade'], 'BUY'),
                    gevent.spawn(globals()[bid_e + '_trade'], 'SELL'),
                    gevent.spawn(print,
                                 'TRADE - buy:' + e + ' sell:'+ bid_e + ' margin: ' + 
                                 str((max_bid - ask) / max_bid)
                                )
                ]
                gevent.joinall(orders)
                logging.info(
                    datetime.datetime.now() + ' - buy ' + e + ' : ' + str(max_bid) + 
                    ' sell '+ bid_e + ' : ' + str(ask)
                )
    
        # low funds accept low margin
        elif (max_bid - ask) / max_bid > LOW_MARGIN:
            if not status[e]['sell'] or not status[bid_e]['buy']:
                orders = [
                    gevent.spawn(globals()[e + '_trade'], 'BUY'),
                    gevent.spawn(globals()[bid_e + '_trade'], 'SELL'),
                    gevent.spawn(print,
                                 'TRADE - buy:' + e + ' sell:'+ bid_e + ' margin: ' + 
                                 str((max_bid - ask) / max_bid), max_bid, ask
                                )
                ]
                gevent.joinall(orders)
                logging.info(
                    datetime.datetime.now() + ' - buy ' + e + ' : ' + str(max_bid) + 
                    ' sell '+ bid_e + ' : ' + str(ask)
                )
        
        if (bid - min_ask) / bid > MARGIN:
            if status[ask_e]['buy'] and status[e]['sell']:
                orders = [
                    gevent.spawn(globals()[ask_e + '_trade'], 'BUY'),
                    gevent.spawn(globals()[e + '_trade'], 'SELL'),
                    gevent.spawn(print,
                                 'TRADE - buy:' + ask_e + ' sell:'+ e + ' margin: ' +
                                 str((bid - min_ask) / bid), bid, min_ask
                                )
                ]
                gevent.joinall(orders)
                logging.info(
                    datetime.datetime.now() + ' - buy ' + ask_e + ' : ' + str(bid) + 
                    ' sell '+ e + ' : ' + str(min_ask)
                )

                
        elif (bid - min_ask) / bid > LOW_MARGIN:
            if not status[ask_e]['sell'] or not status[e]['buy']:
                orders = [
                    gevent.spawn(globals()[ask_e + '_trade'], 'BUY'),
                    gevent.spawn(globals()[e + '_trade'], 'SELL'),
                    gevent.spawn(print,
                                 'TRADE - buy:' + ask_e + ' sell:'+ e + ' margin: ' +
                                 str((bid - min_ask) / bid), bid, min_ask
                                )
                ]
                gevent.joinall(orders)
                logging.info(
                    datetime.datetime.now() + ' - buy ' + ask_e + ' : ' + str(bid) + 
                    ' sell '+ e + ' : ' + str(min_ask)
                )
        
        if ask < min_ask:
            min_ask = ask
            ask_e = e
        if bid > max_bid:
            max_bid = bid
            bid_e = e

        data[e + '_ask'] = ask
        data[e + '_bid'] = bid
    return data

def main():
    %matplotlib tk
    d = []

    fig = plt.figure()
    ax = fig.add_subplot(111)
    print('-------------')
    print("START!")
    print("-------------")

    i = 0
    with open('last_table.json') as data_file:    
        old_table = json.load(data_file)
        start = time.time()

    while True:
        i += 1
        table, status = portfolio_value()

        if i % 10 == 0 or i == 2:
            clear_output(wait=True)
            print('-------')
            print('PORTFOLIO')
            print('-------')
            pprint(table)
            print('-------')
            print('GROWTH')
            print('-------')
            jpy_net = (table['jpy']['_total'] - old_table['jpy']['_total']) / old_table['jpy']['_total']
            btc_net = (table['btc']['_total'] - old_table['btc']['_total']) / old_table['btc']['_total']
            print("jpy growth(%)", round(jpy_net * 100,3))
            print("btc growth(%)", round(btc_net * 100,3))
            print("net growth(%)", round((jpy_net + btc_net) * 100,3))
            print('-------')
            print('STATUS')
            print('-------')
            pprint(status)
            print('-------')
            print('ELAPSED TIME (mins)')
            print('-------')
            print(round((time.time() - start)/60, 1))

            with open('last_table.json', 'w') as outfile:
                json.dump(table, outfile)

        balancer(status)
        data = trade_data(table, status)

        d.append(data)
        if i > 30:
            d.pop(0)
        df = pd.DataFrame(data=d)

        rows = -30
        # clear the list
        ax.clear()

        e_color = 0
        for e in EXCHANGES:
            ax.plot(df.index[rows:], df[e+'_ask'][rows:], label=e+'_ask', color=COLORS[e_color])
            ax.plot(df.index[rows:], df[e+'_bid'][rows:], label=e+'_bid', linestyle='dashed', color=COLORS[e_color])
            e_color += 1
        ax.legend()

        fig.canvas.draw()
        fig.canvas.flush_events()
        time.sleep(1.5)

main()
