import re
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from ltc_registry import terminal_name


class TerminalNameTest(unittest.TestCase):
    def test_polish_titles_are_readable_and_fit_herdr(self):
        name = terminal_name('Czytelne nazwy zadań na liście Herdr')
        self.assertRegex(name, r'^czytelne-nazwy-zadan-na-[a-f0-9]{8}$')
        self.assertLessEqual(len(name), 32)

    def test_fallback_and_unique_names(self):
        name = terminal_name('🚀!!!')
        self.assertRegex(name, r'^task-[a-f0-9]{8}$')
        self.assertNotEqual(terminal_name('Task'), terminal_name('Task'))
        for title in ('A' * 100, '---', 'Zażółć gęślą jaźń', 'task_1'):
            self.assertTrue(re.fullmatch(r'[a-z0-9_-]{1,32}', terminal_name(title)))


if __name__ == '__main__':
    unittest.main()
