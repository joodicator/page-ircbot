#==============================================================================#
# tell.py - provides !tell, and related commands, to allow users to leave each
# other messages in an IRC channel.

#==============================================================================#
# Possible extensions:
#
# - Allow multiple senders to be specified for !dismiss and !undismiss.
#
# - Allow "private" messages: if user A is in channel #C and tells the bot by,
#   PM "!tell #C B MSG", user B will be delivered MSG by PM next time they are
#   in channel C, provided that user A is also in channel #C.
#
# - Place some limit on the number of messages a single user can leave, possibly
#   also per recipient (if it's possible to identify "recipients"...)

import util
import auth
from util import LinkSet
from auth import admin
from message import reply
import channel
import untwisted.magic

from collections import namedtuple
from copy import deepcopy
from itertools import *
import pickle as pickle
import os.path
import datetime
import time
import re

#==============================================================================#
link, install, uninstall = LinkSet().triple()

# Memory-cached plugin state.
current_state = None


# File where the plugin state is stored.
STATE_FILE = 'state/tell.pickle'

# After this many days, dismissed messages may be deleted.
DISMISS_DAYS = 30

# Date format used by !tell? and !tell+.
DATE_FORMAT_SHORT = '%Y-%m-%d %H:%M'

# Maximum number of history states to remember.
HISTORY_SIZE = 8 


# A saved message kept by the system.
Message = namedtuple('Message',
    ('time_sent', 'channel', 'from_id', 'to_nick', 'message'))
Message.__getstate__ = lambda *a, **k: None

# The plugin's persistent state object.
class State(object):
    def __new__(clas):
        inst = object.__new__(clas)
        inst.init()
        return inst

    def init(self):
        self.msgs = []
        self.dismissed_msgs = []
        self.prev_state = None
        self.next_state = None        

#==============================================================================#
# Retrieve a copy of the plugin's state.
def get_state():
    return deepcopy(load_state())

# Commit a forward change to the plugin's state.
def put_state(state):
    current_state.next_state = state
    state.prev_state = current_state
    state.next_state = None

    # Prune undo history based on HISTORY_SIZE.
    old_state = state
    for count in range(HISTORY_SIZE):
        if old_state.prev_state is None: break
        old_state = old_state.prev_state
    else:
        old_state.prev_state = None

    set_state(state)

# Retrieve the plugin's state.
def load_state():
    global current_state
    if not current_state and os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as state_file:
                current_state = pickle.load(state_file)
        except pickle.UnpicklingError: pass
        except EOFError: pass    
    if not current_state:
        current_state = State()
    return current_state

# Change to the given state without any processing of metadata.
def set_state(state):
    global current_state
    with open(STATE_FILE, 'w') as state_file:
        pickler = pickle.Pickler(state_file)
        pickler.clear_memo()
        pickler.dump(state)
    current_state = state    

class HistoryEmpty(Exception): pass

# Restores the state which existed before the last call to put_state().
# Raises HistoryEmpty if no such state exists.
def undo_state():
    state = load_state().prev_state
    if state is None: raise HistoryEmpty
    set_state(state)

# Restores the state which existed before the last call to undo_state().
# Raises HistoryEmpty if no such state exists.
def redo_state():
    state = load_state().next_state
    if state is None: raise HistoryEmpty
    set_state(state)

#==============================================================================#
@link('HELP')
def h_help_tell_short(bot, reply, args):
    reply('tell NICK [...] MESSAGE',
    'When NICK is next seen in this channel, MESSAGE will be delivered to them.')

@link(('HELP', 'tell'))
def h_help_tell(bot, reply, args):
    reply('tell NICK MESSAGE')
    reply('tell NICK[, NICK[, ...]]: MESSAGE')
    reply('',
    'Leaves a message for the given NICK, or for each of the listed NICKs,'
    ' so that it will be delivered to them when next seen in this channel.',
    'If NICK contains any occurrence of ! or @, it will be matched against'
    ' the full NICK!USER@HOST of the recipient, instead of just their nick;'
    ' if NICK contains the wildcard characters * or ?, these will match any'
    ' sequence of 0 or more characters, or exactly 1 character, respectively.')

