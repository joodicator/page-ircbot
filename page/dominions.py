from itertools import *
import traceback
import urllib2
import json
import time
import re

from bs4 import BeautifulSoup

from untwisted.magic import sign
import channel
import runtime
import message
import util
import auth

STATE_FILE = 'state/dominions.json'
UPDATE_PERIOD_S = 15
TICK_PERIOD_S = 15

#-------------------------------------------------------------------------------
link = util.LinkSet()
is_installed = False

def install(bot):
    global is_installed   
    link.install(bot)
    is_installed = True
    bot.drive('DOMINIONS_TICK', bot)

def uninstall(bot):
    global is_installed
    link.uninstall(bot)
    is_installed = False

install, uninstall = util.depend(install, uninstall,
    'auth', 'identity', 'channel')

#-------------------------------------------------------------------------------
class State(object):
    def __init__(self, path=None, jdict=None):
        self.channels = dict()
        self.games = dict()
        if path is not None: self.load_path(path)
        if jdict is not None: self.load_jdict(jdict)

    def load_path(self, path):
        try:
            with open(path) as file:
                jdict = json.load(file)
        except:
            jdict = dict()
            traceback.print_exc()
        self.load_jdict(jdict)

    def save_path(self, path):
        with open(path, 'w') as file:
            json.dump(self.save_jdict(), file)

    def load_jdict(self, jdict):
        all_urls = set()
        for chan, urls in jdict.get('channels', dict()).iteritems():
            chan = chan.lower()
            if chan not in self.channels:
                self.channels[chan] = []
            self.channels[chan].extend(urls)
            all_urls.update(urls)
        for url, report in jdict.get('games', dict()).iteritems():
            if url not in all_urls: continue
            if report.get('type') == 'report':
                self.games[url] = Report(jdict=report)
            elif report.get('type') == 'error':
                self.games[url] = ErrorReport(url=url, jdict=report)

    def save_jdict(self):
        jdict = dict(self.__dict__)
        jdict['games'] = {u:r.save_jdict() for (u,r) in self.games.iteritems()}
        return jdict

class Core(object):
    def __eq__(self, other):
        return type(self) is type(other) \
           and self.core() == other.core()
    def __ne__(self, other):
        return type(self) is not type(other) \
            or self.core() != other.core()
    def __hash__(self, other):
        return hash(self.core())

class Report(Core):
    def __init__(self, jdict=None, url=None, soup=None):
        self.time = time.time()
        self.name = None
        self.turn = None
        self.players = set()
        if jdict is not None: self.load_jdict(jdict)
        if url is not None: self.load_url(url)
        if soup is not None: self.load_soup(soup)

    def load_jdict(self, jdict):
        self.time = jdict.get('time', self.time)
        self.name = jdict.get('name', self.name)
        self.turn = jdict.get('turn', self.turn)
        self.players.update(Player(jdict=d) for d in jdict.get('players', []))

    def save_jdict(self):
        jdict = dict(self.__dict__)
        jdict['players'] = [p.save_jdict() for p in self.players]
        jdict['type'] = 'report'
        return jdict

    def load_url(self, url):
        try:
            stream = urllib2.urlopen(url)
            encoding = stream.info().getparam('charset')
            soup = BeautifulSoup(stream, 'html5lib', from_encoding=encoding)
            self.load_soup(soup)
        except urllib2.URLError:
            raise UnreadableURL('Unable to load <%s>.' % url)
        except ValueError:
            raise UnreadableURL('Unable to load <%s>.' % url)
        except UnreadableSoup:
            raise UnreadableURL('Unable to read status page at <%s>.' % url)

    def load_soup(self, soup):
        rows = soup.find_all(name='tr')
        if len(rows) < 1: raise UnreadableSoup(
            'No <tr> elements found in document.')
        title = rows[0].text.strip()
        match = re.match(r'(?P<name>.*), turn (?P<turn>\d+)', title)
        if match is None: raise UnreadableSoup(
            'Cannot parse title: %r' % title)
        self.name = match.group('name')
        self.turn = int(match.group('turn'))
        for index in range(1, len(rows)):
            self.players.add(Player(index=index, soup=rows[index]))

    def show_irc(self, format=True):
        show_players = [p for p in self.players if p.status != 'AI']
        show_players = sorted(show_players, key=lambda p: p.index)
        return '%s, turn %s [%s]' % (
            ('\2%s\2' if format else '%s') % self.name,
            ('\2%d\2' if format else '%d') % self.turn,
            ', '.join(p.show_irc(format=format) for p in show_players))

    def show_topic(self, format=False):
        return self.show_irc(format=format)

    def core(self):
        return (self.name, self.turn)

