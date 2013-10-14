from __future__ import print_function

import runtime
import control
import util

from untwisted import event
from untwisted.utils import common
from untwisted.utils import std
from untwisted.magic import sign
from untwisted import event

import struct
import sys


__INSTALL_BOT__ = False


link = util.LinkSet()
link.link_module(std)
link.link(event.DATA, common.append)

debug_link = util.LinkSet()


def install(mode, debug=False):
    if hasattr(mode, 'terraria_protocol'): raise control.AlreadyInstalled
    class TerrariaProtocol(object): pass
    mode.terraria_protocol = TerrariaProtocol()
    mode.terraria_protocol.debug = debug
    if debug: debug_link.install(mode)
    link.install(mode)

def uninstall(mode):
    if not hasattr(mode, 'terraria_protocol'): raise control.NotInstalled
    if mode.terraria_protocol.debug: debug_link.uninstall(mode)
    link.uninstall(mode)
    del mode.terraria_protocol


@link(event.BUFFER)
def h_buffer(work, data):
    while len(data) > 4:
        length, type = struct.unpack('<iB', data[:5])
        if len(data) < length + 4: break
        yield sign('MESSAGE', work, type, data[4:][1:length])
        data = data[4:][length:]
    work.stack = data


@link('MESSAGE')
def h_message(work, head, body):
    if head == 0x02:
        yield sign('DISCONNECT', work, body)
    elif head == 0x03:
        slot, = struct.unpack('<B', body[:1])
        yield sign('CONNECTION_APPROVED', work, slot)
    elif head == 0x04:
        slot, = struct.unpack('<B', body[:1])
        name = body[25:]
        yield sign('PLAYER_APPEARANCE', work, slot, name)
    elif head == 0x09:
        count = struct.unpack('<i', body[:4])
        text = body[4:]
        yield sign('STATUSBAR_TEXT', work, count, text)
    elif head == 0x19:
        slot, = struct.unpack('<B', body[:1])
        colour = struct.unpack('<BBB', body[1:4])
        text = body[4:]
        yield sign('CHAT', work, slot, colour, text)
    elif head == 0x31:
        yield sign('SPAWN', work)
    elif head == 0x07:
        spawn = struct.unpack('<ii', body[15:23])
        world_name = body[36:]
        yield sign('WORLD_INFORMATION', work, spawn, world_name)
    elif head == 0x25:
        yield sign('REQUEST_PASSWORD', work)
    elif head not in (0x0a, 0x14, 0x17, 0x1a, 0x1b, 0x1c, 0x1d):
        yield sign('UNKNOWN', work, '$%02X' % head, body)


def h_debug(bot, *args):
    print('> %s %s' % (args[-1], args[:-1]), file=sys.stderr)

for h in (
    'DISCONNECT', 'CONNECTION_APPROVED', 'PLAYER_APPEARANCE',
    'CHAT', 'SPAWN', 'WORLD_INFORMATION'
): debug_link.link(h, h_debug, h)

def debug_send(send_f):
    def send_g(work, *args, **kwds):
        if work.poll.terraria_protocol.debug:
            print('< %s %s %s' % (send_f.func_name[5:].upper(), args, kwds),
                file=sys.stderr)
        return send_f(work, *args, **kwds)
    return send_g


def send_message(work, type, body):
    body = struct.pack('<B', type) + body
    body = struct.pack('<i', len(body)) + body
    work.dump(body)

@debug_send
def send_connect_request(work, version):
    send_message(work, 0x01, version)

@debug_send
def send_player_appearance(work, slot, name):
    body = struct.pack('<B', slot) + '\0'*24 + name
    send_message(work, 0x04, body)

@debug_send
def send_set_player_life(work, slot, current, maximum):
    body = struct.pack('<Bii', slot, current, maximum)
    send_message(work, 0x10, body)

@debug_send
def send_set_player_mana(work, slot, current, maximum):
    body = struct.pack('<Bii', slot, current, maximum)
    send_message(work, 0x2A, body)

