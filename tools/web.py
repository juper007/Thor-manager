import html
import ipaddress
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None


def public_url(url):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname: raise ValueError('only public http(s) URLs are allowed')
    for item in socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM):
        ip=ipaddress.ip_address(item[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError('private or reserved network targets are blocked')
    return url


def request(url,limit=800_000):
    opener=urllib.request.build_opener(_NoRedirect); current=url
    for _ in range(6):
        current=public_url(current)
        req=urllib.request.Request(current,headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/json,text/plain'})
        try: response=opener.open(req,timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301,302,303,307,308): raise
            location=exc.headers.get('Location')
            if not location: raise ValueError('redirect response has no Location header')
            current=urllib.parse.urljoin(current,location); continue
        with response:
            return response.headers.get_content_type(),response.read(limit+1)[:limit]
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
