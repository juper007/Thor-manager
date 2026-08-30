import unittest
from collections import namedtuple
from unittest import mock

import monitoring


class MonitoringTests(unittest.TestCase):
    def tearDown(self):
        if hasattr(monitoring.cpu_percent,'prev'):
            del monitoring.cpu_percent.prev

    def test_parse_extracts_tegrastats_metrics(self):
        line=('RAM 2048/8192MB CPU [10%@729,off,20%@1020] GR3D_FREQ 72% '
              'cpu@41.5C gpu@39C soc0@40C VDD_GPU 1234mW VIN_SYS_5V0 4321mW')
        with mock.patch.object(monitoring,'cpu_percent',return_value=12.5), \
             mock.patch.object(monitoring,'gpu_utilization',return_value=(9,8)):
            result=monitoring.parse(line)
        self.assertEqual(result['cpu'],12.5)
        self.assertEqual(result['memory'],{'used':2048,'total':8192,'percent':25.0})
        self.assertEqual(result['clocks'],[729,1020])
        self.assertEqual((result['gpu'],result['gpu_memory']),(72,8))
        self.assertEqual(result['temps'],{'cpu':41.5,'gpu':39.0,'soc0':40.0})
        self.assertEqual(result['power'],{'VDD_GPU':1234,'VIN_SYS_5V0':4321})

    def test_gpu_utilization_handles_valid_and_invalid_output(self):
        with mock.patch.object(monitoring.subprocess,'check_output',return_value='35, 12\n'):
            self.assertEqual(monitoring.gpu_utilization(),(35,12))
        with mock.patch.object(monitoring.subprocess,'check_output',return_value='invalid'):
            self.assertEqual(monitoring.gpu_utilization(),(0,0))

    def test_parse_handles_zero_memory_total(self):
        with mock.patch.object(monitoring,'cpu_percent',return_value=0), \
             mock.patch.object(monitoring,'gpu_utilization',return_value=(0,0)):
            result=monitoring.parse('RAM 0/0MB')
        self.assertEqual(result['memory']['percent'],0)

    def test_cpu_percent_handles_missing_and_malformed_proc_stat(self):
        with mock.patch.object(monitoring,'read_text',return_value=''):
            self.assertEqual(monitoring.cpu_percent(),0)
        with mock.patch.object(monitoring,'read_text',return_value='cpu broken data'):
            self.assertEqual(monitoring.cpu_percent(),0)

    def test_disk_net_tolerates_malformed_proc_data(self):
        usage=namedtuple('usage','total used free')(0,0,0)
        def inaccessible_proc():
            raise OSError('proc unavailable')
            yield
        def contents(path,default=''):
            return {'/proc/net/dev':'header\nheader\ninvalid\neth0: 1 2',
                    '/proc/meminfo':'MemTotal: invalid kB\n',
                    '/proc/uptime':'invalid'}.get(path,default)
        with mock.patch.object(monitoring,'read_text',side_effect=contents), \
             mock.patch.object(monitoring.shutil,'disk_usage',return_value=usage), \
             mock.patch.object(monitoring.Path,'iterdir',return_value=inaccessible_proc()), \
             mock.patch.object(monitoring.os,'getloadavg',side_effect=OSError):
            result=monitoring.disk_net()
        self.assertEqual(result['network'],{})
        self.assertEqual(result['uptime'],0)
        self.assertEqual(result['load'],[0,0,0])
        self.assertEqual(result['disk']['percent'],0)


if __name__=='__main__':
    unittest.main()