@link('!tell')
def h_tell(bot, id, target, args, full_msg):
    # Secretly, admins may prepend the arguments with the target channel.
    match = re.match(r'(#\S+)\s+(.*)', args)
    if match:
        is_admin = yield auth.check(bot, id)
        if is_admin: channel, args = match.groups()
    elif target:
        channel = target
    else:
        reply(bot, id, target,
            'Error: the "tell" command may only be used in a channel.')
        return

    match = re.match(r'(%(nick)s(?:(?:\s*,\s*%(nick)s)*\s*:)?)\s+(.*)'
        % {'nick': r'[^\s,]*[^\s,:]'}, args)
    if not match:
        reply(bot, id, target,
            'Error: invalid syntax. See "help tell" for correct usage.')
        return

    to, msg = match.groups()
    to_nicks = [nick.strip() for nick in to.strip(':').split(',')]
    state = get_state()
    for to_nick in to_nicks:
        if re.search(r'[#]', to_nick):
            reply(bot, id, target,
                'Error: "%s" is not a valid IRC nick or hostmask'
                ' (no messages sent).' % to_nick)
            return

        record = Message(
            time_sent   = datetime.datetime.utcnow(),
            channel     = channel,
            from_id     = id,
            to_nick     = to_nick,
            message     = msg)
        state.msgs.append(record)

    put_state(state)
    count = len(to_nicks)
    reply(bot, id, target, 'It shall be done%s.'
        % (' (message sent to %s recipients)' % count if count > 1 else ''))

#==============================================================================#
@link('HELP')
def h_help_untell_short(bot, reply, args):
    reply('untell NICK [...]',
    'Cancels messages left using "tell".')

@link(('HELP', 'untell'))
def h_help_untell(bot, reply, args):
    reply('untell NICK[, NICK[, ...]]',
    'Cancels all undelivered messages sent using the "tell" command to any of'
    ' the listed NICKs, by any user with your hostmask.')

@link('!untell')
def h_untell(bot, id, target, args, full_msg):
    # Secretly, admins may prepend the arguments with the target channel.
    match = re.match(r'(#\S+)\s+(.*)', args)
    if match:
        is_admin = yield auth.check(bot, id)
        if is_admin: channel, args = match.groups()
    elif target:
        channel = target
    else:
        reply(bot, id, target,
            'Error: the "untell" command may only be used in a channel.')
        return

    if not args:
        reply(bot, id, target,
            'Error: you must specify at least one recipient.'
            ' See "help untell" for correct usage.')
        return

    def will_cancel(msg, to_nick):
        if msg.channel.lower() != channel.lower(): return False
        if msg.to_nick != to_nick: return False
        if msg.from_id != id: return False
        return True

    count = dict()
    state = get_state()
    for to_nick in [n.strip() for n in args.split(',')]:
        msgs = [(will_cancel(m, to_nick), m) for m in state.msgs]
        msgs_cancel = [m for (b, m) in msgs if b]
        msgs_keep = [m for (b, m) in msgs if not b]
        count[to_nick] = len(msgs_cancel)
        if len(msgs_cancel): state.msgs = msgs_keep

    total = sum(count.itervalues())
    msg = '%s message%s deleted.' % (total, 's' if total != 1 else '')

    empty = ['"%s"' % nick for (nick, count) in count.iteritems() if not count]
    if empty:
        list = ', '.join(empty[:-2] + [' or '.join(empty[-2:])])
        msg += (' There were no messages to %s, from %s in %s.'
            % (list, '%s!%s@%s' % tuple(id), channel))

    put_state(state)
    reply(bot, id, target, msg)

#==============================================================================#
@link('HELP')
def h_help_dismiss_short(bot, reply, args):
    reply('dismiss [NICK]',
    'Cancels delivery of the last message left for you.')

@link(('HELP', 'dismiss'))
def h_help_dismiss(bot, reply, args):
    reply('dismiss [NICK]',
    'If NICK is given, dismisses the most recent message left for you by NICK,'
    ' preventing it from being delivered; otherwise, dismisses the most recent'
    ' message left by anybody. Messages may be recovered using "undismiss".',
    'NICK may be an IRC nick or a NICK!USER@HOST, and may contain the wildcard'
    ' characters * and ?, as specified in "help tell", in which case the last'
    ' matching message is dismissed.')

