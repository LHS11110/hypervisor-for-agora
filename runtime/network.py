#!/usr/bin/env python3
"""Root-only, per-canvas namespaces and outbound-only IPv4 NAT.

The VMM remains unprivileged. Only this short-lived service configures networks.
Never flush host firewall tables: all changes use owned chains and scoped jumps.
"""
import fcntl
import ipaddress
import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path

RUN = Path('/run/agora-runtime-network')
CONFIG = Path('/etc/agora-runtime-network.json')
TRANSIT = ipaddress.ip_network('10.204.0.0/24')
GUEST = '10.205.0.0/30'
BLOCKED = ('0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
           '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
           '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24', '203.0.113.0/24',
           '224.0.0.0/4', '240.0.0.0/4')
CHAIN = 'AGORA-RUNTIME'
NAT = 'AGORA-RUNTIME-NAT'
HTTPS_IN='AGORA-HTTPS-IN'
HTTPS_OUT='AGORA-HTTPS-OUT'
HTTPS_NAT='AGORA-HTTPS-NAT'
HTTPS_NS='AGORA-HTTPS'


def call(*args, check=True, input=None):
    return subprocess.run(args, check=check, capture_output=True, text=True, input=input, timeout=20)


def ipt(*args, check=True):
    return call('iptables', '-w', '5', *args, check=check)


def ensure_rule(table, chain, *rule):
    if ipt('-t', table, '-C', chain, *rule, check=False).returncode:
        ipt('-t', table, '-I', chain, '1', *rule)


def uplink():
    routes = json.loads(call('ip', '-j', '-4', 'route', 'show', 'default').stdout)
    if not routes:
        raise RuntimeError('No IPv4 default route')
    interface = min(routes, key=lambda route: route.get('metric', 0))['dev']
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', interface):
        raise ValueError('Invalid uplink')
    return interface


def blocked_networks():
    extra = json.loads(CONFIG.read_text()).get('blocked_ipv4', []) if CONFIG.exists() else []
    return [*BLOCKED, *(str(ipaddress.IPv4Network(value, strict=False)) for value in extra)]


def public_https():
    """Explicit public proxy exceptions, never arbitrary internal destinations."""
    values=json.loads(CONFIG.read_text()).get('public_https_ipv4',[]) if CONFIG.exists() else []
    if not isinstance(values,list) or len(values)>8:raise ValueError('Invalid public HTTPS addresses')
    result=[]
    for value in values:
        address=ipaddress.IPv4Address(value)
        if not address.is_global:raise ValueError('HTTPS exception must be a public IPv4 address')
        if str(address) not in result:result.append(str(address))
    return result


def ingress_address(interface):
    links=json.loads(call('ip','-j','-4','address','show','dev',interface,'scope','global').stdout)
    values=[entry['local'] for link in links for entry in link.get('addr_info',[]) if entry.get('family')=='inet']
    if not values:raise RuntimeError('No HTTPS ingress address')
    return str(ipaddress.IPv4Address(values[0]))


def namespace_https(namespace):
    def ns(*args,check=True):return call('ip','netns','exec',namespace,*args,check=check)
    if ns('iptables','-w','5','-n','-L',HTTPS_NS,check=False).returncode:
        ns('iptables','-w','5','-N',HTTPS_NS)
    rules=['*filter',f'-F {HTTPS_NS}']
    for address in public_https():
        rules.append(f'-A {HTTPS_NS} -i tap0 -o wan0 -s 10.205.0.2/32 -d {address}/32 -p tcp --dport 443 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT')
    rules+=['COMMIT']
    call('ip','netns','exec',namespace,'iptables-restore','--wait','5','--noflush',input='\n'.join(rules)+'\n')
    if ns('iptables','-w','5','-C','FORWARD','-j',HTTPS_NS,check=False).returncode:
        ns('iptables','-w','5','-I','FORWARD','1','-j',HTTPS_NS)


def firewall_up():
    interface = uplink()
    endpoints=public_https()
    ingress=ingress_address(interface) if endpoints else None
    # Dedicated owned chains are refreshed atomically; other host rules are untouched.
    for table, chain in (('filter', CHAIN), ('nat', NAT), ('filter',HTTPS_IN),('filter',HTTPS_OUT),('nat',HTTPS_NAT)):
        if ipt('-t', table, '-n', '-L', chain, check=False).returncode:
            ipt('-t', table, '-N', chain)
    rules=['*filter',f'-F {HTTPS_IN}',f'-F {HTTPS_OUT}']
    for address in endpoints:
        original=f'-m conntrack --ctorigdst {address} --ctorigdstport 443'
        rules += [f'-A {HTTPS_IN} -i agv+ -s {TRANSIT} -d {ingress}/32 -p tcp --dport 443 {original} --ctstate NEW,ESTABLISHED -j ACCEPT',
                  f'-A {HTTPS_OUT} -o agv+ -d {TRANSIT} -s {ingress}/32 -p tcp --sport 443 {original} --ctstate ESTABLISHED -j ACCEPT']
    rules += [f'-A {HTTPS_IN} -j DROP',f'-A {HTTPS_OUT} -j DROP',f'-F {CHAIN}', f'-A {CHAIN} -m conntrack --ctstate INVALID -j DROP',
             f'-A {CHAIN} -i agv+ ! -s {TRANSIT} -j DROP']
    for network in blocked_networks():
        rules.append(f'-A {CHAIN} -i agv+ -d {network} -j DROP')
    rules += [f'-A {CHAIN} -i agv+ -o {interface} -s {TRANSIT} -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT',
              f'-A {CHAIN} -i {interface} -o agv+ -d {TRANSIT} -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',
              f'-A {CHAIN} -j DROP', 'COMMIT', '*nat', f'-F {NAT}',
              f'-A {NAT} -s {TRANSIT} -o {interface} -j MASQUERADE',f'-F {HTTPS_NAT}']
    for address in endpoints:
        rules.append(f'-A {HTTPS_NAT} -i agv+ -s {TRANSIT} -d {address}/32 -p tcp --dport 443 -j DNAT --to-destination {ingress}:443')
    rules+=['COMMIT']
    call('iptables-restore', '--wait', '5', '--noflush', input='\n'.join(rules)+'\n')
    ensure_rule('filter', 'INPUT', '-i', 'agv+', '-j', HTTPS_IN)
    ipt('-D','INPUT','-i','agv+','-j','DROP',check=False)
    ensure_rule('filter', 'OUTPUT', '-o', 'agv+', '-j', HTTPS_OUT)
    ipt('-D','OUTPUT','-o','agv+','-j','DROP',check=False)
    ensure_rule('nat','PREROUTING','-i','agv+','-j',HTTPS_NAT)
    ensure_rule('filter', 'FORWARD', '-i', 'agv+', '-j', CHAIN)
    ensure_rule('filter', 'FORWARD', '-o', 'agv+', '-j', CHAIN)
    ensure_rule('nat', 'POSTROUTING', '-s', str(TRANSIT), '-j', NAT)
    call('sysctl', '-q', '-w', 'net.ipv4.ip_forward=1')


