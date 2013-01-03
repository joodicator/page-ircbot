from untwisted.magic import sign, hold
import util
import re

link, ls_install, ls_uninstall = util.LinkSet().triple()
REGISTERED = '001'
IDENTIFIED = ('AFTER', REGISTERED, __name__)

def install(bot):
    util.event_sub(bot, REGISTERED, IDENTIFIED)
    ls_install(bot)
    
def uninstall(bot):
    ls_uninstall(bot)
    util.event_sub(bot, IDENTIFIED, REGISTERED)

def conf(*args, **kwds):
    return util.fdict('conf/nickserv.py', util.__dict__).get(*args, **kwds)

@link(REGISTERED)
def registered(bot, *rargs):
    # Wait until mode +r is received before proceeding.
    while conf('password'):
        _, margs = yield hold(bot, 'MODE')
        bot, source, target, modes = margs[:4]
        if target != bot.nick: continue
        if re.search(r'\+[a-zA-Z]*r', modes): break
    yield sign(IDENTIFIED, bot, *rargs)

@link(('UNOTICE', None))
def notice(bot, id, msg):
    nickserv = conf('nickserv')
    if id.nick.lower() != nickserv.nick.lower(): return
    if (id.user, id.host) != (nickserv.user, nickserv.host):
        raise Exception('%s is %s@%s; %s@%s expected.'
            % (id.nick, id.user, id.host, nickserv.user, nickserv.host))
    yield sign('NICKSERV_NOTICE', bot, id, msg)

@link('NICKSERV_NOTICE')
def nickserv_notice(bot, id, msg):
    if msg.startswith(conf('prompt')):
        bot.send_msg(id.nick, 'IDENTIFY %s' % conf('password'))
        return
    match = re.match(r'STATUS\s+(?P<nick>\S+)\s+(?P<code>\d+)', msg)
    if match:
        nick, code = match.groups()
        yield sign(('NICKSERV_STATUS', nick), bot, id, int(code))

# Returns an object which may be yielded in an untwisted event handler to obtain
# `(('NICKSERV_STATUS', nick), [bot, id, code])`, where `code` is the result of a
# NickServ STATUS query. See NickServ's response to `HELP STATUS` for its meaning.
def status(bot, nick):
    bot.send_msg(conf('nickserv').nick, 'STATUS %s' % nick)
    return hold(bot, ('NICKSERV_STATUS', nick))

