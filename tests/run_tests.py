"""Run all Stage Helper tests:  python tests/run_tests.py"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

if __name__ == '__main__':
    print('Python %s' % sys.version.split()[0])
    suite = unittest.defaultTestLoader.discover(HERE, pattern='test_*.py', top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