class ErrorReport(Core):
    def __init__(self, url=None, prev=None, exc=None, tstamp=None, jdict=None):
        self.time = tstamp if tstamp is not None else time.time()
        self.url = url
        self.name = prev.name if prev is not None else None
        if jdict is not None: self.load_jdict(jdict)
    def load_jdict(self, jdict):
        self.time = jdict.get('time', self.time)
        self.name = jdict.get('name', self.name)
    def save_jdict(self):
        jdict = dict(self.__dict__)
        del jdict['url']
        jdict['type'] = 'error'
        return jdict
    def show_irc(self, format=True):
        return ('%s: unable to retrieve status.' % (
            ('\2%s\2' if format else '%s') % self.name
            if self.name is not None else '<%s>' % self.url))
    def show_topic(self, format=False):
        return '%s, turn ? [unable to retrieve status]' % (
            self.name if self.name is not None else '?')
    def core(self):
        return ()

class Player(object):
    def __init__(self, index=None, soup=None, jdict=None):
        self.index = index
        self.name = None
        self.css = None
        self.status = None
        if soup is not None: self.load_soup(soup)
        if jdict is not None: self.load_jdict(jdict)
    def load_soup(self, soup):
        cells = soup.select('td')
        if len(cells) < 2: raise UnreadableSoup(
            'Cannot parse body row: %s' % row)
        self.name = cells[0].text
        self.status = cells[1].text
        self.css = ' '.join(sorted(cells[0].get('class')))
    def load_jdict(self, jdict):
        self.index = jdict.get('index', self.index)
        self.name = jdict.get('name', self.name)
        self.css = jdict.get('css', self.css)
        self.status = jdict.get('status', self.status)
    def save_jdict(self):
        return self.__dict__

    def show_irc(self, format=True):
        return '%s: %s' % (
            self.name.split(',', 1)[0],
            'played' if self.status == 'Turn played' else
            self.status.lower())

    def id(self):
        return ('Player', self.name, self.status)
    def __cmp__(self, other):
        if type(other) is not Player: return False
        return cmp(self.id(), other.id())
    def __hash__(self):
        return hash(self.id())

class UnreadableURL(Exception):
    pass

class UnreadableSoup(Exception):
    pass

state = State(path=STATE_FILE)

#-------------------------------------------------------------------------------
def get_report(url):
    try:
        return Report(url=url)
    except UnreadableURL as exc:
        return ErrorReport(url=url, prev=state.games.get(url), exc=exc)

@util.msub(link, 'dominions.update_urls')
def update_urls(bot, urls, report_to):
    for url in urls:
        prev_report = state.games.get(url)
        report = state.games[url] = get_report(url)
        for chan, chan_urls in state.channels.iteritems():
            if url not in chan_urls: continue
            yield update_topic(bot, chan)
            if report_to is not None and chan == report_to.lower():
                bot.send_msg(chan, report.show_irc())
            elif (type(report) is Report and type(prev_report) is Report
            and report.turn > prev_report.turn):
                bot.send_msg(chan, '\2%s\2 has advanced to turn \2%d\2.'
                % (report.name, report.turn))
            elif type(report) is ErrorReport and report != prev_report:
                bot.send_msg(chan, report.show_irc())
    if urls: state.save_path(STATE_FILE)

