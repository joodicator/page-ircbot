import time
import sys

from untwisted.mode import Mode
from untwisted.event import TICK, CLOSE
from untwisted.magic import sign
from untwisted.core import gear

import util
import debug


EXIT_DELAY_SECONDS = 0.1

b_link = util.LinkSet()

mode = Mode()
mode.domain = 'run'
mode.poll = mode
gear.tick_list.append(mode)
m_link = util.LinkSet()
if '--debug' in sys.argv: m_link.link_module(debug)

sleepers = list()


def install(bot):
    b_link.install(bot)
    m_link.install(mode)

def uninstall(bot):
    m_link.uninstall(mode)
    b_link.uninstall(bot)

def reinstall(prev):
    global mode
    if hasattr(prev, 'mode') and prev.mode:
        mode = prev.mode


def sleep(delta):
    return util.mmcall(mode, 'runtime.sleep', time.time() + delta)

@m_link('runtime.sleep')
def h_sleep(until):
    sleepers.append(until)
    sleepers.sort()

@m_link(TICK)
def h_tick(mode):
    while len(sleepers):
        if sleepers[0] > time.time(): break
        yield sign(('runtime.sleep', sleepers.pop(0)), None)

@b_link(CLOSE)
def h_close(bot):
    print '! disconnected'
    yield sign('CLOSING', bot)
    bot.destroy()
    yield sleep(EXIT_DELAY_SECONDS)
    sys.exit(0)

@b_link('EXCEPTION')
def h_exception(bot, e):
    print '! uncaught exception'
    yield sign('CLOSING', bot)
    bot.destroy()
    yield sleep(EXIT_DELAY_SECONDS)
    raise e