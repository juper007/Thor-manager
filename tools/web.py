import html
import http.client
import ipaddress
import re
import socket
import time
import urllib.parse
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


USER_AGENT='Mozilla/5.0 (compatible; ThorMonitorAgent/1.0)'


class TextParser(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','svg','noscript'): self.skip+=1
        if not self.skip and tag in ('p','h1','h2','h3','li','br','article','section'): self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','svg','noscript') and self.skip: self.skip-=1
    def handle_data(self,data):
        if not self.skip: self.parts.append(data)


def _public_target(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname: raise ValueError('only public http(s) URLs are allowed')
    addresses=[]
    for item in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM):
        ip=ipaddress.ip_address(item[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError('private or reserved network targets are blocked')
        if str(ip) not in addresses: addresses.append(str(ip))
    if not addresses: raise ValueError('URL hostname did not resolve')
    return parsed,addresses


def public_url(url):
    _public_target(url)
    return url


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self,host,address,port,timeout):
        super().__init__(host,port=port,timeout=timeout); self.address=address
    def connect(self):
        self.sock=socket.create_connection((self.address,self.port),self.timeout,self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self,host,address,port,timeout):
        super().__init__(host,port=port,timeout=timeout); self.address=address
    def connect(self):
        self.sock=socket.create_connection((self.address,self.port),self.timeout,self.source_address)
        self.sock=self._context.wrap_socket(self.sock,server_hostname=self.host)


def _open_pinned(parsed,addresses,timeout=20):
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    path=urllib.parse.urlunsplit(('', '', parsed.path or '/',parsed.query,''))
    host=parsed.hostname if parsed.port is None else f'{parsed.hostname}:{parsed.port}'
    last_error=None
    for address in addresses:
        connection=(_PinnedHTTPSConnection if parsed.scheme=='https' else _PinnedHTTPConnection)(parsed.hostname,address,port,timeout)
        try:
            connection.request('GET',path,headers={'Host':host,'User-Agent':USER_AGENT,'Accept':'text/html,application/json,text/plain','Connection':'close'})
            return connection.getresponse()
        except OSError as exc:
            last_error=exc; connection.close()
    raise last_error or OSError('unable to connect to validated target')


def request(url,limit=800_000,timeout=20):
    current=url; deadline=time.monotonic()+timeout
    for _ in range(6):
        remaining=deadline-time.monotonic()
        if remaining<=0: raise TimeoutError(f'web request timed out after {timeout} seconds')
        parsed,addresses=_public_target(current)
        response=_open_pinned(parsed,addresses,timeout=remaining)
        try:
            if response.status in (301,302,303,307,308):
                location=response.headers.get('Location')
                if not location: raise ValueError('redirect response has no Location header')
                current=urllib.parse.urljoin(current,location); continue
            if response.status>=400: raise ValueError(f'web request failed with HTTP {response.status}')
            content_type=response.headers.get_content_type()
            return content_type,response.read(limit+1)[:limit]
        finally:
            response.close()
    raise ValueError('too many redirects')


def web_search(args):
    query=str(args.get('query','')).strip()[:300]; count=max(1,min(8,int(args.get('max_results',5))))
    if not query: raise ValueError('query is required')
    url='https://www.bing.com/search?'+urllib.parse.urlencode({'format':'rss','q':query})
    _,raw=request(url); root=ET.fromstring(raw); results=[]
    for item in root.findall('./channel/item')[:count]:
        title=item.findtext('title',''); link=item.findtext('link',''); snippet=item.findtext('description','')
        snippet=re.sub(r'<[^>]+>',' ',html.unescape(snippet))
        results.append({'title':' '.join(title.split()),'url':link,'snippet':' '.join(snippet.split())})
    return {'query':query,'results':results}


def read_webpage(args):
    url=public_url(str(args.get('url','')).strip()); content_type,raw=request(url)
    text=raw.decode('utf-8','replace')
    if content_type=='text/html':
        parser=TextParser(); parser.feed(text); text=' '.join(''.join(parser.parts).split())
    return {'url':url,'content':text[:12000],'truncated':len(text)>12000}