@debug_send
def send_set_player_buffs(work, slot, buffs):
    body = struct.pack('<10B', *buffs)
    send_message(work, 0x32, body)

@debug_send
def send_set_inventory(work, slot, islot, istack, iprefix, item):
    body = struct.pack('<BBBBh', slot, islot, istack, iprefix, item)
    send_message(work, 0x05, body)

@debug_send
def send_request_world_information(work):
    send_message(work, 0x06, '')

@debug_send
def send_request_initial_tile_data(work, *spawn):
    body = struct.pack('<ii', *spawn)
    send_message(work, 0x08, body)

@debug_send
def send_spawn_player(work, slot, *spawn):
    body = struct.pack('<Bii', slot, *spawn)
    send_message(work, 0x0C, body)

@debug_send
def send_password(work, password):
    send_message(work, 0x26, password)

@debug_send
def send_chat(work, slot, (r,g,b), text):
    body = struct.pack('<B3B', slot, r,g,b) + text
    send_message(work, 0x19, body)


def login(work, name, password='', version='Terraria71'):
    class TerrariaProtocol(object): pass
    work.terraria_protocol = TerrariaProtocol()
    work.terraria_protocol.chat_queue = []
    work.terraria_protocol.players = dict()
    work.terraria_protocol.stage = 0
    work.terraria_protocol.name = name
    work.terraria_protocol.password = password
    work.terraria_protocol.version = version
    send_connect_request(work, version)

def chat(work, text, colour=(255,255,255)):
    if work.terraria_protocol.stage != 3:
        work.terraria_protocol.chat_queue.append(text)
    else:
        send_chat(work, work.terraria_protocol.slot, colour, text)    

def close(work):
    if hasattr(work, 'terraria_protocol'): del work.terraria_protocol


@link('REQUEST_PASSWORD')
def h_request_password(work):
    if not hasattr(work, 'terraria_protocol'): return
    send_password(work, work.terraria_protocol.password)

@link('CONNECTION_APPROVED')
def h_connection_approved(work, slot):
    if not hasattr(work, 'terraria_protocol'): return
    if work.terraria_protocol.stage != 0: return
    work.terraria_protocol.stage = 1
    work.terraria_protocol.slot = slot
    work.terraria_protocol.players[slot] = work.terraria_protocol.name
    send_player_appearance(work, slot, work.terraria_protocol.name)
    send_set_player_life(work, slot, 0, 0)
    send_set_player_mana(work, slot, 0, 0)
    send_set_player_buffs(work, slot, [0 for i in xrange(10)])
    for islot in xrange(60):
        send_set_inventory(work, slot, islot, 0, 0, 0)
    send_request_world_information(work)

@link('WORLD_INFORMATION')
def h_world_information(work, spawn, world_name):
    if not hasattr(work, 'terraria_protocol'): return
    if work.terraria_protocol.stage != 1: return
    work.terraria_protocol.stage = 2
    work.terraria_protocol.spawn = spawn
    work.terraria_protocol.world_name = world_name
    send_request_initial_tile_data(work, -1, -1)

@link('SPAWN')
def h_spawn(work):
    if not hasattr(work, 'terraria_protocol'): return
    if work.terraria_protocol.stage != 2: return
    work.terraria_protocol.stage = 3
    send_spawn_player(work,
        work.terraria_protocol.slot,
        *work.terraria_protocol.spawn)
    for text in work.terraria_protocol.chat_queue:
        chat(work, text)
    work.terraria_protocol.chat_queue = []
    yield sign('HEARTBEAT', work)

@link('HEARTBEAT')
def h_heartbeat(work):
    while hasattr(work, 'terraria_protocol'):
        send_set_player_life(work, work.terraria_protocol.slot, 0, 0)
        yield runtime.sleep(1)

@link('PLAYER_APPEARANCE')
def h_player_appearance(work, slot, name):
    if not hasattr(work, 'terraria_protocol'): return
    work.terraria_protocol.players[slot] = name
