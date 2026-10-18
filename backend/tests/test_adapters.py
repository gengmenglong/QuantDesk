from datetime import datetime, timezone
import httpx
import pytest


def test_coinbase_uses_source_timestamp_and_depth():
    from quantdesk.providers import Coinbase
    def transport(request):
        if request.url.path.endswith('/book'):
            return httpx.Response(200, json={'time':'2026-10-16T10:00:00Z','bids':[['99','2',1]],'asks':[['101','3',2]], 'sequence':5})
        return httpx.Response(200, json={'id':'BTC-USD','status':'online','trading_disabled':False})
    q = Coinbase(httpx.Client(transport=httpx.MockTransport(transport))).fetch('BTC-USD')
    assert q.price == 100
    assert q.ask_size == 3  # order count is not a quantity multiplier
    assert q.event_time == datetime(2026,10,16,10,tzinfo=timezone.utc)


def test_provider_error_never_returns_demo():
    from quantdesk.providers import Coinbase, ProviderError
    with pytest.raises(ProviderError):
        Coinbase(httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403)))).fetch('BTC-USD')


def test_polymarket_sorts_book_and_does_not_assume_resolution():
    from quantdesk.providers import Polymarket
    def transport(request):
        if request.url.host == 'gamma-api.polymarket.com':
            return httpx.Response(200, json=[{'conditionId':'condition','closed':False,'active':True,
                'acceptingOrders':True,'enableOrderBook':True,'outcomes':'["Yes","No"]','clobTokenIds':'["yes-token","no-token"]'}])
        return httpx.Response(200, json={'timestamp':'1792144800000','bids':[{'price':'0.3','size':'2'},{'price':'0.4','size':'3'}],
                                       'asks':[{'price':'0.7','size':'5'},{'price':'0.6','size':'4'}]})
    provider = Polymarket(httpx.Client(transport=httpx.MockTransport(transport)))
    q = provider.fetch('yes-token')
    assert str(q.bid) == '0.4'
    assert q.ask_size == 4
    assert q.outcome == 'Yes'
    assert provider.resolution('yes-token') is None


def test_tushare_permission_error_is_explicit():
    from quantdesk.providers import Tushare, ProviderError
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'code':2002,'msg':'no permission','data':None})))
    with pytest.raises(ProviderError, match='permission'):
        Tushare(client, {'tushare_token':'x'}).fetch('600519.SH')
