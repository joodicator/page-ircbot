from importlib import import_module
from socket import *
import time
import sys
import re

from plugins.standard import head
from untwisted.core import gear
from untwisted.network import Mac
from untwisted.event import CLOSE, TICK
from untwisted.usual import Kill
import utils.misc
import stdlog as std
import xirclib

RPL_WELCOME         = '001'
RPL_ISUPPORT        = '005'
ERR_NICKNAMEINUSE   = '433'

class NotInstalled(Exception): pass
class AlreadyInstalled(Exception): pass

default_conf = {
    'server':        'irc.freenode.net',
    'port':          6667,
    'nick':          'ameliabot',
    'user':          'ameliabot',
    'name':          'ameliabot',
    'host':          '0',
    'channels':      ['#untwisted'],
    'plugins':       [],
    'timeout':       180, # 180s = 3m
    'bang_cmd':      True,
    'flood_seconds': 9,
    'flood_lines':   9
}

class AmeliaBot(Mac):
    def __init__(self, conf=None):
        # Load configuration
        self.conf = default_conf.copy()
        if conf: self.conf.update(conf)

        # Initialise socket
        sock = socket(AF_INET, SOCK_STREAM)
        Mac.__init__(self, sock, is_read=True, is_write=True)
        if 'bind_addr' in self.conf: sock.bind(self.conf['bind_addr'])
        address = gethostbyname(self.conf['server'])
        sock.setblocking(0)
        sock.connect_ex((address, self.conf['port']))

        # Initialise miscellaneous attributes
        self.isupport = {
            'PREFIX':    ('ohv','@%+'),
            'CHANMODES': ('be','k','l','') }

        # Initialise flood-protection system
        self.send_times = []
        self.flood_buffer = []
        self.flood_active = False

        # Initialise events
        std.install(self)
        xirclib.install(self)
        self.link(ERR_NICKNAMEINUSE,    self.h_err_nicknameinuse)
        self.link(RPL_WELCOME,          self.h_rpl_welcome)
        self.link(RPL_ISUPPORT,         self.h_rpl_isupport)
        self.link(TICK,                 self.h_tick)
        
        # Load plugins
        self.conf['plugins'][:0] = ['plugins.standard.head']
        self.load_plugins()

        # Start registration
        self.nick = self.conf['nick']
        self.send_cmd('NICK %s' % self.nick)
        self.send_cmd('USER %(user)s %(host)s %(server)s :%(name)s' % self.conf) 

    def load_plugins(self):
        loaded_plugins = []
        for name in self.conf['plugins']:
            plugin = import_module(name)
            loaded_plugins.append(plugin)

        for plugin in loaded_plugins:
            try:
                plugin.install(self)
            except AlreadyInstalled:
                pass

    def h_err_nicknameinuse(self, bot, *args):
        self.nick += "_"
        self.send_cmd('NICK %s' % self.nick)

    def h_rpl_isupport(self, bot, pre, target, *args):
        for arg in args[:-1]:
            match = re.match(r'-?(?P<key>[^=]+)(=(?P<val>.*))?', arg)
            key, val = match.group('key', 'val')
            if key == 'PREFIX' and val:
                match = re.match(r'(\((?P<ms>[^)]*)\))?(?P<ps>.*)', val)
                val = match.group('ms', 'ps')
            elif key == 'CHANMODES' and val:
                val = tuple(val.split(','))
            bot.isupport[key] = val

    def h_rpl_welcome(self, *args):
        for channel in self.conf['channels']:
            self.send_cmd('JOIN %s' % channel)
        self.drive('AUTOJOIN', self)

    def mainloop(self):
        return gear.mainloop()

    def send_msg(self, target, msg, **kwds):
        self.send_line('PRIVMSG %s :%s' % (target, msg))
        self.drive('SEND_MSG', self, target, msg, kwds)
        self.activity = True
    
    def send_cmd(self, cmd):
        self.send_line(cmd)
        self.activity = True

    def send_line(self, line):
        now = time.time()
        cut = now - self.conf['flood_seconds']
        while self.send_times and self.send_times[0] < cut:
            del self.send_times[0]

        if len(self.send_times) > self.conf['flood_lines']:
            self.flood_active = True

        if self.flood_active:
            self.flood_buffer.append(line)
        else:
            self.send_times.append(now)
            self.dump('%s\r\n' % line[:510])

    def h_tick(self, bot):
        if not self.flood_active:
            return
        lines = self.flood_buffer
        self.flood_buffer = []
        self.flood_active = False
        for line in lines:
            self.send_line(line)

if __name__ == '__main__':
    gear = AmeliaBot()
    gear.mainloop()

