# -*- mode: python; coding: utf-8 -*-
"""Daemon for updating `PriceSeries`
"""

import os
import datetime
import pytz
from dateutil.parser import parse as parse_dt
import sqlite3
import json
import atexit
import urllib
import requests


class PriceSeries(object):

    query_template = dict(
        period_id='6HRS',
        time_start='',
        time_end='',
        limit=100000,
    )
    date_format_template = '%Y-%m-%dT%H:%M:%S.%f0Z'  # magic
    headers = {'X-CoinAPI-Key':
               open('API_KEY').read().strip()}
    schema = [
        ('time_period_start', 'TEXT'),
        ('time_period_end', 'TEXT'),
        ('time_open', 'TEXT'),
        ('time_close', 'TEXT'),
        ('price_open', 'REAL'),
        ('price_high', 'REAL'),
        ('price_low', 'REAL'),
        ('price_close', 'REAL'),
        ('volume_traded', 'REAL'),
        ('trades_count', 'INTEGER'),
    ]

    # these are THE values we care about, so CAPS
    TIME = 'time_period_end'
    PRICE = 'price_close'

    # this is the beginning of time if we don't have any local data
    first_date = '2014-01-01T00:00:00.0000000Z'

    column_names = [n for n, _ in schema]
    row_template = 'INSERT INTO {{symbol_id}}({column_names}) values ({q_marks});'.\
        format(
            column_names=', '.join(column_names),
            q_marks=', '.join(['?']*len(schema))
        )
    _sqlite_db = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'db.sqlite3')

    def __init__(self, symbol_id):
        self.symbol_id = symbol_id
        self._connection = None
        self._create_store()
        self.row_template = self.row_template.format(symbol_id=self.symbol_id)

    def get_url(self, query_data):
        url_1 = ('https', 'rest.coinapi.io/v1', 'ohlcv/{}/history'.format(self.symbol_id))
        query = []
        for key, value in query_data.items():
            if not value:
                continue
            if isinstance(value, datetime.datetime):
                self.validate_datetime_object(value)
                value = self.get_normalized_datetime(value)
            query.append('{}={}'.format(key, value))
        query = '&'.join(query)
        url_2 = ('', query, '')
        url = urllib.parse.urlunparse(url_1 + url_2)
        return url

    def _create_store(self):
        create_table = ['CREATE TABLE IF NOT EXISTS {symbol_id} (']
        #fields = ['id integer PRIMARY KEY']
        fields = []
        for name, type_ in self.schema:
            fields.append('{} {}'.format(name, type_))
        fields = ',\n'.join(fields)
        create_table.append(fields)
        create_table.append(');')
        create_table = '\n'.join(create_table)
        cursor = self.connection.cursor()
        cursor.execute(create_table.format(
            symbol_id=self.symbol_id
        ))
        self.connection.commit()

    @property
    def connection(self):
        if self._connection is None:
            self._connection = sqlite3.connect(self._sqlite_db)

            def _cleanup():
                self._connection.commit()
                self._connection.close()
            atexit.register(_cleanup)
        return self._connection

    def get_normalized_datetime(self, dt):
        if not isinstance(dt, datetime.datetime):
            dt = parse_dt(dt)
        return dt.strftime(self.date_format_template)

    def validate_datetime_object(self, dt):
        assert dt.tzname() == 'UTC', 'tzname==`{}`. Expected `UTC`'.format(dt.tzname())
        assert dt.hour in (0, 6, 12, 18)
        assert not dt.hour % 6, 'hour==`{}` not a multiple of `6`'.format(dt.hour)
        for attr in 'minute', 'second', 'microsecond':
            value = getattr(dt, attr)
            assert value == 0, 'datetime attribute `{}`==`{}`. Expected `0`'.format(attr, value)

    def fetch(self):
        last_date = self.get_last_date_from_store()
        if last_date is None:
            last_date = parse_dt(self.first_date)
        self.validate_datetime_object(last_date)

        now = datetime.datetime.now(tz=pytz.UTC)
        if now - last_date < datetime.timedelta(hours=6):
            return {}

        first_fetch_date = last_date + datetime.timedelta(hours=6)
        query_data = dict(self.query_template)
        query_data['time_start'] = first_fetch_date
        url = self.get_url(query_data)
        response = requests.get(url, headers=self.headers)
        return response.json()

    def get_last_date_from_store(self):
        sql = 'SELECT {the_columns} FROM {symbol_id} ORDER BY _ROWID_ DESC LIMIT 1;'.format(
            the_columns=', '.join((self.TIME, self.PRICE)),
            symbol_id=self.symbol_id,
        )
        cursor = self.connection.cursor()
        cursor.execute(sql)
        results = cursor.fetchall()
        if len(results) == 0:
            return None
        last_row, = results
        last_date = parse_dt(last_row[0])
        return last_date

    def insert(self, data):
        insert_rows = []
        for row in data:
            values = [None] * len(self.schema)
            for key, value in row.items():
                index = self.column_names.index(key)
                values[index] = value
            assert all(map(lambda x: x is not None, values)), 'Tied to insert None: {}'.format(values)
            insert_rows.append(values)
        if insert_rows:
            try:
                cursor = self.connection.cursor()
                cursor.executemany(self.row_template, insert_rows)
            finally:
                self.connection.commit()

    def update(self):
        data = self.fetch()
        self.insert(data)


if __name__ == '__main__':

    ps = PriceSeries('BITSTAMP_SPOT_BTC_USD')
    ps.update()