@link('!dismiss')
def h_dismiss(bot, id, chan, query, *args):
    if chan is None: return reply(bot, id, chan,
        'Error: the "dismiss" command may only be used in a channel.')

    state = get_state()
    msgs = [m for m in state.msgs
            if m.channel.lower() == chan.lower()
            and (not query or match_id(query, m.from_id))]

    msgs = [m for m in state.msgs if would_deliver(id, chan, m)
            and (not query or match_id(query, m.from_id))]
    if not msgs: return reply(bot, id, chan,
        'You have no messages%s to dismiss.' % (query and ' from "%s"' % query))

    msg = msgs[-1]
    state.msgs.remove(msg)
    state.dismissed_msgs = [m for m in state.dismissed_msgs
        if (datetime.datetime.utcnow() - m.time_sent).days <= DISMISS_DAYS]
    state.dismissed_msgs.append(msg)

    count = len([m for m in state.msgs if would_deliver(id, chan, m)])
    msg = ('1 message from %s deleted; you now have %s message%s'
       ' (you may reverse this using "undismiss").'
       % (msg.from_id.nick, count, 's' if count != 1 else ''))

    put_state(state)
    reply(bot, id, chan, msg)

#==============================================================================#
@link('HELP')
def h_help_undismiss_short(bot, reply, args):
    reply('undismiss [NICK]',
    'Restores the last message that you dismissed.')

@link(('HELP', 'undismiss'))
def h_help_undismiss(bot, reply, args):
    reply('undismiss [NICK]',
    'Reverses the effect of "dismiss", restoring the last dismissed message'
    ' from NICK, or from anybody if NICK is not specified. This may be done'
    ' multiple times to restore messages from up to %s days ago.'
    % DISMISS_DAYS,
    'As with "dismiss", NICK may take the form NICK!USER@HOST, and may contain'
    ' the wildcard characters * and ?.')

@link('!undismiss')
def h_undismiss(bot, id, chan, query, *args):
    if chan == None: return reply(bot, id, chan,
        'Error: the "undismiss" command may only be used in a channel.')

    state = get_state()
    msgs = [m for m in state.dismissed_msgs if would_deliver(id, chan, m)
            and (not query or match_id(query, m.from_id))]
    if not msgs: return reply(bot, id, chan,
        'You have no dismissed messages%s.'
        % (query and ' from "%s"' % query))
    msg = msgs[-1]
    state.dismissed_msgs.remove(msg)
    state.msgs.append(msg)

    count = len([m for m in state.msgs if would_deliver(id, chan, m)])
    msg = ('1 message from %s restored; you now have %s message%s'
        ' (say anything to read %s).'
        % (msg.from_id.nick, count, 's' if count != 1 else '',
        'them' if count != 1 else 'it'))

    put_state(state)
    reply(bot, id, chan, msg)

#==============================================================================#
@link('!tell?')
@admin
def h_tell_list(bot, id, target, args, full_msg):
    output = lambda msg: reply(bot, id, target, msg, prefix=False)
    state = get_state()
    lines = [('#', 'From', 'To', 'Channel', 'Time', 'Message')]
    for (num, msg) in izip(count(1), state.msgs):
        lines.append((
            str(num),
            '%s!%s@%s' % tuple(msg.from_id),
            msg.to_nick,
            msg.channel,
            msg.time_sent.strftime(DATE_FORMAT_SHORT),
            msg.message))
    lines = util.align_table(lines)
    output('\2' + lines[0])
    map(output, lines[1:])
    output('\2End of List')

#==============================================================================#
@link('!tell+')
@admin
def h_tell_add(bot, id, target, args, full_msg):
    args = [a.strip() for a in args.split(',', 4)]
    if len(args) != 5: return reply(bot, id, target,
        'Error: expected: FROM_ID, TO_NICK, CHAN, %s, MESSAGE...'
         % DATE_FORMAT_SHORT)

    [from_id, to_nick, channel, time_sent, message] = args
    try:
        from_id = util.ID(*re.match(r'(.*?)!(.*?)@(.*)$', from_id).groups())
        time_sent = datetime.datetime.strptime(time_sent, DATE_FORMAT_SHORT)
    except Exception as e: return reply(bot, id, target, repr(e))

    msg = Message(from_id=from_id, to_nick=to_nick, channel=channel,
                  time_sent=time_sent, message=message)
    state = get_state()
    state.msgs.append(msg)
    state.msgs.sort(key=lambda m: m.time_sent)
    put_state(state)
    reply(bot, id, target, 'Done.')

#==============================================================================#
@link('!tell-')
@admin
def h_tell_remove(bot, id, target, args, full_msg):
    state = get_state()
    remove_msgs = []
    try:
        for match in re.finditer(r'\S+', args):
            index = int(match.group()) - 1
            remove_msgs.append(state.msgs[index])
        for msg in remove_msgs:
            state.msgs.remove(msg)
    except Exception as e:
        return reply(bot, id, target, repr(e))
    put_state(state)
    reply(bot, id, target, 'Done.')