def addresses(slot):
    if type(slot) is not int or not 0 <= slot < 64:
        raise ValueError('Invalid network slot')
    base = int(TRANSIT.network_address) + slot * 4
    return str(ipaddress.IPv4Address(base+1)), str(ipaddress.IPv4Address(base+2))


def names(canvas):
    if not canvas.isdecimal() or not 0 < int(canvas) <= 2147483647 or str(int(canvas)) != canvas:
        raise ValueError('Invalid canvas')
    return 'agora-runtime-'+canvas, 'agv'+canvas


def down(canvas, state):
    namespace, host = names(canvas)
    call('ip', 'link', 'del', host, check=False)
    call('ip', 'netns', 'del', namespace, check=False)
    state.pop(canvas, None)


def up(canvas, state):
    namespace, host = names(canvas)
    firewall_up()
    if canvas in state and Path('/run/netns', namespace).exists():
        namespace_https(namespace)
        return
    down(canvas, state)
    used = set(state.values())
    slot = next((slot for slot in range(64) if slot not in used), None)
    if slot is None:
        raise RuntimeError('No free network slot')
    host_ip, wan_ip = addresses(slot)
    try:
        call('ip', 'netns', 'add', namespace)
        call('ip', 'link', 'add', host, 'type', 'veth', 'peer', 'name', 'wan0', 'netns', namespace)
        call('ip', 'address', 'add', host_ip+'/30', 'dev', host)
        call('ip', 'link', 'set', host, 'up')
        def ns(*args): return call('ip', 'netns', 'exec', namespace, *args)
        ns('sysctl', '-q', '-w', 'net.ipv4.ip_forward=1', 'net.ipv6.conf.all.disable_ipv6=1', 'net.ipv6.conf.default.disable_ipv6=1')
        ns('ip', 'link', 'set', 'lo', 'up')
        ns('ip', 'address', 'add', wan_ip+'/30', 'dev', 'wan0')
        ns('ip', 'link', 'set', 'wan0', 'up')
        ns('ip', 'route', 'add', 'default', 'via', host_ip)
        ns('ip', 'tuntap', 'add', 'dev', 'tap0', 'mode', 'tap', 'user', str(pwd.getpwnam('agora-vmm').pw_uid), 'vnet_hdr')
        ns('ip', 'address', 'add', '10.205.0.1/30', 'dev', 'tap0')
        ns('ip', 'link', 'set', 'tap0', 'up')
        # Defence in depth: unsolicited traffic is dropped even if host firewall
        # rules change. No guest inbound mappings or private-service access; only
        # explicitly configured public proxy HTTPS may reach the host gateway.
        rules = ['*filter', ':INPUT DROP [0:0]', ':FORWARD DROP [0:0]', ':OUTPUT ACCEPT [0:0]',
                 '-A INPUT -i lo -j ACCEPT', '-A FORWARD -m conntrack --ctstate INVALID -j DROP',
                 '-A FORWARD -i tap0 ! -s 10.205.0.2/32 -j DROP']
        for network in blocked_networks():
            rules.append(f'-A FORWARD -i tap0 -d {network} -j DROP')
        rules += ['-A FORWARD -i tap0 -o wan0 -s 10.205.0.2/32 -m conntrack --ctstate NEW,ESTABLISHED,RELATED -j ACCEPT',
                  '-A FORWARD -i wan0 -o tap0 -d 10.205.0.2/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',
                  'COMMIT', '*nat', ':POSTROUTING ACCEPT [0:0]',
                  '-A POSTROUTING -s 10.205.0.2/32 -o wan0 -j MASQUERADE', 'COMMIT']
        call('ip', 'netns', 'exec', namespace, 'iptables-restore', input='\n'.join(rules)+'\n')
        namespace_https(namespace)
        state[canvas] = slot
    except BaseException:
        down(canvas, state)
        raise


def main():
    if os.geteuid() != 0: raise SystemExit('Run as root')
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (RUN/'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        action = sys.argv[1]
        if action == 'firewall-up':
            firewall_up(); return
        path = RUN/'slots.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        try:
            if action == 'up': up(sys.argv[2], state)
            elif action == 'down': down(sys.argv[2], state)
            else: raise ValueError('Unknown action')
        finally:
            temporary = RUN/'slots.next'
            temporary.write_text(json.dumps(state))
            temporary.chmod(0o600)
            temporary.replace(path)


if __name__ == '__main__': main()
