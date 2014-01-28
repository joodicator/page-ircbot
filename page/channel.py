from collections import defaultdict
import re

from untwisted.magic import sign, hold

import util
link, install, uninstall = util.LinkSet().triple()


ERR_NOTONCHAN   = '442'
ERR_CHOPNEEDED  = '482'
ERR_NOSUCHCHAN  = '403'
RPL_NOTOPIC     = '331'
RPL_TOPIC       = '332'
RPL_CHANMODEIS  = '324'


names_channels = defaultdict(list)
track_channels = defaultdict(list)
invited = set()

def reload(prev):
    try: names_channels.update(prev.names_channels)
    except: pass
    try: track_channels.update(prev.track_channels)
    except: pass
    try: invited.update(prev.invited)
    except: pass

#===============================================================================
def topic(bot, chan):
    return util.mcall('channel.topic', bot, chan)

@link('channel.topic')
def h_topic(bot, chan):
    bot.send_cmd('TOPIC %s' % chan)
    while True:
        (event, data) = yield hold(bot, ERR_NOTONCHAN, RPL_NOTOPIC, RPL_TOPIC)
        if data[3].lower() == chan.lower(): break
    if event == RPL_TOPIC:
        result = data[4]
    elif event == RPL_NOTOPIC:
        result = ''
    else:
        result = None
    yield sign(('channel.topic', bot, chan), result)

#===============================================================================
def mode(bot, chan):
    return util.mcall('channel.mode', bot, chan)

@link('channel.mode')
def h_mode(bot, chan):
    bot.send_cmd('MODE %s' % chan)
    while True:
        (event, data) = yield hold(bot,
            ERR_NOTONCHAN, ERR_NOSUCHCHAN, RPL_CHANMODEIS)
        if data[3].lower() == chan.lower(): break
    if event == RPL_CHANMODEIS:
        result = data[4:]
    else:
        result = None
    yield sign(('channel.mode', bot, chan), result)

#===============================================================================
def strip_names(names):
    return [re.sub(r'^[+%@~^]', '', n) for n in names]

def names(bot, chan):
    return util.mcall('channel.names', bot, chan.lower())

@link('channel.names')
def h_names(bot, chan):
    bot.send_cmd('NAMES %s' % chan)

@link('353')
def h_rpl_namereply(bot, _1, _2, _3, chan, names):
    names = re.findall(r'\S+', names)
    names_channels[chan.lower()] += names

    track_names = track_channels[chan.lower()]
    for name in strip_names(names):
        if name.lower() in map(str.lower, track_names): continue
        track_names.append(name)
    track_channels[chan.lower()] = track_names

@link('366')
def h_rpl_endofnames(bot, _1, _2, chan, *args):
    names = names_channels[chan.lower()]
    yield sign(('channel.names', bot, chan.lower()), names)
    yield sign(('NAMES', chan.lower()), bot, names)
    yield sign('NAMES', bot, chan.lower(), names)
    del names_channels[chan.lower()]


#===============================================================================
@link('SOME_JOIN')
def h_some_join(bot, id, chan):
    chan = chan.lower()
    names = track_channels[chan.lower()]
    if id.nick.lower() in map(str.lower, names): return
    names.append(id.nick)
    track_channels[chan.lower()] = names

@link('SELF_PART')
@link('SELF_KICKED')
@link('SELF_QUIT_CHAN')
def h_self_part_kicked_quit(bot, chan, *args):
    del track_channels[chan.lower()]

@link('OTHER_PART')
def h_other_part(bot, id, chan, *args):
    chan = chan.lower()
    names = track_channels[chan.lower()]
    names = [n for n in names if n.lower() != id.nick.lower()]
    track_channels[chan.lower()] = names

@link('OTHER_KICKED')
def h_other_kicked(bot, nick, op_id, chan, *args):
    chan = chan.lower()
    names = track_channels[chan.lower()]
    names = [n for n in names if n.lower() != nick.lower()]
    track_channels[chan.lower()] = names

@link('SELF_QUIT')
def h_self_quit(bot, msg):
    for chan, names in track_channels.iteritems():
        yield sign('SELF_QUIT_CHAN', bot, chan, msg)
        yield sign(('SELF_QUIT_CHAN', chan), bot, msg)

@link('OTHER_QUIT')
def h_other_quit(bot, id, msg):
    for chan, names in track_channels.iteritems():
        if id.nick.lower() not in map(str.lower, names): continue
        names = [n for n in names if n.lower() != id.nick.lower()]  
        track_channels[chan.lower()] = names
        yield sign('OTHER_QUIT_CHAN', bot, id, chan, msg)
        yield sign(('OTHER_QUIT_CHAN', chan), bot, id, msg)

@link('SOME_NICK')
def h_other_nick(bot, id, new_nick):
    old_nick = id.nick.lower()
    for chan, names in track_channels.iteritems():
        if old_nick not in map(str.lower, names): continue
        names = map(lambda n: new_nick if n.lower() == old_nick else n, names)
        track_channels[chan.lower()] = names
        yield sign('SOME_NICK_CHAN', bot, id, new_nick, chan)
        yield sign(('SOME_NICK_CHAN', chan), bot, id, new_nick)
        if old_nick != bot.nick.lower():
            yield sign('OTHER_NICK_CHAN', bot, id, new_nick, chan)
            yield sign(('OTHER_NICK_CHAN', chan), bot, id, new_nick)

@link('CLOSING')
def h_closing(bot):
    for chan, names in track_channels.iteritems():
        yield sign('CLOSING_CHAN', bot, chan)
        yield sign(('CLOSING_CHAN', chan), bot)

#===============================================================================
INVITE_FILE = 'state/channel_invite.txt'

@link('INVITE')
def h_invite(bot, id, target, channel, *args):
    if target.lower() != bot.nick.lower(): return
    invited.add(channel.lower())
    bot.send_cmd('JOIN %s' % channel)

@link('AUTOJOIN')
def h_autojoin(bot):
    try:
        with open(INVITE_FILE) as file:
            file_invited = map(str.strip, file.readlines())
    except IOError:
        file_invited = []
    for chan in file_invited:
        bot.send_cmd('JOIN %s' % chan)

@link('SELF_JOIN')
def h_self_join(bot, chan):
    if chan.lower() not in invited: return
    with open(INVITE_FILE, 'a') as file:
        file.write(chan + '\n')
    if chan.lower() in invited:
        invited.remove(chan.lower())

@link('SELF_PART', 'SELF_KICKED')
def h_self_part_kicked(bot, chan, *args):
    if chan.lower() in invited:
        invited.remove(chan.lower())
    try:
        with open(INVITE_FILE) as file:
            file_invited = map(str.strip, file.readlines())
    except IOError:
        file_invited = []
    new_file_invited = filter(lambda c: c.lower() != chan.lower(), file_invited)
    if new_file_invited == file_invited: return
    with open(INVITE_FILE, 'w') as file:
        for c in new_file_invited: file.write(c + '\n')
