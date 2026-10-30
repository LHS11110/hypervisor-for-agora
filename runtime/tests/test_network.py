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
        with patch.object(network,'uplink',return_value='eth0'), patch.object(network,'blocked_networks',return_value=list(network.BLOCKED)), \
             patch.object(network,'ipt',return_value=result), patch.object(network,'ensure_rule') as ensure, \
             patch.object(network,'call',side_effect=lambda *args,**kwargs:calls.append((args,kwargs))):
            network.firewall_up()
        text=next(kwargs['input'] for args,kwargs in calls if args[0]=='iptables-restore')
        self.assertIn('-i eth0 -o agv+ -d 10.204.0.0/24 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT',text)
        self.assertNotIn('DNAT',text)
        self.assertNotIn('-F FORWARD',text)
        self.assertIn('-A AGORA-RUNTIME -i agv+ -d 172.16.0.0/12 -j DROP',text)
        self.assertIn('-A AGORA-RUNTIME -j DROP',text)
        ensure.assert_any_call('filter','INPUT','-i','agv+','-j','DROP')
        ensure.assert_any_call('filter','OUTPUT','-o','agv+','-j','DROP')

if __name__=='__main__':unittest.main()