#==============================================================================#
@link('!tell-clear')
@admin
def h_tell_clear(bot, id, target, args, full_msg):
    put_state(State())
    reply(bot, id, target, 'Done.')

#==============================================================================#
@link('!tell-undo')
@admin
def h_tell_undo(bot, id, target, args, full_msg):
    try:
        undo_state()
    except HistoryEmpty:
        reply(bot, id, target, 'Error: no undo state is available.')
    else:
        reply(bot, id, target, 'Done.')

#==============================================================================#
@link('!tell-redo')
@admin
def h_tell_undo(bot, id, target, args, full_msg):
    try:
        redo_state()
    except HistoryEmpty:
        reply(bot, id, target, 'Error: no redo state is available.')
    else:
        reply(bot, id, target, 'Done.')

#==============================================================================#
@link('OTHER_JOIN')
def h_other_join(bot, id, chan):
    notify_msgs(bot, id, chan)

@link('MESSAGE')
def h_message(bot, id, target, msg):
    if target: deliver_msgs(bot, id, target)

@link('OTHER_NICK_CHAN')
def h_nick(bot, id, new_nick, chan):
    state = get_state()
    old_id = util.ID(*id)
    new_id = util.ID(new_nick, old_id.user, old_id.host)
    new_msgs = {m for m in state.msgs
                if would_deliver(new_id, chan, m)
                and not would_deliver(old_id, chan, m)}
    if new_msgs: notify_msgs(bot, new_id, chan)

#==============================================================================#
# Notify `id' of messages left for them in `chan', if any.
def notify_msgs(bot, id, chan):
    state = get_state()
    msgs = filter(lambda m: would_deliver(id, chan, m), state.msgs)
    if len(msgs) > 1:
        reply(bot, id, chan,
            'You have %s messages; say anything to read them.' % len(msgs))
    elif len(msgs):
        reply(bot, id, chan,
            'You have a message; say anything to read it.')    

#==============================================================================#
# Deliver to `id' any messages left for them in `chan'.
def deliver_msgs(bot, id, chan):
    state = get_state()
    msgs = [(would_deliver(id, chan, m), m) for m in state.msgs]
    msgs_deliver = [m for (b, m) in msgs if b]
    msgs_keep = [m for (b, m) in msgs if not b]
    if not msgs_deliver: return
    for msg in msgs_deliver:
        deliver_msg(bot, id, chan, msg)
    state.msgs = msgs_keep
    put_state(state)

#==============================================================================#
# Unconditionally deliver `msg' to `id' in `chan', or by PM if `chan' is None.
def deliver_msg(bot, id, chan, msg):
    delta = datetime.datetime.utcnow() - msg.time_sent
    if delta.total_seconds() < 1: return False
    d_mins, d_secs = divmod(delta.seconds, 60)
    d_hours, d_mins = divmod(d_mins, 60)

    bot.send_msg(chan, '%s: %s said on %s UTC (%s ago):' % (
        id.nick,
        '%s!%s@%s' % tuple(msg.from_id),
        msg.time_sent.strftime('%d %b %Y, %H:%M'),
        '%sd, %02d:%02d:%02d' % (delta.days, d_hours, d_mins, d_secs)))
    bot.send_msg(chan, "<%s> %s" % (msg.from_id.nick, msg.message))

    return True

#==============================================================================#
# Returns True if `msg' would be delivered at this time to `id' in `chan',
# or otherwise returns False.
def would_deliver(id, chan, msg):
    if msg.channel.lower() != chan.lower(): return False
    if not match_id(msg.to_nick, id): return False
    delta = datetime.datetime.utcnow() - msg.time_sent
    if delta.total_seconds() < 1: return False    
    return True

#==============================================================================#
# Returns True if `query', which is is a wildcard expression matching either a
# nick or a nick!user@host, matches the given id.
def match_id(query, id):
    id_str = '%s!%s@%s' % tuple(id) if re.search(r'!|@', query) else id.nick
    return re.match(wc_to_re(query), id_str, re.I) is not None

#==============================================================================#
# Returns a Python regular expression pattern string equivalent to the given
# wildcard pattern (which accepts only the entire input, not part of it).
def wc_to_re(wc):
    def sub(match):
        if match.group(1): return '.*'
        elif match.group(2): return '.'
        else: return re.escape(match.group(3))
    return '^' + re.sub(r'(\*)|(\?)|([^*?]+)', sub, wc) + '$'
