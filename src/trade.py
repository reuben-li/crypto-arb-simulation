%matplotlib tk
import requests
import time
import hmac
import hashlib
import binance
import pandas as pd
from pprint import pprint
import matplotlib.pyplot as plt
from quoine.client import Quoinex
import json
import pybitflyer

# insert auth credentials
auth = {}

qn_client = Quoinex(auth['qn']['key'], auth['qn']['secret'])
bf_client = pybitflyer.API(api_key=auth['bf']['key'], api_secret=auth['bf']['secret'])
binance.set(auth['bn']['key'], auth['bn']['secret'])

def bf_trade(direction, size=0.0001):
    a = bf_client.sendchildorder(
        product_code="BTC_JPY",
        child_order_type="MARKET",
        side=direction,
        size=size
    )
    print(a)
    
def qn_trade(direction, size=0.0001):
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

def portfolio_value(data, funds):
    for i in qclient.get_account_balances():
        if i['currency'] == 'JPY':
            qn_jpy = float(i['balance'])
        elif i ['currency'] == 'BTC':
            qn_btc = float(i['balance'])
    for i in bf_client.getbalance():
        if i['currency_code'] == 'JPY':
            bf_jpy = i['amount']
        elif i ['currency_code'] == 'BTC':
            bf_btc = i['amount']
    print('-----------------')
    print('qn_jpy:', qn_jpy, 'bf_jpy:', bf_jpy, 'total:', qn_jpy+bf_jpy)
    print('qn_btc:', qn_btc, 'bf_btc:', bf_btc, 'total:', qn_btc+bf_btc)
    print('-----------------')
    
bitflyer_fees = 0.000
    
def trade_decision(data, funds):
    # logic to purchase only if both sides have sufficient funding
    if data['bitflyer_bid'] * (1 - bitflyer_fees) > data['quoinex_ask']:
        print('buy @quoinex, sell @bitflyer')
        #qn_trade('BUY')
        #bf_trade('SELL')
        portfolio_value(data, funds)
    elif data['bitflyer_ask'] * (1 + bitflyer_fees) < data['quoinex_bid']:
        print('buy @bitflyer, sell @quoinex')
        #qn_trade('SELL')
        #bf_trade('BUY')
        portfolio_value(data, funds)
    else:
        pass
        
d = []

fig = plt.figure()
ax = fig.add_subplot(111)
print("start!")

for i in range(100):
    js_bf = bf_client.ticker(product_code="BTC_JPY")
    js_qn = qn_client.get_product(product_id=5)
    # timestamp = str(round(time.time()))
    data = {
        #'time': timestamp,
        'bitflyer_ask': js_bf['best_ask'],
        'bitflyer_bid': js_bf['best_bid'],
        'quoinex_ask': float(js_qn['market_ask']),
        'quoinex_bid': float(js_qn['market_bid'])
    }
    trade_decision(data, funds)

    d.append(data)
    if i > 30:
        d.pop(0)
    df = pd.DataFrame(data=d)
    
    rows = -30
    # clear the list
    ax.clear()
    ax.plot(df.index[rows:], df['bitflyer_ask'][rows:], label='bf_ask')
    ax.plot(df.index[rows:], df['bitflyer_bid'][rows:], label='bf_bid')
    ax.plot(df.index[rows:], df['quoinex_ask'][rows:], label='qn_ask')
    ax.plot(df.index[rows:], df['quoinex_bid'][rows:], label='qn_bid')
    ax.legend()
    
    fig.canvas.draw()
    fig.canvas.flush_events()
    time.sleep(1.5)