@util.msub(link, 'dominions.update_topic')
def update_topic(bot, chan):
    new_dyn = '; '.join(
        state.games[url].show_topic()
        for url in state.channels.get(chan.lower(), []) if url in state.games)

    if new_dyn:
        topic = yield channel.topic(bot, chan)
        if new_dyn in topic: return
    
        match = re.search(
            r'(^|-- )(?P<dyn>(.+, turn (\d+|\?) \[[^\]]*\](; )?)+)( --|$)', topic)
        if match:
            start, end = match.span('dyn')
            topic = ''.join((topic[:start], new_dyn, topic[end:]))
        else:
            topic = ' -- '.join((new_dyn, re.sub(r'^\s*(--)?', '', topic)))
        bot.send_cmd('TOPIC %s :%s' % (chan, topic))
    
@link('DOMINIONS_TICK')
def h_dominions_tick(bot):
    if not is_installed: return
    urls = []
    latest = time.time() + UPDATE_PERIOD_S
    for chan, chan_urls in state.channels.iteritems():
        for url in chan_urls:
            if url in urls:
                continue
            if url in state.games and state.games[url].time > latest:
                continue
            urls.append(url)
    yield update_urls(bot, urls, None)
    yield runtime.sleep(TICK_PERIOD_S)
    yield sign('DOMINIONS_TICK', bot)

@link('!turn')
def h_turn(bot, id, chan, args, full_msg):
    if chan is None: return
    for url in state.channels.get(chan.lower(), []):
        yield update_urls(bot, [url], chan)

@link('!dom+')
@auth.admin
def h_dom_add(bot, id, chan, add_spec, full_msg):
    if chan is None: return
    chan = chan.lower()
    aurls = re.findall(r'\S+', add_spec.lower())
    for aurl in aurls:
        if aurl in state.channels.get(chan, []):
            message.reply(bot, id, chan,
                'Error: "%s" is already monitored here.' % aurl)
            break
    else:
        if chan not in state.channels:
            state.channels[chan] = []
        for aurl in aurls:
            if aurl not in state.channels[chan]:
                state.channels[chan].append(aurl)
    
        try:
            state.save_path(STATE_FILE)
        except Exception as e:
            message.reply(bot, id, chan, 'Error: %s' % str(e))
            raise
    
        message.reply(bot, id, chan, '%d game(s) added.' % len(aurls))
        yield update_urls(bot, aurls, None)

@link('!dom-')
@auth.admin
def h_dom_del(bot, id, chan, del_spec, full_msg):
    if chan is None: return
    chan = chan.lower()
    del_spec = re.findall(r'\S+', del_spec.lower())
    if not del_spec: return

    del_urls = []
    for spec in del_spec:
        for i, iurl in izip(count(), state.channels.get(chan, [])):
            if spec == iurl: break
            if spec == str(i+1): break
            if iurl in state.games and spec == state.games[iurl].name: break
        else:
            return message.reply(bot, id, chan,
                'Error: no game matching "%s" is monitored here.' % spec)
        del_urls.append(iurl)
    
    del_count = 0
    for durl in del_urls:
        if durl in state.channels[chan]:
            state.channels[chan].remove(durl)
            del_count += 1
        for ochan, ocurls in state.channels.iteritems():
            if durl in ocurls: break
        else:
            if durl in state.games: del state.games[durl]

    try:
        state.save_path(STATE_FILE)
    except Exception as e:
        message.reply(bot, id, chan, 'Error: %s' % str(e))
        raise
    message.reply(bot, id, chan, '%d game(s) removed.' % del_count)

@link('!dom?')
@auth.admin
def h_dom_query(bot, id, chan, args, full_msg):
    if chan is None: return
    urls = state.channels.get(chan.lower(), [])
    if urls:
        for index, url in izip(count(), urls):
            name = getattr(state.games[url], 'name', None) \
                   if url in state.games else None
            message.reply(bot, id, chan, '%d. %s%s%s' % (
                index + 1,
                url,
                ' (%s)' % name if name is not None else '',
                ',' if index < len(urls)-1 else '.'), prefix=False)
    else:
        message.reply(bot, id, chan, 'None.', prefix=False)