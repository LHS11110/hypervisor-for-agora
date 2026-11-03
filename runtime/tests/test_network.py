import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('network', Path(__file__).resolve().parents[1] / 'network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)

class NetworkTests(unittest.TestCase):
    def test_names_cannot_inject_commands_or_other_interfaces(self):
        for value in ('../1', '1;reboot', '-1', '0', '01', '2147483648'):
            with self.assertRaises(ValueError): network.names(value)
        self.assertEqual(network.names('2147483647'), ('agora-runtime-2147483647', 'agv2147483647'))

    def test_transit_addresses_are_disjoint_and_bounded(self):
        values = [address for slot in range(64) for address in network.addresses(slot)]
        self.assertEqual(len(values),len(set(values)))
        self.assertEqual(network.addresses(0), ('10.204.0.1', '10.204.0.2'))
        for value in (-1,64,True):
            with self.assertRaises(ValueError): network.addresses(value)

    def test_host_rules_allow_replies_and_drop_new_inbound_without_dnat(self):
        calls=[]
        result=type('Result',(),{'returncode':0})()
        with patch.object(network,'public_https',return_value=[]), patch.object(network,'uplink',return_value='eth0'), patch.object(network,'blocked_networks',return_value=list(network.BLOCKED)), \
             patch.object(network,'ipt',return_value=result), patch.object(network,'ensure_rule') as ensure, \
             patch.object(network,'call',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            network.firewall_up()
        text=next(kwargs['input'] for args,kwargs in calls if args[0]=='iptables-restore')
        self.assertIn('-i eth0 -o agv+ -d 10.204.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',text)
        self.assertNotIn('DNAT',text)
        self.assertNotIn('-F FORWARD',text)
        self.assertIn('-A AGORA-RUNTIME -i agv+ -d 172.16.0.0/12 -j DROP',text)
        self.assertIn('-A AGORA-RUNTIME -j DROP',text)
        ensure.assert_any_call('filter','INPUT','-i','agv+','-j',network.HTTPS_IN)
        ensure.assert_any_call('filter','OUTPUT','-o','agv+','-j',network.HTTPS_OUT)

    def test_public_https_only_reaches_the_proxy_and_only_allows_reply_traffic(self):
        calls=[];result=type('Result',(),{'returncode':0})()
        with patch.object(network,'public_https',return_value=['203.0.113.10']), patch.object(network,'ingress_address',return_value='192.168.100.10'), patch.object(network,'uplink',return_value='eth0'), patch.object(network,'blocked_networks',return_value=[*network.BLOCKED,'203.0.113.10/32']), patch.object(network,'ipt',return_value=result), patch.object(network,'ensure_rule'), patch.object(network,'call',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            network.firewall_up()
        text=next(kwargs['input'] for args,kwargs in calls if args[0]=='iptables-restore')
        self.assertIn('--dport 443 -j DNAT --to-destination 192.168.100.10:443',text)
        self.assertIn('--ctorigdst 203.0.113.10 --ctorigdstport 443 --ctstate NEW,ESTABLISHED',text)
        self.assertIn('--sport 443 -m conntrack --ctorigdst 203.0.113.10 --ctorigdstport 443 --ctstate ESTABLISHED',text)
        self.assertIn('-i agv+ -d 203.0.113.10/32 -j DROP',text)
        self.assertNotIn('--dport 80 ',text)
        self.assertNotIn('-F INPUT',text)

    def test_https_exceptions_cannot_point_to_internal_addresses(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            config=Path(directory)/'network.json'
            for address in ['127.0.0.1','192.168.100.10','10.205.0.2','0.0.0.0','169.254.169.254']:
                config.write_text(__import__('json').dumps({'public_https_ipv4':[address]}))
                with patch.object(network,'CONFIG',config):
                    with self.assertRaises(ValueError):network.public_https()

if __name__=='__main__':unittest.main()
