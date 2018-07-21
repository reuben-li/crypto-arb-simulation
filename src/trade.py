%matplotlib tk
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
from multiprocessing import Process

BTCJPY = 808865
JPY_MIN = 1000
BTC_MIN = 0.001
BF_FEES = 0.0016
EXCHANGES = ['bb', 'zf']
MARGIN = {'low':0.00001, 'high' :0.0003}
COLORS = ['blue', 'green', 'red', 'orange']

## import credentials
with open("config.yml", 'r') as ymlfile:
    cfg = yaml.load(ymlfile)

auth = cfg['auth']

# deposit fees
# zf = 486 jpy
# bb = free
# gm = free

# withdrawal transfer fees
# zf = 0.0005 BTC / 350 jpy
# bb = 0.001 BTC / 540 jpy
# gm = free

## instantiate clients
# qn_client = Quoinex(auth['qn']['key'], auth['qn']['secret'])
bf_client = pybitflyer.API(api_key=auth['bf']['key'], api_secret=auth['bf']['secret'])
# binance.set(auth['bn']['key'], auth['bn']['secret'])
bb_client_pte = python_bitbankcc.private(auth['bb']['key'], auth['bb']['secret'])
bb_client = python_bitbankcc.public()
zf_pclient = ZaifTradeApi(auth['zf']['key'], auth['zf']['secret'])
zf_client = ZaifPublicApi()

def bf_trade(direction, size=0.001):
    a = bf_client.sendchildorder(
        product_code="BTC_JPY",
        child_order_type="MARKET",
        side=direction,
        size=size
    )
    print(a)
    
def bf_price():
    res = bf_client.ticker(product_code="BTC_JPY")
    return res['best_ask'], res['best_bid']

def bf_portfolio():
    for i in bf_client.getbalance():
        if i['currency_code'] == 'JPY':
            bf_jpy = i['amount']
        elif i ['currency_code'] == 'BTC':
            bf_btc = i['amount']
    return bf_jpy, bf_btc
    
def qn_trade(direction, size=0.001):
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
        
def bb_trade(direction, size=0.001):
    bb_client_pte.order(
        'btc_jpy',
        '',
        size,
        direction.lower(),
        'market'
    )
    
def bb_price():
    res = bb_client.get_ticker('btc_jpy')
    return float(res['sell']), float(res['buy'])

def bb_portfolio():
    assets = bb_client_pte.get_asset()['assets']
    for a in assets:
        if a['asset'] == 'jpy':
            bb_jpy = float(a['onhand_amount'])
        elif a['asset'] == 'btc':
            bb_btc = float(a['onhand_amount'])
    return bb_jpy, bb_btc

def zf_trade(direction, size=0.001):
    ask, bid = zf_price()
    if direction == 'BUY':
        action = 'bid'
        price = ask
    else:
        action = 'ask'
        price = bid
    res = zf_pclient.trade(
        currency_pair='btc_jpy',
        action=action,
        amount=size,
        price=int(price)
    )
    return res

def zf_price():
    res = zf_client.ticker('btc_jpy')
    return res['ask'], res['bid']

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
    
def trade_data(status):
    action = ''
    data = {}
    min_ask = 2000000
    max_bid = 1
    
    # best ask and bid exchanges
    ask_e = ''
    bid_e = ''
    
    # dynamic margins
    margin = MARGIN['high']
    if min(table['jpy'].values())/max(table['jpy'].values()) < 0.3 or min(table['btc'].values) < 0.004:
        margin = MARGIN['low']
    
    for e in EXCHANGES:
        ask, bid = globals()[e + '_price']()
        
        if (max_bid - ask) / max_bid > margin:
            if status[e]['buy'] and status[bid_e]['sell']:
                globals()[e + '_trade']('BUY')
                globals()[bid_e + '_trade']('SELL')
                print('opp! buy:' + e + ' sell: '+ bid_e + ' margin: '  + str(margin))
                action = 'traded'
            else:
                action = 'no funds'
        elif (bid - min_ask) / bid > margin:
            if status[ask_e]['buy'] and status[e]['sell']:
                globals()[ask_e + '_trade']('BUY')
                globals()[e + '_trade']('SELL')
                print('opp! buy:' + ask_e + ' sell:'+ e + ' margin: '  + str(margin))
                action = 'traded'
            else:
                action = 'no funds'
        else:
            action = 'no opportunity'            
        if ask < min_ask:
            min_ask = ask
            ask_e = e
        if bid > max_bid:
            max_bid = bid
            bid_e = e

        data[e + '_ask'] = ask
        data[e + '_bid'] = bid

    return data, action

d = []

fig = plt.figure()
ax = fig.add_subplot(111)
print('-------------')
print("START!")
print("-------------")

i = 0
last_action = ''
while True:
    i += 1
    table, status = portfolio_value()    
    data, action = trade_data(status)
    
    if action == 'traded' or action != last_action:
        print(action)
        pprint(table)
        pprint(status)
        print("-------------")
    
    last_action = action

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
    time.sleep(2)
